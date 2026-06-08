# main.py
# 创建日期: 2026-06-07 13:07:00（北京时间 UTC+8）
# 更新日期: 2026-06-08 11:07:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: OpenTable Nobu 餐厅时段查询主入口，支持轮询模式

"""
INFO 输出格式:
  有 slot:  2026-06-07 20:06 nobu-los-angeles-west-hollywood | 2026-07-04 19:00 | 4人 | 4 slots | 17:45 | 18:00 | 19:30 | 19:45
  无 slot:   2026-06-07 20:06 nobu-los-angeles-west-hollywood | 2026-07-04 19:00 | 4人 | no slots
"""

import argparse
import json
import os
import sys
import time as time_module
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from logger_setup import setup, debug, result
from riskbypass_client import AbckGenerator
from session_manager import get_session, reset_session, OTProfile
from graphql_client import GQLClient, GQLResult

setup()

# ============================================================================
# abck 管理器
# ============================================================================

class AbckManager:
    def __init__(self):
        self._generator = AbckGenerator()
        self._refresh_count = 0

    def check_balance(self):
        return self._generator.check_balance()

    def get_abck(self) -> dict | None:
        result().info("RiskByPass fetching _abck...")
        res = self._generator.get_abck(
            target_url=config.RESTAURANT_URL,
            akamai_js_url=config.AKAMAI_JS_URL,
            page_fp=config.PAGE_FP,
        )
        if res["status"] != "success":
            result().warning("RiskByPass FAIL: %s", res.get("error", "unknown"))
            return None
        self._refresh_count += 1
        result().info("RiskByPass OK (count=%d, len=%d)", self._refresh_count, len(res["abck"]))
        return res

# ============================================================================
# 结果输出
# ============================================================================

def _slot_times(slots: list, base_date: str, base_time: str) -> list[str]:
    base_dt = datetime.strptime(f"{base_date} {base_time}", "%Y-%m-%d %H:%M")
    return [(base_dt + timedelta(minutes=s.time_offset_minutes)).strftime("%H:%M")
            for s in slots]


