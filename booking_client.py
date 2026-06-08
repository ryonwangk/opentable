# booking_client.py
# 创建日期: 2026-06-08 09:30:00（北京时间 UTC+8）
# 更新日期: 2026-06-08 09:30:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: OpenTable 预约完整协议客户端，支持 slotLock -> makeReservation -> cancel

"""
OpenTable 预约协议模块

完整预约链路（3步）:
  Step 1: GET /booking/details?rid=&datetime=&covers=&slotHash=&slotAvailabilityToken=
          -> 解析 __INITIAL_STATE__ 获取 CC 策略、diningAreaId、savedCards

  Step 2: POST /dapi/fe/gql?optype=mutation&opname=BookDetailsStandardSlotLock
          -> 锁定 slot，获得 slotLockId（有效期约90秒）

  Step 3: POST /dapi/booking/make-reservation
          -> 提交预约，返回 confirmationNumber
"""

import json
import re
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import config
from session_manager import OTProfile
from logger_setup import debug
from graphql_client import GQLClient

logger = debug()

HASH_AVAILABILITY = "cbcf4838a9b399f742e3741785df64560a826d8d3cc2828aa01ab09a8455e29e"
HASH_SLOT_LOCK = "1100bf68905fd7cb1d4fd0f4504a4954aa28ec45fb22913fa977af8b06fd97fa"
HASH_CANCEL = "4ee53a006030f602bdeb1d751fa90ddc4240d9e17d015fb7976f8efcb80a026e"

URL_GQL = "https://www.opentable.com/dapi/fe/gql"
URL_BOOKING_DETAILS = "https://www.opentable.com/booking/details"
URL_MAKE_RESERVATION = "https://www.opentable.com/dapi/booking/make-reservation"

@dataclass
class SavedCard:
    card_id: str
    brand: str
    last4: str
    expiry_month: int
    expiry_year: int
    is_default: bool = False

    def expiry_mm_yy(self) -> str:
        return f"{self.expiry_month:02d}{self.expiry_year % 100:02d}"


def generate_fake_card() -> SavedCard:
    import random
    prefix = "4532"
    partial = prefix + ''.join([str(random.randint(0, 9)) for _ in range(11)])
    digits = [int(c) for c in partial]
    for d in range(10):
        check_digits = digits + [d]
        total = 0
        for i, val in enumerate(reversed(check_digits)):
            v = val * 2 if i % 2 == 1 else val
            total += v if v < 10 else v - 9
        if total % 10 == 0:
            card_number = partial + str(d)
            break
    return SavedCard(
        card_id=f"DMf{random.randint(10**25, 10**26-1)}T",
        brand="Visa",
        last4=card_number[-4:],
        expiry_month=random.randint(1, 12),
        expiry_year=random.randint(2027, 2030),
        is_default=True,
    )


@dataclass
class CancellationPolicy:
    policy_type: str
    description: str
    amount_usd: float | None
    free_cancel_days: int | None


@dataclass
class BookingDetails:
    cc_required: bool
    policy: CancellationPolicy
    default_card: SavedCard | None
    dining_areas: list[dict]
    wallet_cards: list[SavedCard]
    upcoming_conflicts: list[dict]
    terms: str | None
    experience: dict | None


@dataclass
class UserProfile:
    first_name: str
    last_name: str
    email: str
    mobile_phone_number: str
    country_id: str = "US"


@dataclass
class BookingResult:
    success: bool
    confirmation_number: int | None = None
    security_token: str | None = None
    points: int = 0
    error_code: str | None = None
    error_message: str | None = None
    raw: dict | None = None


@dataclass
class SlotLockResult:
    success: bool
    slot_lock_id: int | None = None
    error_message: str | None = None
    raw: dict | None = None


