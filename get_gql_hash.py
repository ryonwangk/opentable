# get_gql_hash.py
# 创建日期: 2026-06-07 19:35:00（北京时间 UTC+8）
# 更新日期: 2026-06-07 19:58:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: 动态从 OpenTable JS Bundle 中提取 RestaurantsAvailability 的 documentId

"""
原理:
  OpenTable 前端将 GraphQL query 编译为 AST，以 import chunk 形式加载。
  chunk 文件名固定路径: /static/cfe/<ver>/js/chunk-<name>.js
  chunk 导出时附带的 documentId: e.documentId="<64-hex>";
  这就是 GraphQL persisted query 的 SHA256 hash。
"""

import re
import config
from logger_setup import debug

logger = debug()

_cached_hash: str | None = None

_CHUNK_PATTERNS = [
    "https://www.opentable.com/static/cfe/15/js/chunk-JJJZJ73O.js",
]


def _extract_doc_id_from_chunk(chunk_text: str) -> str | None:
    m = re.search(r'e\.documentId="([a-f0-9]{64})"', chunk_text)
    if m:
        return m.group(1)
    m = re.search(r'\.documentId="([a-f0-9]{64})"', chunk_text)
    if m:
        return m.group(1)
    return None


def _extract_chunk_url_from_bundle(bundle_text: str) -> str | None:
    imports = re.findall(r'import\(["\']([^"\']+)["\']\)', bundle_text)
    for imp in imports:
        if 'chunk' in imp.lower():
            if imp.startswith('/'):
                return f"https://www.opentable.com{imp}"
            return imp
    return None


def fetch_gql_hash(http, cookies: dict = None) -> str:
    global _cached_hash
    if _cached_hash:
        logger.debug("GQL Hash 使用缓存: %s", _cached_hash)
        return _cached_hash

    page_url = config.RESTAURANT_URL

    # = 策略1: 直接请求已知 chunk URL =
    for chunk_url in _CHUNK_PATTERNS:
        try:
            logger.debug("策略1: GET %s", chunk_url)
            resp = http.get(
                chunk_url,
                headers={"Referer": page_url, "Origin": "https://www.opentable.com", "User-Agent": config.USER_AGENT},
                cookies=cookies or {},
            )
            if resp.status_code == 200:
                doc_id = _extract_doc_id_from_chunk(resp.text)
                if doc_id:
                    _cached_hash = doc_id
                    logger.debug("策略1 成功，documentId: %s", doc_id)
                    return doc_id
        except Exception as e:
            logger.debug("策略1 失败: %s", e)

    # = 策略2: 先获取主 bundle，再找 chunk URL =
    logger.debug("策略2: 获取主 bundle 定位 chunk")
    try:
        resp = http.get(
            page_url,
            headers={"Accept": "text/html,application/xhtml+xml,*/*", "User-Agent": config.USER_AGENT},
            cookies=cookies or {},
        )
        html = resp.text
        m = re.search(r'/static/cfe/(\d+)/js/restprofilepage-([A-Za-z0-9_=-]+)\.js', html)
        if not m:
            chunks = re.findall(r'/static/cfe/\d+/js/chunk-([A-Za-z0-9_-]+)\.js', html)
            for chunk_name in set(chunks):
                chunk_url = f"https://www.opentable.com/static/cfe/15/js/chunk-{chunk_name}.js"
                try:
                    cr = http.get(chunk_url, headers={"Referer": page_url, "Origin": "https://www.opentable.com", "User-Agent": config.USER_AGENT}, cookies=cookies or {}, timeout=10)
                    if cr.status_code == 200 and 'RestaurantsAvailability' in cr.text:
                        doc_id = _extract_doc_id_from_chunk(cr.text)
                        if doc_id:
                            _cached_hash = doc_id
                            logger.debug("策略2 扫描 chunk 成功: %s", doc_id)
                            return doc_id
                except Exception:
                    continue
        else:
            version = m.group(1)
            bundle_url = f"https://www.opentable.com/static/cfe/{version}/js/restprofilepage-{m.group(2)}.js"
            logger.debug("主 Bundle: %s", bundle_url)
            br = http.get(bundle_url, headers={"Referer": page_url, "Origin": "https://www.opentable.com", "User-Agent": config.USER_AGENT}, cookies=cookies or {}, timeout=15)
            if br.status_code == 200:
                chunk_path = _extract_chunk_url_from_bundle(br.text)
                if chunk_path:
                    logger.debug("Chunk 路径: %s", chunk_path)
                    cr = http.get(chunk_path, headers={"Referer": bundle_url, "Origin": "https://www.opentable.com", "User-Agent": config.USER_AGENT}, cookies=cookies or {}, timeout=10)
                    if cr.status_code == 200:
                        doc_id = _extract_doc_id_from_chunk(cr.text)
                        if doc_id:
                            _cached_hash = doc_id
                            logger.debug("策略2 bundle 定位成功: %s", doc_id)
                            return doc_id
    except Exception as e:
        logger.debug("策略2 失败: %s", e)

    # = 策略3: 扫描 cfe/15/chunk-XXXX.js =
    logger.debug("策略3: 扫描 cfe/15 下 chunks")
    for i in range(30):
        chunk_url = f"https://www.opentable.com/static/cfe/15/js/chunk-{i:04d}.js"
        try:
            cr = http.get(chunk_url, headers={"Referer": page_url, "Origin": "https://www.opentable.com", "User-Agent": config.USER_AGENT}, cookies=cookies or {}, timeout=5)
            if cr.status_code == 200 and 'RestaurantsAvailability' in cr.text:
                doc_id = _extract_doc_id_from_chunk(cr.text)
                if doc_id:
                    _cached_hash = doc_id
                    logger.debug("策略3 扫描成功 (尝试 %d): %s", i, doc_id)
                    return doc_id
        except Exception:
            continue

    raise RuntimeError("无法从 JS Bundle 提取 GQL documentId")


def clear_cache():
    global _cached_hash
    _cached_hash = None