def _log_result(result_obj: GQLResult, date_str: str, time_str: str, party_size: int, qnum: int = 0):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not result_obj.success:
        result().info(
            "%s %s | %s %s | %d人 | FAIL: HTTP %d",
            ts, config.RESTAURANT_SLUG, date_str, time_str, party_size, result_obj.status_code)
        return

    avail = result_obj.available_slots
    if not avail:
        result().info(
            "%s %s | %s %s | %d人 | no slots",
            ts, config.RESTAURANT_SLUG, date_str, time_str, party_size)
    else:
        times = _slot_times(avail, date_str, time_str)
        result().info(
            "%s %s | %s %s | %d人 | %d slots | %s",
            ts, config.RESTAURANT_SLUG, date_str, time_str, party_size,
            len(avail), " | ".join(times))

    if result_obj.raw_data:
        try:
            tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
            os.makedirs(tmp, exist_ok=True)
            out = os.path.join(tmp, f"gql_{date_str}_{qnum}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(result_obj.raw_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# ============================================================================
# 主流程
# ============================================================================

def run_once(date_str: str, time_str: str, party_size: int, proxy: str = "",
             restaurant_slug: str = "") -> int:
    abck_mgr = AbckManager()

    reset_session()
    profile = get_session(proxy=proxy, restaurant_slug=restaurant_slug or None)
    profile.init()
    gql = GQLClient(profile)

    res = gql.query(date_str, time_str, party_size)

    if not res.success and res.status_code == 403:
        debug().info("curl_cffi blocked (403), switching to RiskByPass...")
        abck_info = abck_mgr.get_abck()
        if abck_info:
            reset_session()
            profile = get_session(proxy=proxy, restaurant_slug=restaurant_slug or None)
            profile.register_abck(
                abck_info["abck"],
                abck_info.get("ak_bmsc", ""),
                abck_info.get("bm_sz", ""),
                abck_info.get("bm_s", ""),
            )
            profile.init(abck_info, use_abck_from_riskbypass=True)
            gql = GQLClient(profile)
            res = gql.query(date_str, time_str, party_size)

    _log_result(res, date_str, time_str, party_size, 1)
    return len(res.available_slots)


def run_poll(date_str: str, time_str: str, party_size: int,
             poll_interval: int, proxy: str = "", restaurant_slug: str = ""):
    result().info(
        "POLL %s %s %d | interval=%ds max_abck_req=%d",
        date_str, time_str, party_size, poll_interval, config.ABCK_MAX_REQUESTS)

    abck_mgr = AbckManager()

    reset_session()
    profile = get_session(proxy=proxy)
    profile.init()
    gql = GQLClient(profile)

    qnum = 0
    try:
        while True:
            qnum += 1
            if profile.should_renew_abck():
                debug().info("abck limit reached, resetting session...")
                reset_session()
                profile = get_session(proxy=proxy, restaurant_slug=restaurant_slug or None)
                profile.init()
                gql = GQLClient(profile)

            res = gql.query(date_str, time_str, party_size)

            if not res.success and res.status_code == 403:
                debug().info("403 blocked, switching to RiskByPass...")
                abck_info = abck_mgr.get_abck()
                if abck_info:
                    reset_session()
                    profile = get_session(proxy=proxy, restaurant_slug=restaurant_slug or None)
                    profile.register_abck(
                        abck_info["abck"], abck_info.get("ak_bmsc", ""),
                        abck_info.get("bm_sz", ""), abck_info.get("bm_s", ""),
                    )
                    profile.init(abck_info, use_abck_from_riskbypass=True)
                    gql = GQLClient(profile)
                    res = gql.query(date_str, time_str, party_size)
                else:
                    debug().info("RiskByPass failed, skip this round")

            _log_result(res, date_str, time_str, party_size, qnum)
            time_module.sleep(poll_interval)

    except KeyboardInterrupt:
        result().info("POLL END (queries=%d)", qnum)


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    today = date.today().strftime("%Y-%m-%d")
    p = argparse.ArgumentParser(
        description="OpenTable 时段查询 + 预约（curl_cffi + RiskByPass）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-d", "--date", default=today, help="YYYY-MM-DD")
    p.add_argument("-t", "--time", default=config.DEFAULT_TIME, help="HH:MM")
    p.add_argument("-p", "--party", type=int, default=config.DEFAULT_PARTY_SIZE, help="人数")
    p.add_argument("--poll", action="store_true", help="轮询模式（仅查询）")
    p.add_argument("--poll-interval", type=int, default=config.POLL_INTERVAL, help="轮询间隔(秒)")
    p.add_argument("--check-balance", action="store_true", help="仅查 RiskByPass 余额")
    p.add_argument("--proxy", default="", help="http://user:pass@ip:port")
    p.add_argument("--restaurant-slug", default="", help="餐厅 slug（覆盖 config）")
    p.add_argument("--restaurant-id", type=int, default=0, help="餐厅 ID（覆盖 config，0=不覆盖）")
    p.add_argument("--restaurant-page-url", default="", help="第一步 GET 的完整 URL（覆盖 config.RESTAURANT_PAGE_URL）")
    p.add_argument("--book", action="store_true", help="执行预约（需要 --auth-cke）")
    p.add_argument("--preview", action="store_true", help="仅预览预约详情（CC策略、座位区）")
    p.add_argument("--slot-hash", default="", help="slotHash（从查询结果获取）")
    p.add_argument("--slot-token", default="", help="slotAvailabilityToken（从查询结果获取）")
    p.add_argument("--dining-area-id", type=int, default=None, help="座位区 ID（可选，自动解析）")
    p.add_argument("--auth-cke", default="", help="OpenTable 登录 cookie authCke")
    p.add_argument("--first-name", default="", help="名（可省略，自动获取）")
    p.add_argument("--last-name", default="", help="姓（可省略，自动获取）")
    p.add_argument("--email", default="", help="邮箱（可省略，自动获取）")
    p.add_argument("--phone", default="", help="电话（可省略，自动获取）")
    p.add_argument("--country", default="US", help="国家 ID（默认 US）")
    p.add_argument("--card-id", default="", help="信用卡 token（spreedly token）")
    p.add_argument("--card-last4", default="", help="卡号末4位")
    p.add_argument("--card-expiry", default="", help="卡过期时间 MMYY")
    p.add_argument("--card-brand", default="", help="卡品牌 Visa/Mastercard")
    return p.parse_args()


def run_book(args):
    from datetime import datetime, timedelta
    from booking_client import (
        BookingClient, SavedCard, UserProfile,
        resolve_dining_area_id,
    )
    from graphql_client import GQLClient

    abck_mgr = AbckManager()
    reset_session()
    profile = get_session(proxy=args.proxy, restaurant_slug=args.restaurant_slug or None)
    profile.init()
    gql = GQLClient(profile)
    bc = BookingClient(profile)

    # Step 1: 查询 slot（如果没有传入 slot-hash/token）
    if not args.slot_hash or not args.slot_token:
        result().info("未提供 --slot-hash，将自动查询...")
        res = gql.query(args.date, args.time, args.party)
        if not res.available_slots:
            result().info("没有可用 slot")
            return 1

        slot = res.available_slots[0]
        base = datetime.strptime(f"{args.date} {args.time}", "%Y-%m-%d %H:%M")
        actual = base + timedelta(minutes=slot.time_offset_minutes)
        args.slot_hash = slot.slot_hash
        args.slot_token = slot.slot_availability_token
        args.time = actual.strftime("%H:%M")
        result().info(
            "自动选择 slot: %s hash=%s token=%s",
            actual.strftime("%H:%M"),
            args.slot_hash[:20],
            args.slot_token[:20],
        )

        if not res.success and res.status_code == 403:
            debug().info("curl_cffi blocked (403), switching to RiskByPass...")
            abck_info = abck_mgr.get_abck()
            if abck_info:
                reset_session()
                profile = get_session(proxy=args.proxy)
                profile.register_abck(
                    abck_info["abck"], abck_info.get("ak_bmsc", ""),
                    abck_info.get("bm_sz", ""), abck_info.get("bm_s", ""),
                )
                profile.init(abck_info, use_abck_from_riskbypass=True)
                gql = GQLClient(profile)
                bc = BookingClient(profile)
                res = gql.query(args.date, args.time, args.party)
                if res.available_slots:
                    slot = res.available_slots[0]
                    base = datetime.strptime(f"{args.date} {args.time}", "%Y-%m-%d %H:%M")
                    actual = base + timedelta(minutes=slot.time_offset_minutes)
                    args.slot_hash = slot.slot_hash
                    args.slot_token = slot.slot_availability_token
                    args.time = actual.strftime("%H:%M")

    # Step 2: booking/details
    try:
        details = bc.fetch_booking_details(
            restaurant_id=config.RESTAURANT_ID,
            date=args.date,
            time=args.time,
            party_size=args.party,
            slot_hash=args.slot_hash,
            slot_availability_token=args.slot_token,
            dining_area_id=args.dining_area_id,
        )
    except Exception as e:
        result().info("Step 1 [booking/details] 失败: %s", e)
        return 1

    result().info(
        "Step 1 OK | CC Required: %s | Policy: %s",
        details.cc_required,
        details.policy.policy_type,
    )

    dining_area_id = args.dining_area_id or resolve_dining_area_id(details.dining_areas)
    if not dining_area_id:
        result().info("ERROR: 无法解析 diningAreaId，请传入 --dining-area-id")
        result().info("可用 dining areas: %s", [
            {"id": da["dining_area_id"], "name": da.get("name", "")}
            for da in details.dining_areas
        ])
        return 1
    result().info("DiningAreaId: %d", dining_area_id)

    # Step 3: slot lock
    result().info("Step 2: slotLock...")
    lock_result = bc.lock_slot(
        restaurant_id=config.RESTAURANT_ID,
        date=args.date,
        time=args.time,
        party_size=args.party,
        slot_hash=args.slot_hash,
        dining_area_id=dining_area_id,
        slot_availability_token=args.slot_token,
    )
    if not lock_result.success:
        result().info("Step 2 [slotLock] 失败: %s", lock_result.error_message)
        return 1
    result().info("Step 2 OK | slotLockId: %d", lock_result.slot_lock_id)

    # Step 4: 构建用户信息
    if args.first_name and args.last_name and args.email:
        profile_data = UserProfile(
            first_name=args.first_name,
            last_name=args.last_name,
            email=args.email,
            mobile_phone_number=args.phone or "5550000000",
            country_id=args.country or "US",
        )
    else:
        result().info("Step 3: 获取用户信息...")
        try:
            profile_data = bc.fetch_profile(args.auth_cke)
        except Exception as e:
            result().info("Step 3 [fetch_profile] 失败: %s", e)
            return 1
        result().info(
            "Step 3 OK | %s %s <%s>",
            profile_data.first_name, profile_data.last_name, profile_data.email,
        )

    # Step 5: make reservation
    result().info("Step 4: makeReservation...")
    card = None
    if args.card_id:
        card = SavedCard(
            card_id=args.card_id,
            brand=args.card_brand or "Visa",
            last4=args.card_last4 or "0000",
            expiry_month=int(args.card_expiry[:2]) if args.card_expiry else 1,
            expiry_year=int("20" + args.card_expiry[2:4]) if args.card_expiry else 2028,
        )
    use_fake = details.cc_required and card is None

    book_result = bc.make_reservation(
        restaurant_id=config.RESTAURANT_ID,
        date=args.date,
        time=args.time,
        party_size=args.party,
        slot_hash=args.slot_hash,
        slot_availability_token=args.slot_token,
        slot_lock_id=lock_result.slot_lock_id,
        dining_area_id=dining_area_id,
        profile=profile_data,
        payment_card=card,
        cc_required=details.cc_required,
        auth_cke=args.auth_cke,
        use_fake_card=use_fake,
    )

    if book_result.success:
        result().info("=" * 60)
        result().info("预约成功！Confirmation Number: %d", book_result.confirmation_number)
        result().info("Security Token: %s | Points: %d", book_result.security_token, book_result.points)
        result().info("=" * 60)
        return 0
    else:
        result().info("预约失败 | [%s] %s", book_result.error_code, book_result.error_message)
        if book_result.raw:
            tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            out = os.path.join(tmp_dir, f"book_failed_{int(datetime.now().timestamp())}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(book_result.raw, f, ensure_ascii=False, indent=2)
            result().info("详情: %s", out)
        return 1


def run_preview(args):
    from booking_client import BookingClient, resolve_dining_area_id
    from graphql_client import GQLClient

    if not args.slot_hash or not args.slot_token:
        result().info("预览需要 --slot-hash 和 --slot-token（从查询结果获取）")
        result().info("用法: python main.py --preview -d 2026-06-29 -t 19:00 --slot-hash <hash> --slot-token <token>")
        return 1

    reset_session()
    profile = get_session(proxy=args.proxy)
    profile.init()
    bc = BookingClient(profile)

    try:
        details = bc.fetch_booking_details(
            restaurant_id=config.RESTAURANT_ID,
            date=args.date,
            time=args.time,
            party_size=args.party,
            slot_hash=args.slot_hash,
            slot_availability_token=args.slot_token,
            dining_area_id=args.dining_area_id,
        )
    except Exception as e:
        result().info("preview 失败: %s", e)
        return 1

    result().info(
        "Preview | CC Required: %s | Policy: %s | Amount: %s | DiningAreas: %d",
        details.cc_required,
        details.policy.policy_type,
        f"${details.policy.amount_usd}" if details.policy.amount_usd else "N/A",
        len(details.dining_areas),
    )
    if details.default_card:
        result().info(
            "  Card: %s ***%s exp %s/%s",
            details.default_card.brand, details.default_card.last4,
            details.default_card.expiry_month, details.default_card.expiry_year,
        )
    for da in details.dining_areas:
        result().info(
            "  DiningArea: id=%d name='%s'",
            da["dining_area_id"], da.get("name", da["table_category"]),
        )
    if details.terms:
        result().info("  Terms: %s", details.terms[:200])
    return 0


def main():
    args = parse_args()

    if args.restaurant_slug:
        config.RESTAURANT_SLUG = args.restaurant_slug
        config.RESTAURANT_URL = f"https://www.opentable.com/{args.restaurant_slug}"
        config.RESTAURANT_PAGE_URL = config.RESTAURANT_URL

    if args.restaurant_page_url:
        config.RESTAURANT_PAGE_URL = args.restaurant_page_url

    if args.restaurant_id > 0:
        config.RESTAURANT_ID = args.restaurant_id

    debug().info("OT v1.2.0 | date=%s time=%s party=%d",
                 args.date, args.time, args.party)

    if args.check_balance:
        result().info("RiskByPass balance: %s", AbckManager().check_balance())
        return

    if args.preview:
        sys.exit(run_preview(args))
    elif args.book:
        sys.exit(run_book(args))
    elif args.poll:
        run_poll(args.date, args.time, args.party, args.poll_interval, proxy=args.proxy, restaurant_slug=args.restaurant_slug)
    else:
        avail = run_once(args.date, args.time, args.party, proxy=args.proxy, restaurant_slug=args.restaurant_slug)
        sys.exit(0 if avail > 0 else 1)


if __name__ == "__main__":
    main()
