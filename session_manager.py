# session_manager.py
# 创建日期: 2026-06-07 13:07:00（北京时间 UTC+8）
# 更新日期: 2026-06-08 11:07:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: Cookie 生命周期管理与 HTTP 会话维持（curl_cffi 优先 + RiskByPass fallback）

"""
SessionManager - Cookie 生命周期与 HTTP 会话

设计思路（双轨策略，实测验证 2026-06-07）:
  路线A（curl_cffi）: GET 主页 -> curl_cffi 生成短 _abck -> GraphQL 200
  路线B（RiskByPass）: curl_cffi 被拦截 -> RiskByPass 生成 _abck

日志级别:
  DEBUG -> 文件（Step 步骤、Cookie 状态、HTTP 详情）
  WARNING -> 控制台 + 文件（可恢复错误）
  ERROR -> 控制台 + 文件（请求失败）
"""

import json
import re
import time
import uuid
import logging
from datetime import datetime

import config
from logger_setup import debug
logger = debug()

_use_curl = False
try:
    from curl_cffi import requests as curl_requests
    _use_curl = True
except ImportError:
    pass

import requests as std_requests


# ============================================================================
# HTTP 客户端封装
# ============================================================================

class HTTPClient:
    """HTTP 客户端，统一 requests / curl_cffi 接口"""

    def __init__(self, proxy: str = None):
        self._proxy = proxy or config.PROXY
        self._use_curl = _use_curl

        if self._use_curl:
            self.session = curl_requests.Session(
                impersonate=config.CURL_IMPERSONATE,
                proxies={"https": self._proxy, "http": self._proxy} if self._proxy else None,
                verify=False if self._proxy else True,
            )
            self._engine = "curl_cffi"
        else:
            self.session = std_requests.Session()
            self._engine = "requests"

        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Origin": "https://www.opentable.com",
        })
        self.session.timeout = config.REQUEST_TIMEOUT

    def get(self, url, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url, **kwargs):
        return self.session.post(url, **kwargs)

    def request(self, method, url, **kwargs):
        return self.session.request(method, url, **kwargs)

    @property
    def engine(self) -> str:
        return self._engine


# ============================================================================
# Cookie 管理器
# ============================================================================

