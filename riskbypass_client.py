# riskbypass_client.py
# 创建日期: 2026-06-07 13:07:00（北京时间 UTC+8）
# 更新日期: 2026-06-07 13:20:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: RiskByPass Akamai _abck Cookie 生成客户端封装

"""
RiskByPass 第三方 abck 生成模块

核心原理:
  RiskByPass 通过真实浏览器环境执行 OpenTable 的 Akamai JS challenge，
  收集 Canvas/WebGL/Audio 等浏览器指纹，生成有效的 _abck cookie。
"""

import json
import time
import re
import html as htmlmod

import config
from logger_setup import debug
logger = debug()


class AbckGenerator:
    def __init__(self, api_url=None, api_token=None, proxy=None, timeout=None):
        self.api_url = api_url or config.RISKBASE_URL
        self.api_token = api_token or config.RISK_TOKEN
        self.proxy = proxy or config.PROXY
        self.timeout = timeout or config.RISK_TIMEOUT
        self._last_result = None
        self._abck_count = 0

    def check_balance(self):
        from riskbypass import RiskByPassClient
        client = RiskByPassClient(token=self.api_token, base_url=self.api_url)
        return client.check_balance()

    def get_abck(self, target_url, akamai_js_url=None, page_fp=None, proxy=None):
        try:
            from riskbypass import RiskByPassClient
        except ImportError:
            return {
                "status": "error",
                "error": "riskbypass 未安装，请运行: pip install riskbypass",
                "raw": None,
            }

        rb_client = RiskByPassClient(
            token=self.api_token,
            base_url=self.api_url,
        )

        proxy = proxy or self.proxy

        payload = {
            "task_type": "akamai",
            "target_url": target_url,
            "akamai_js_url": akamai_js_url or config.AKAMAI_JS_URL,
            "page_fp": page_fp or config.PAGE_FP,
        }
        if proxy:
            payload["proxy"] = proxy

        logger.debug("RiskByPass 提交任务: %s", target_url)

        try:
            results = rb_client.run_task(payload=payload, timeout=self.timeout)
        except Exception as e:
            return {
                "status": "error",
                "error": f"RiskByPass 调用异常: {e}",
                "raw": None,
            }

        cookies_dict = results.get("cookies_dict", {})
        abck = cookies_dict.get("_abck", "")

        if not abck:
            return {
                "status": "error",
                "error": "RiskByPass 未返回 _abck cookie",
                "raw": results,
            }

        self._last_result = results
        self._abck_count += 1

        return {
            "status": "success",
            "abck": abck,
            "ak_bmsc": cookies_dict.get("ak_bmsc"),
            "bm_sz": cookies_dict.get("bm_sz"),
            "bm_s": cookies_dict.get("bm_s"),
            "expires_in": 3600,
            "error": None,
            "raw": results,
        }

    def get_abck_with_detection(self, target_url, proxy=None):
        import requests

        logger.debug("RiskByPass 探测目标页面参数...")
        session = requests.Session()
        session.verify = False
        session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        })

        try:
            resp = session.get(target_url, timeout=30)
        except Exception as e:
            return {
                "status": "error",
                "error": f"页面探测失败: {e}",
                "abck": None,
            }

        js_urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', resp.text)
        akamai_js_url = None
        for u in js_urls:
            if "/wt/vk/" in u:
                decoded = htmlmod.unescape(u)
                if decoded.startswith("//"):
                    decoded = "https:" + decoded
                elif decoded.startswith("/"):
                    decoded = "https://www.opentable.com" + decoded
                akamai_js_url = decoded
                break

        page_fp = None
        fp_match = re.search(
            r'name="akamai-js-client-tag"\s+content="([^"]+)"',
            resp.text
        )
        if not fp_match:
            fp_match = re.search(r'"page_fp"\s*:\s*"([^"]+)"', resp.text)
        if fp_match:
            page_fp = fp_match.group(1)

        if not akamai_js_url:
            return {
                "status": "error",
                "error": "无法从页面提取 akamai_js_url",
                "abck": None,
            }

        logger.debug("RiskByPass 检测到 akamai_js_url: %s...", akamai_js_url[:80])

        return self.get_abck(
            target_url,
            akamai_js_url=akamai_js_url,
            page_fp=page_fp,
            proxy=proxy,
        )
