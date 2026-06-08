# config.py
# 创建日期: 2026-06-07 13:07:00（北京时间 UTC+8）
# 更新日期: 2026-06-08 11:07:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: OpenTable Nobu 餐厅时段查询的全局配置

"""
OT_Nobu 配置模块

配置区说明:
  - 餐厅信息: restaurant_slug 和 restaurant_id
  - RiskByPass: 第三方 _abck 生成服务配置
  - 查询参数: 默认日期/时间/人数
  - 轮询参数: abck 有效期内可发多少次请求后强制刷新
"""

# ============================================================================
# 餐厅信息（用户可修改）
# ============================================================================
# 只改 RESTAURANT_SLUG，RESTAURANT_ID 照抄下面这行即可
# RESTAURANT_ID 获取方式：
#   1. 浏览器打开 https://www.opentable.com/r/<你的slug>
#   2. 打开 DevTools -> Network -> 找 GraphQL 请求 body 中的 restaurantIds
#   3. 或者运行 find_restaurant_id.py（传入页面 URL 自动提取）
RESTAURANT_SLUG = "nobu-los-angeles-west-hollywood"
RESTAURANT_ID = 17077

RESTAURANT_URL = f"https://www.opentable.com/{RESTAURANT_SLUG}"
# 第一步 GET 的完整 URL（需从页面或用户提供，格式可能是 /r/slug 或 /slug）
RESTAURANT_PAGE_URL = RESTAURANT_URL

# ============================================================================
# RiskByPass 第三方服务配置
# ============================================================================
RISKBASE_URL = "https://riskbypass.com"
RISK_TOKEN = "xyinner@gmail.com_vFQh2dNu0UHRxYvfwiI4gL0YrOOMcUN"
RISK_TIMEOUT = 120

# RiskByPass 请求构造参数（从历史 HAR + Akamai fp 数据推导）
AKAMAI_JS_URL = "https://www.opentable.com/vM1UqV12vQXFI5zadqi2/9k7whcw3YD9QhJ/MHNfInI/K1Yt/D1BiDmUB"
PAGE_FP = "42455e5a4e495c515f42425a4b474f485c5148"

# 代理（可选，格式: http://user:pass@ip:port 或 socks5://...）
PROXY = ""

# ============================================================================
# 查询默认参数
# ============================================================================
DEFAULT_TIME = "19:00"
DEFAULT_PARTY_SIZE = 4

# ============================================================================
# 会话与轮询策略
# ============================================================================
ABCK_MAX_REQUESTS = 50
REQUEST_TIMEOUT = 30
ABCK_RETRY = 3
GQL_RETRY = 3
RETRY_DELAY = 3
POLL_INTERVAL = 10

# ============================================================================
# User-Agent
# ============================================================================
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

# ============================================================================
# TLS / HTTP 客户端
# ============================================================================
HTTP_CLIENT = "curl_cffi"
CURL_IMPERSONATE = "chrome"

# ============================================================================
# GraphQL 固定参数（从 JS Bundle 动态获取，可选覆盖）
# ============================================================================
GQL_PERSIST_HASH = "cbcf4838a9b399f742e3741785df64560a826d8d3cc2828aa01ab09a8455e29e"
GQL_OPERATION_NAME = "RestaurantsAvailability"
GQL_VARIABLES_FIXED = {
    "onlyPop": False,
    "forwardDays": 0,
    "requireTimes": False,
    "requireTypes": ["Standard", "Experience", "PrivateDining"],
    "useCBR": True,
    "privilegedAccess": [
        "UberOneDiningProgram",
        "VisaDiningProgram",
        "VisaEventsProgram",
        "ChaseDiningProgram",
    ],
    "databaseRegion": "NA",
    "restaurantAvailabilityTokens": [],
    "loyaltyRedemptionTiers": [],
    "forwardMinutes": 210,
    "backwardMinutes": 210,
    "slotDiscovery": [],
    "forwardTimeslots": 0,
    "backwardTimeslots": 0,
    "gpid": 0,
}