class OTProfile:
    """单个餐厅查询会话的完整状态"""

    def __init__(self, http_client: HTTPClient, restaurant_slug: str = "", restaurant_id: int = 0):
        self.http = http_client
        self.csrf_token: str = ""
        self.restaurant_slug = restaurant_slug or config.RESTAURANT_SLUG
        self.restaurant_id = restaurant_id or config.RESTAURANT_ID
        self.base_url = "https://www.opentable.com"
        self._page_url = ""
        self._cookies = {}
        self._abck_request_count = 0
        self._initialized = False
        self.gql_persist_hash: str = ""

    def set_restaurant(self, slug: str, restaurant_id: int):
        """运行时修改餐厅信息"""
        self.restaurant_slug = slug
        self.restaurant_id = restaurant_id

    def set_cookie(self, name: str, value: str):
        self._cookies[name] = value

    def get_cookie(self, name: str) -> str:
        return self._cookies.get(name, "")

    def update_from_response(self, resp):
        for name, value in resp.cookies.get_dict().items():
            self._cookies[name] = value

    def to_cookie_dict(self) -> dict:
        return dict(self._cookies)

    def register_abck(self, abck: str, ak_bmsc: str = "",
                       bm_sz: str = "", bm_s: str = ""):
        self.set_cookie("_abck", abck)
        if ak_bmsc:
            self.set_cookie("ak_bmsc", ak_bmsc)
        if bm_sz:
            self.set_cookie("bm_sz", bm_sz)
        if bm_s:
            self.set_cookie("bm_s", bm_s)
        self._abck_request_count = 0
        logger.debug("RiskByPass _abck 注册成功 (len=%d)", len(abck))

    def should_renew_abck(self) -> bool:
        return self._abck_request_count >= config.ABCK_MAX_REQUESTS

    def record_request(self):
        self._abck_request_count += 1

    def generate_bm_lso(self):
        """bm_lso = bm_so_seed~payload~timestamp_ms"""
        bm_so = self.get_cookie("bm_so")
        if not bm_so or "~" not in bm_so:
            logger.debug("bm_so 不存在，跳过 bm_lso 生成")
            return False

        parts = bm_so.split("~")
        if len(parts) >= 2:
            seed_and_payload = "~".join(parts[:2])
            timestamp_ms = str(int(time.time() * 1000))
            bm_lso = f"{seed_and_payload}~{timestamp_ms}"
            self.set_cookie("bm_lso", bm_lso)
            logger.debug("bm_lso 生成: %s...", bm_lso[:60])
            return True
        return False

    def _generate_csrf(self) -> str:
        """任意非空字符串即可，服务器不验证内容"""
        return str(uuid.uuid4())

    def init(self, abck_info: dict = None,
             use_abck_from_riskbypass: bool = False) -> bool:
        """执行 Step1-4 初始化流程"""
        if self._initialized:
            logger.debug("Session 已初始化，跳过 Step1-4")
            return True

        page_url = config.RESTAURANT_PAGE_URL

        # = Step 1: GET 主页 =
        resp1 = self.http.get(
            page_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        logger.debug("Step1 GET %s 状态码: %d, 内容: %d 字节, 引擎: %s",
                     page_url, resp1.status_code, len(resp1.text), self.http.engine)
        self._page_url = page_url
        self.update_from_response(resp1)

        if use_abck_from_riskbypass and abck_info and abck_info.get("abck"):
            self.register_abck(
                abck_info["abck"],
                abck_info.get("ak_bmsc", ""),
                abck_info.get("bm_sz", ""),
                abck_info.get("bm_s", ""),
            )

        self.csrf_token = self._generate_csrf()
        logger.debug("CSRF Token 生成: %s", self.csrf_token)

        # = Step 1.5: 提取 GraphQL hash =
        try:
            from get_gql_hash import fetch_gql_hash
            self.gql_persist_hash = fetch_gql_hash(self.http, self._cookies)
        except Exception as e:
            logger.warning("Step1.5 动态获取 hash 失败: %s，使用 config 兜底", e)
            self.gql_persist_hash = config.GQL_PERSIST_HASH

        # = Step 2: POST trackgoal (可选) =
        logger.debug("Step2 POST /trackgoal")
        try:
            tg_url = f"{self.base_url}/dapi/fe/proxy/consumer-frontend/trackgoal"
            tg_resp = self.http.post(
                tg_url,
                data="user_viewed_4_or_less_reviews",
                headers={
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Referer": page_url,
                },
                cookies=self.to_cookie_dict(),
            )
            self.update_from_response(tg_resp)
            logger.debug("Step2 状态码: %d", tg_resp.status_code)
        except Exception as e:
            logger.debug("Step2 跳过: %s", e)

        # = Step 3: POST /dapi/v1/session =
        logger.debug("Step3 POST /dapi/v1/session")
        sess_url = f"{self.base_url}/dapi/v1/session"
        sess_resp = self.http.post(
            sess_url,
            data="{}",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.csrf_token,
                "Referer": page_url,
            },
            cookies=self.to_cookie_dict(),
        )
        logger.debug("Step3 状态码: %d", sess_resp.status_code)
        self.update_from_response(sess_resp)

        # = Step 4: POST /dapi/fe/human (可选) =
        logger.debug("Step4 POST /dapi/fe/human")
        try:
            human_url = f"{self.base_url}/dapi/fe/human"
            human_resp = self.http.post(
                human_url,
                data="{}",
                headers={
                    "Content-Type": "application/json",
                    "X-CSRF-Token": self.csrf_token,
                    "Referer": page_url,
                },
                cookies=self.to_cookie_dict(),
            )
            self.update_from_response(human_resp)
            logger.debug("Step4 状态码: %d", human_resp.status_code)
        except Exception as e:
            logger.debug("Step4 跳过: %s", e)

        # 生成 bm_lso（依赖 Step1 的 bm_so）
        self.generate_bm_lso()

        # 确保 ftc 存在
        if not self.get_cookie("ftc"):
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            self.set_cookie("ftc", (
                f"x={ts}&c=1&pt1=1&pt2=1&er={self.restaurant_id}"
                f"&p1ca=restaurant%2Fprofile%2F{self.restaurant_id}"
            ))
            logger.debug("ftc 补充生成")

        self._initialized = True
        self._log_cookie_status()
        return True

    def refresh_from_graphql_response(self, resp):
        """每次 GraphQL 响应后刷新 bm_s / bm_sv"""
        before_s = self.get_cookie("bm_s")
        before_sv = self.get_cookie("bm_sv")
        self.update_from_response(resp)
        if self.get_cookie("bm_s") != before_s:
            logger.debug("bm_s 已刷新")
        if self.get_cookie("bm_sv") != before_sv:
            logger.debug("bm_sv 已刷新")

    def validate_cookies(self) -> tuple:
        """验证必需 cookie"""
        required = [
            "_abck", "bm_mi", "bm_ss", "bm_so", "bm_sz",
            "bm_s", "bm_sc", "bm_lso", "ftc",
        ]
        missing = [n for n in required if not self.get_cookie(n)]
        return len(missing) == 0, missing

    def _log_cookie_status(self):
        important = [
            "_abck", "bm_mi", "bm_s", "bm_sv", "bm_sz",
            "bm_ss", "bm_so", "bm_sc", "bm_lso",
            "OT-SessionId", "OT-Session-Update-Date",
            "OT-Interactive-SessionId", "ak_bmsc", "ftc",
        ]
        logger.debug("Cookie 当前状态:")
        for name in important:
            val = self.get_cookie(name)
            if val:
                logger.debug("  %s: %s... (len=%d)", name, val[:40], len(val))
            else:
                logger.debug("  %s: --", name)


# ============================================================================
# 会话工厂
# ============================================================================

_session_http: HTTPClient = None
_session_profile: OTProfile = None
_session_proxy: str = None
_session_restaurant_slug: str = ""
_session_restaurant_id: int = 0


def get_session(proxy: str = None, restaurant_slug: str = None,
                 restaurant_id: int = None) -> OTProfile:
    global _session_http, _session_profile, _session_proxy
    global _session_restaurant_slug, _session_restaurant_id

    proxy = proxy or _session_proxy or config.PROXY
    slug = restaurant_slug if restaurant_slug else _session_restaurant_slug
    rid = restaurant_id if restaurant_id is not None else _session_restaurant_id

    if _session_http is None or _session_proxy != proxy:
        _session_proxy = proxy
        _session_http = HTTPClient(proxy=proxy)
        _session_profile = OTProfile(_session_http, slug, rid)
    else:
        if restaurant_slug and restaurant_slug != _session_profile.restaurant_slug:
            _session_profile.set_restaurant(restaurant_slug, rid or _session_profile.restaurant_id)
            _session_profile._initialized = False

    return _session_profile


def reset_session():
    global _session_http, _session_profile
    _session_http = None
    _session_profile = None
    logger.debug("Session 已重置")