def extract_initial_state(html: str) -> dict | None:
    idx = html.find('__INITIAL_STATE__')
    if idx == -1:
        return None
    colon = html.find(':', idx)
    if colon == -1:
        return None
    json_start = colon + 1
    while json_start < len(html) and html[json_start] in ' \t\n\r':
        json_start += 1
    if json_start >= len(html) or html[json_start] != '{':
        return None
    depth = 0
    json_end = json_start
    for i in range(json_start, len(html)):
        c = html[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break
        elif depth == 0 and c in '}\0':
            break
    json_str = html[json_start:json_end]
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _normalise_policy_type(t: str | None) -> str:
    if not t:
        return "none"
    s = t.lower()
    if s == "hold":
        return "hold"
    if s == "deposit":
        return "deposit"
    return "none"


def _extract_fee_from_text(msg: str) -> tuple:
    if not msg:
        return None, False
    money = re.search(r'\$ ?(\d+(?:\.\d{1,2})?)', msg)
    amount = float(money.group(1)) if money else None
    per_person = bool(re.search(r'per (person|guest|diner)', msg, re.I))
    return amount, per_person


def parse_booking_details_state(state: dict) -> BookingDetails:
    root = state.get("state", state)
    dining_areas = []
    for da in (root.get("restaurant", {}).get("diningAreas") or []):
        da_id = da.get("diningAreaId")
        if da_id:
            dining_areas.append({
                "dining_area_id": da_id,
                "table_category": da.get("tag", "") or da.get("name", ""),
                "name": da.get("name", ""),
            })
    root_da_id = root.get("diningAreaId")
    if root_da_id and not any(d["dining_area_id"] == root_da_id for d in dining_areas):
        dining_areas.insert(0, {"dining_area_id": root_da_id, "table_category": "default", "name": "Default"})
    ts = root.get("timeSlot", {})
    cc_required = bool(ts.get("creditCardRequired"))
    policy_type = _normalise_policy_type(ts.get("creditCardPolicyType"))
    messages = root.get("messages", {})
    raw_msg = (
        messages.get("cancellationPolicyMessage", {})
        .get("cancellationMessage", {})
        .get("message", "") or
        (messages.get("creditCardDayMessage", [{}])[0] or {}).get("message", "") or
        messages.get("cancellationPolicyMessage", {}).get("message", "") or ""
    )
    amount, per_person = _extract_fee_from_text(raw_msg)
    features = root.get("restaurant", {}).get("features", {}) or {}
    free_cancel_days = features.get("creditCardCancellationDayLimit")
    policy = CancellationPolicy(
        policy_type=policy_type,
        description=raw_msg or "No cancellation policy",
        amount_usd=amount,
        free_cancel_days=free_cancel_days,
    )
    wallet = root.get("wallet", {})
    raw_cards = wallet.get("savedCards", [])
    cards = []
    default_card = None
    selected_id = wallet.get("selectedPaymentCardId")
    for c in raw_cards:
        if not c.get("cardId") or not c.get("last4"):
            continue
        card = SavedCard(
            card_id=c["cardId"],
            brand=c.get("type", "Unknown"),
            last4=c["last4"],
            expiry_month=c.get("expiryMonth") or 0,
            expiry_year=c.get("expiryYear") or 0,
            is_default=bool(c.get("default")) or (c["cardId"] == selected_id),
        )
        cards.append(card)
        if card.is_default or not default_card:
            default_card = card
    conflicts = []
    for c in (root.get("upcomingReservationConflicts") or []):
        if c.get("dateTime") and c.get("confirmationNumber"):
            conflicts.append({"date_time": c["dateTime"], "confirmation_number": c["confirmationNumber"], "party_size": c.get("partySize", 0)})
    tc = messages.get("termsAndConditions") or {}
    terms = tc.get("message") if tc.get("message") else None
    return BookingDetails(
        cc_required=cc_required,
        policy=policy,
        default_card=default_card,
        dining_areas=dining_areas,
        wallet_cards=cards,
        upcoming_conflicts=conflicts,
        terms=terms,
        experience=None,
    )


def resolve_dining_area_id(dining_areas: list[dict], seating: str = "default") -> int | None:
    preferred_keywords = ["dining room", "main", "terrace", "patio", "outdoor"]
    preferred_area = None
    for da in dining_areas:
        cat = da.get("table_category", "")
        name = da.get("name", "").lower()
        combined = (cat + " " + name).lower()
        if name in ("other", "general", "") or cat in ("other",):
            continue
        if cat == seating or (seating == "default" and cat in ("", "default", "Default")):
            for kw in preferred_keywords:
                if kw in combined and preferred_area is None:
                    preferred_area = da["dining_area_id"]
                    break
            if preferred_area is None:
                preferred_area = da["dining_area_id"]
    if preferred_area:
        return preferred_area
    if dining_areas:
        return dining_areas[0]["dining_area_id"]
    return None


def parse_user_profile(state: dict) -> UserProfile | None:
    root = state.get("state", state)
    profile_data = None
    up = root.get("userProfile", {}) or {}
    if up:
        profile_data = up
    if not profile_data:
        user = root.get("user", {}) or {}
        profile_data = user
    if not profile_data:
        return None
    first_name = profile_data.get("firstName") or profile_data.get("first_name", "")
    last_name = profile_data.get("lastName") or profile_data.get("last_name", "")
    email = profile_data.get("email", "")
    phone = profile_data.get("mobilePhone") or profile_data.get("mobile_phone", "") or ""
    phone = re.sub(r'^\+\d+\s*', '', phone)
    country_id = profile_data.get("countryId") or profile_data.get("country_id", "US")
    if not first_name or not email:
        return None
    return UserProfile(first_name=first_name, last_name=last_name, email=email, mobile_phone_number=phone, country_id=country_id)


class BookingClient:
    def __init__(self, profile: OTProfile):
        self.profile = profile
        self.http = profile.http
        self._booking_details: BookingDetails | None = None
        self._user_profile: UserProfile | None = None

    def _ssr_headers(self, extra: dict | None = None) -> dict:
        h = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Referer": self.profile._page_url or f"{self.profile.base_url}/r/{self.profile.restaurant_slug}",
        }
        if extra:
            h.update(extra)
        return h

    def _api_headers(self, extra: dict | None = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self.profile.csrf_token,
            "Accept": "application/json",
            "Referer": self.profile._page_url or f"{self.profile.base_url}/r/{self.profile.restaurant_slug}",
            "Origin": "https://www.opentable.com",
        }
        if extra:
            h.update(extra)
        return h

    def _cookies(self, auth_cke: str = "") -> dict:
        cookies = dict(self.profile.to_cookie_dict())
        if auth_cke:
            cookies["authCke"] = auth_cke
        return cookies

    def fetch_booking_details(self, restaurant_id: int, date: str, time: str, party_size: int,
                              slot_hash: str, slot_availability_token: str,
                              dining_area_id: int | None = None) -> BookingDetails:
        params = {
            "rid": restaurant_id,
            "datetime": f"{date}T{time}",
            "covers": party_size,
            "partySize": party_size,
            "seating": "default",
            "slotHash": slot_hash,
            "slotAvailabilityToken": slot_availability_token,
        }
        if dining_area_id:
            params["diningAreaId"] = dining_area_id
        url = f"{URL_BOOKING_DETAILS}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
        logger.debug("GET booking/details: %s", url)
        resp = self.http.get(url, headers=self._ssr_headers(), cookies=self._cookies())
        logger.debug("booking/details 状态码: %d, 大小: %d bytes", resp.status_code, len(resp.text))
        if resp.status_code != 200:
            raise RuntimeError(f"booking/details 返回 {resp.status_code}")
        state = extract_initial_state(resp.text)
        if not state:
            raise RuntimeError("无法从 booking/details 解析 __INITIAL_STATE__")
        details = parse_booking_details_state(state)
        self._booking_details = details
        return details

    def lock_slot(self, restaurant_id: int, date: str, time: str, party_size: int,
                  slot_hash: str, dining_area_id: int, slot_availability_token: str = "") -> SlotLockResult:
        url = f"{URL_GQL}?optype=mutation&opname=BookDetailsStandardSlotLock"
        payload = {
            "operationName": "BookDetailsStandardSlotLock",
            "variables": {
                "input": {
                    "restaurantId": restaurant_id,
                    "seatingOption": "DEFAULT",
                    "reservationDateTime": f"{date}T{time}",
                    "partySize": party_size,
                    "databaseRegion": "NA",
                    "slotHash": slot_hash,
                    "reservationType": "STANDARD",
                    "diningAreaId": dining_area_id,
                }
            },
            "extensions": {
                "persistedQuery": {"version": 1, "sha256Hash": HASH_SLOT_LOCK},
            },
        }
        logger.debug("POST slotLock: restaurant_id=%d date=%s time=%s", restaurant_id, date, time)
        resp = self.http.post(url, data=json.dumps(payload),
                              headers=self._api_headers({"ot-page-type": "network_details", "ot-page-group": "booking"}),
                              cookies=self._cookies())
        logger.debug("slotLock 状态码: %d", resp.status_code)
        self.profile.update_from_response(resp)
        if resp.status_code != 200:
            return SlotLockResult(success=False, error_message=f"HTTP {resp.status_code}", raw={"status": resp.status_code, "text": resp.text[:500]})
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return SlotLockResult(success=False, error_message=f"JSON解析失败: {resp.text[:200]}", raw={"text": resp.text[:500]})
        lock_result = data.get("data", {}).get("lockSlot", {})
        if lock_result.get("success") and lock_result.get("slotLock", {}).get("slotLockId"):
            sl_id = lock_result["slotLock"]["slotLockId"]
            logger.debug("Slot lock 成功: slotLockId=%d", sl_id)
            return SlotLockResult(success=True, slot_lock_id=sl_id, raw=data)
        else:
            err = lock_result.get("slotLockErrors") or data.get("errors", [{}])[0].get("message", "Unknown error")
            logger.debug("Slot lock 失败: %s", err)
            return SlotLockResult(success=False, error_message=str(err), raw=data)

    def fetch_profile(self, auth_cke: str = "") -> UserProfile:
        url = "https://www.opentable.com/user/dining-dashboard"
        logger.debug("GET dining-dashboard")
        resp = self.http.get(url, headers=self._ssr_headers({"Referer": "https://www.opentable.com/"}),
                              cookies=self._cookies(auth_cke))
        logger.debug("dining-dashboard 状态码: %d", resp.status_code)
        if resp.status_code in (302, 401):
            raise RuntimeError("用户未登录或 session 过期，需要有效的 authCke cookie")
        if resp.status_code != 200:
            raise RuntimeError(f"dining-dashboard 返回 {resp.status_code}")
        state = extract_initial_state(resp.text)
        if not state:
            raise RuntimeError("无法从 dining-dashboard 解析 __INITIAL_STATE__")
        profile = parse_user_profile(state)
        if not profile:
            raise RuntimeError("无法从 dining-dashboard 解析用户信息（可能未登录）")
        self._user_profile = profile
        return profile

    def make_reservation(self, restaurant_id: int, date: str, time: str, party_size: int,
                        slot_hash: str, slot_availability_token: str, slot_lock_id: int,
                        dining_area_id: int, profile: UserProfile,
                        payment_card: SavedCard | None = None, cc_required: bool = False,
                        auth_cke: str = "", is_modify: bool = False,
                        existing_conf_number: int | None = None,
                        existing_security_token: str | None = None,
                        use_fake_card: bool = False) -> BookingResult:
        cc_fields = {}
        if payment_card and cc_required:
            cc_fields = {
                "creditCardToken": payment_card.card_id,
                "creditCardLast4": payment_card.last4,
                "creditCardMMYY": payment_card.expiry_mm_yy(),
                "creditCardProvider": "spreedly",
                "scaRedirectUrl": "https://www.opentable.com/booking/payments-sca",
            }
        elif cc_required and not payment_card:
            if use_fake_card:
                fake = generate_fake_card()
                logger.warning("[测试] CC required 但未提供卡，自动使用虚拟卡: %s ***%s exp %s/%s",
                              fake.brand, fake.last4, fake.expiry_month, fake.expiry_year)
                cc_fields = {
                    "creditCardToken": fake.card_id,
                    "creditCardLast4": fake.last4,
                    "creditCardMMYY": fake.expiry_mm_yy(),
                    "creditCardProvider": "spreedly",
                    "scaRedirectUrl": "https://www.opentable.com/booking/payments-sca",
                }
        modify_fields = {"isModify": True, "securityToken": existing_security_token, "confnumber": existing_conf_number} if is_modify else {"isModify": False}
        body = {
            "restaurantId": restaurant_id,
            "reservationDateTime": f"{date}T{time}",
            "partySize": party_size,
            "slotHash": slot_hash,
            "slotAvailabilityToken": slot_availability_token,
            "slotLockId": slot_lock_id,
            "diningAreaId": dining_area_id,
            "firstName": profile.first_name,
            "lastName": profile.last_name,
            "email": profile.email,
            "phoneNumber": profile.mobile_phone_number,
            "phoneNumberCountryId": profile.country_id,
            "country": profile.country_id,
            "reservationAttribute": "default",
            "pointsType": "Standard",
            "points": 100,
            "tipAmount": 0,
            "tipPercent": 0,
            "confirmPoints": True,
            "optInEmailRestaurant": False,
            "additionalServiceFees": [],
            "nonBookableExperiences": [],
            "katakanaFirstName": "",
            "katakanaLastName": "",
            "correlationId": str(uuid.uuid4()),
            "reservationType": "Standard",
            **modify_fields,
            **cc_fields,
        }
        logger.debug("POST make-reservation: restaurant_id=%d date=%s time=%s", restaurant_id, date, time)
        resp = self.http.post(URL_MAKE_RESERVATION, data=json.dumps(body),
                               headers=self._api_headers({"ot-page-type": "network_confirmation", "ot-page-group": "booking"}),
                               cookies=self._cookies(auth_cke))
        logger.debug("make-reservation 状态码: %d", resp.status_code)
        self.profile.update_from_response(resp)
        if resp.status_code == 403:
            return BookingResult(success=False, error_code="403", error_message="请求被拦截（可能缺少 authCke cookie 或 session 过期）", raw={"status": resp.status_code, "text": resp.text[:500]})
        if resp.status_code == 409:
            return BookingResult(success=False, error_code="409", error_message="预约冲突（同一天已有预约或 slot 已过期）", raw={"status": resp.status_code, "text": resp.text[:500]})
        if resp.status_code not in (200, 201):
            return BookingResult(success=False, error_code=str(resp.status_code), error_message=f"HTTP {resp.status_code}: {resp.text[:300]}", raw={"status": resp.status_code, "text": resp.text[:500]})
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return BookingResult(success=False, error_code="parse_error", error_message=f"响应非 JSON: {resp.text[:200]}", raw={"text": resp.text[:500]})
        if data.get("partnerScaRequired"):
            return BookingResult(success=False, error_code="SCA_REQUIRED", error_message="该卡需要 3D Secure 认证", raw=data)
        if data.get("errorCode") or data.get("success") is False:
            err_code = data.get("errorCode", "unknown")
            err_msg = data.get("errorMessage", "")
            if "slot" in err_code.lower() or "lock" in err_code.lower():
                err_msg = "SLOT_LOCK_EXPIRED: slot 锁定已过期（有效期约90秒），请重新执行预约"
            return BookingResult(success=False, error_code=err_code, error_message=err_msg, raw=data)
        conf_num = data.get("confirmationNumber") or data.get("confirmation_number")
        if conf_num:
            return BookingResult(success=True, confirmation_number=int(conf_num), security_token=data.get("securityToken", ""), points=int(data.get("points", 0)), raw=data)
        return BookingResult(success=False, error_code="no_confirmation", error_message=f"响应缺少 confirmationNumber: {json.dumps(data)[:300]}", raw=data)

    def cancel_reservation(self, restaurant_id: int, confirmation_number: int,
                          security_token: str, auth_cke: str = "") -> dict:
        url = f"{URL_GQL}?optype=mutation&opname=CancelReservation"
        payload = {
            "operationName": "CancelReservation",
            "variables": {
                "input": {
                    "restaurantId": restaurant_id,
                    "confirmationNumber": confirmation_number,
                    "securityToken": security_token,
                    "databaseRegion": "NA",
                    "reservationSource": "Online",
                }
            },
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH_CANCEL}},
        }
        logger.debug("POST cancel: conf=%d", confirmation_number)
        resp = self.http.post(url, data=json.dumps(payload),
                              headers=self._api_headers({"ot-page-type": "network_confirmation", "ot-page-group": "booking"}),
                              cookies=self._cookies(auth_cke))
        logger.debug("cancel 状态码: %d", resp.status_code)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return {"success": False, "error": f"JSON解析失败: {resp.text[:200]}"}
        result = data.get("data", {}).get("cancelReservation", {})
        status_code = result.get("statusCode")
        state = result.get("data", {}).get("reservationState", "") if result.get("data") else ""
        if status_code == 200 and "cancel" in state.lower() and not result.get("errors"):
            return {"success": True, "state": state, "raw": data}
        return {"success": False, "state": state, "raw": data}
