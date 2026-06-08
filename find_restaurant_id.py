# find_restaurant_id.py
# 创建日期: 2026-06-07 20:32:00（北京时间 UTC+8）
# 更新日期: 2026-06-08 10:56:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: 从 OpenTable 链接自动提取 restaurantId

"""
用法:
  python find_restaurant_id.py "https://www.opentable.com/r/nobu-los-angeles-west-hollywood"
  python find_restaurant_id.py "https://www.opentable.com/booking/details?rid=19252&datetime=2026-07-01T19:00"
  python find_restaurant_id.py "https://www.opentable.com/restaurant-profile/17077"

三种识别策略:
  1. URL 直接包含 ?rid= 或 /restaurant-profile/{id}  -> 直接正则提取
  2. 页面 meta og:url / canonical                      -> GET 后解析 meta
  3. __INITIAL_STATE__ / meta er= / data-* 属性        -> GET 页面后正则匹配
"""

import re
import sys
import json

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL = True
except ImportError:
    import requests as curl_requests
    HAS_CURL = False


def extract_from_url(url: str) -> int | None:
    m = re.search(r'[?&]rid=(\d{4,8})', url)
    if m:
        return int(m.group(1))
    m = re.search(r'/restaurant-profile/(\d{4,8})', url)
    if m:
        return int(m.group(1))
    return None


def extract_from_page(url: str) -> int | None:
    session = curl_requests.Session(impersonate="chrome", verify=False)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    })

    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        print(f"  [!] 页面返回 404，该 slug 在 OpenTable 上不存在")
        return None

    text = resp.text

    m = re.search(r'"restaurantId"\s*:\s*(\d{4,8})', text)
    if m:
        return int(m.group(1))

    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
    if m:
        try:
            state = json.loads(m.group(1))
            if "restaurant" in state and "id" in state["restaurant"]:
                return int(state["restaurant"]["id"])
            if "seo" in state and "restaurantId" in state["seo"]:
                return int(state["seo"]["restaurantId"])
            if "restaurants" in state and state["restaurants"]:
                first = state["restaurants"][0]
                if isinstance(first, dict) and "id" in first:
                    return int(first["id"])
        except json.JSONDecodeError:
            pass

    m = re.search(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', text, re.I)
    if m:
        rid = extract_from_url(m.group(1))
        if rid:
            return rid

    m = re.search(r'er[=&](\d{4,8})', text)
    if m:
        return int(m.group(1))

    m = re.search(r'data-rid=["\'](\d{4,8})["\']', text)
    if m:
        return int(m.group(1))

    m = re.search(r'data-restaurant-id=["\'](\d{4,8})["\']', text, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'"@id"\s*:\s*"[^"]*?/(\d{4,8})"', text)
    if m:
        return int(m.group(1))

    for key in ("__PRELOADED_STATE__", "__APOLLO_STATE__"):
        m = re.search(re.escape(key) + r'\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
        if m:
            try:
                state = json.loads(m.group(1))
                def find_id(obj, depth=0):
                    if depth > 10:
                        return None
                    if isinstance(obj, dict):
                        if "restaurantId" in obj:
                            return int(obj["restaurantId"])
                        if "id" in obj and "urlSlug" in obj:
                            return int(obj["id"])
                        for v in obj.values():
                            r = find_id(v, depth + 1)
                            if r:
                                return r
                    elif isinstance(obj, list) and obj:
                        return find_id(obj[0], depth + 1)
                    return None
                rid = find_id(state)
                if rid:
                    return rid
            except (json.JSONDecodeError, TypeError):
                pass
    return None


def resolve_restaurant_id(url: str) -> int | None:
    print(f"URL: {url}")
    print(f"引擎: curl_cffi" if HAS_CURL else f"引擎: requests")

    rid = extract_from_url(url)
    if rid:
        print(f"策略1 [URL直接] -> restaurantId = {rid}")
        return rid

    print("策略1未命中，执行策略2 [GET页面]...")
    rid = extract_from_page(url)
    if rid:
        print(f"策略2 [页面解析] -> restaurantId = {rid}")
        return rid

    print("策略2也未命中，无法提取 restaurantId")
    return None


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python find_restaurant_id.py \"https://www.opentable.com/r/nobu-los-angeles-west-hollywood\"")
        print("  python find_restaurant_id.py \"https://www.opentable.com/booking/details?rid=19252\"")
        print("  python find_restaurant_id.py \"https://www.opentable.com/restaurant-profile/17077\"")
        sys.exit(1)

    url = sys.argv[1].strip().strip('"')
    rid = resolve_restaurant_id(url)

    print()
    print("=" * 60)
    if rid:
        print(f"restaurantId = {rid}")
        print(f"   配置示例: RESTAURANT_ID = {rid}")
        slug_m = re.search(r'/r/([^/?#]+)', url)
        if not slug_m:
            slug_m = re.search(r'^https://www\.opentable\.com/([^/?#]+)/?$', url)
        slug = slug_m.group(1) if slug_m else "<未知slug>"
        print(f"     RESTAURANT_SLUG = \"{slug}\"")
        print(f"     RESTAURANT_ID   = {rid}")
    else:
        print(f"未能提取 restaurantId，请检查 URL 是否正确")
    print("=" * 60)


if __name__ == "__main__":
    main()
