# graphql_client.py
# 创建日期: 2026-06-07 13:07:00（北京时间 UTC+8）
# 更新日期: 2026-06-07 19:58:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: OpenTable GraphQL API 客户端，查询餐厅可用时段

"""
日志级别:
  DEBUG -> 文件（请求详情、状态码、解析过程）
  ERROR -> 控制台 + 文件（请求异常、JSON 解析失败）
"""

import json
import uuid
import logging

import config
from session_manager import OTProfile
from logger_setup import debug
logger = debug()


# ============================================================================
# 响应数据模型
# ============================================================================

class TimeSlot:
    """可用时段对象"""

    def __init__(self, slot_data: dict):
        self.raw = slot_data
        self.time_offset_minutes = slot_data.get("timeOffsetMinutes", 0)
        self.points_value = slot_data.get("pointsValue", 0)
        self.is_available = slot_data.get("isAvailable", False)
        self.reservation_date = slot_data.get("reservationDate", "")
        self.attributes = slot_data.get("attributes", [])
        self.slot_hash = slot_data.get("slotHash", "")
        self.slot_availability_token = slot_data.get("slotAvailabilityToken", "")

    def __repr__(self):
        status = "available" if self.is_available else "unavailable"
        return (
            f"TimeSlot(offset={self.time_offset_minutes}min, "
            f"points={self.points_value}, {status})"
        )


# ============================================================================
# GraphQL 客户端
# ============================================================================

class GQLClient:
    """OpenTable RestaurantsAvailability GraphQL 客户端"""

    def __init__(self, profile: OTProfile):
        self.profile = profile
        self.http = profile.http

    def _build_payload(self, date: str, time_str: str,
                       party_size: int) -> dict:
        correlation_id = str(uuid.uuid4())

        variables = dict(config.GQL_VARIABLES_FIXED)
        variables.update({
            "restaurantIds": [self.profile.restaurant_id],
            "date": date,
            "time": time_str,
            "partySize": party_size,
            "attributionToken": self.profile.get_cookie("ftc") or "",
            "correlationId": correlation_id,
            "gpid": 0,
        })

        persist_hash = self.profile.gql_persist_hash or config.GQL_PERSIST_HASH

        return {
            "operationName": config.GQL_OPERATION_NAME,
            "variables": variables,
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": persist_hash,
                }
            },
        }

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-CSRF-Token": self.profile.csrf_token,
            "X-Query-Timeout": "5500",
            "ot-page-group": "rest-profile",
            "ot-page-type": "restprofilepage",
            "priority": "u=1, i",
            "Referer": f"{config.RESTAURANT_URL}",
        }

    def query(self, date: str, time_str: str = "19:00",
              party_size: int = 2) -> "GQLResult":
        """
        查询指定日期/时间/人数的可用时段
        """
        url = "https://www.opentable.com/dapi/fe/gql"
        params = {"optype": "query", "opname": "RestaurantsAvailability"}

        payload = self._build_payload(date, time_str, party_size)
        headers = self._build_headers()

        logger.debug("GQL 查询: date=%s time=%s party=%d URL=%s",
                     date, time_str, party_size, url)

        if self.profile.should_renew_abck():
            logger.warning("abck 达到最大请求次数，需刷新")

        self.profile.record_request()

        try:
            resp = self.http.post(
                url,
                params=params,
                data=json.dumps(payload),
                headers=headers,
                cookies=self.profile.to_cookie_dict(),
            )
        except Exception as e:
            logger.error("GQL 请求异常: %s", e)
            return GQLResult(
                success=False,
                status_code=0,
                slots=[],
                raw_data=None,
                error=f"请求异常: {e}",
                needs_abck_renew=False,
            )

        logger.debug("GQL 状态码: %d", resp.status_code)
        self.profile.refresh_from_graphql_response(resp)
        needs_renew = self._check_abck_expired(resp)

        if resp.status_code != 200:
            error_text = resp.text[:300] if resp.text else ""
            logger.error("GQL HTTP 错误: %d - %s", resp.status_code, error_text)
            return GQLResult(
                success=False,
                status_code=resp.status_code,
                slots=[],
                raw_data=None,
                error=f"HTTP {resp.status_code}: {error_text}",
                needs_abck_renew=needs_renew,
            )

        try:
            data = resp.json()
        except Exception as e:
            logger.error("GQL JSON 解析失败: %s", e)
            return GQLResult(
                success=False,
                status_code=resp.status_code,
                slots=[],
                raw_data=None,
                error=f"JSON 解析失败: {e}",
                needs_abck_renew=False,
            )

        gql_errors = data.get("errors", [])
        if gql_errors:
            error_msg = "; ".join(e.get("message", "") for e in gql_errors[:2])
            logger.error("GQL 错误: %s", error_msg)
            return GQLResult(
                success=False,
                status_code=resp.status_code,
                slots=[],
                raw_data=data,
                error=f"GraphQL Error: {error_msg}",
                needs_abck_renew=needs_renew,
            )

        slots = self._parse_slots(data)

        logger.debug("GQL 成功，共 %d 个时段，%d 个可用",
                     len(slots), len([s for s in slots if s.is_available]))

        return GQLResult(
            success=True,
            status_code=resp.status_code,
            slots=slots,
            raw_data=data,
            error=None,
            needs_abck_renew=False,
        )

    def _check_abck_expired(self, resp) -> bool:
        """检查 _abck 是否已失效"""
        if resp.status_code in (403, 503):
            return True
        text = resp.text.lower()
        if "access denied" in text or "akamai" in text:
            return True
        return False

    def _parse_slots(self, data: dict) -> list[TimeSlot]:
        """从 GraphQL 响应中解析可用时段"""
        slots = []
        try:
            avail_list = data.get("data", {}).get("availability", [])
            for avail in avail_list:
                days = avail.get("availabilityDays", [])
                for day in days:
                    for s in day.get("slots", []):
                        slots.append(TimeSlot(s))
        except Exception as e:
            logger.error("Slot 解析异常: %s", e)

        return slots


class GQLResult:
    """GraphQL 查询结果封装"""

    def __init__(
        self,
        success: bool,
        status_code: int,
        slots: list[TimeSlot],
        raw_data: dict | None,
        error: str | None,
        needs_abck_renew: bool,
    ):
        self.success = success
        self.status_code = status_code
        self.slots = slots
        self.raw_data = raw_data
        self.error = error
        self.needs_abck_renew = needs_abck_renew

    @property
    def available_slots(self) -> list[TimeSlot]:
        return [s for s in self.slots if s.is_available]

    def __repr__(self):
        total = len(self.slots)
        avail = len(self.available_slots)
        return f"GQLResult(success={self.success}, total={total}, available={avail})"
