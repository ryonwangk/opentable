# logger_setup.py
# 创建日期: 2026-06-07 19:58:00（北京时间 UTC+8）
# 更新日期: 2026-06-07 20:10:00（北京时间 UTC+8）
# 使用模型: Claude Opus 4 (claude-opus-4-8-thinking-high)
# 用途说明: OT_Nobu 项目统一日志配置

"""
日志设计
  debug logger（console DEBUG+）  -> 控制台：所有步骤、Cookie、请求详情
  result logger（file INFO+）      -> 文件+控制台：单行结果

文件格式: logs/YYYY-MM-DD.txt（仅单行 INFO，无其他级别）
"""

import logging
import os
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")

_initialized = False


def setup():
    """
    配置双 logger

    debug_logger:   控制台 DEBUG+
    result_logger:  控制台+文件 INFO+
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.makedirs(_LOG_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(_LOG_DIR, f"{today}.txt")

    # --- 文件 logger（仅 INFO+，写文件） ---
    result_logger = logging.getLogger("result")
    result_logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(message)s"))
    result_logger.addHandler(fh)

    # 文件 logger 也输出到控制台
    ch_result = logging.StreamHandler(sys.stdout)
    ch_result.setLevel(logging.INFO)
    ch_result.setFormatter(logging.Formatter("%(message)s"))
    result_logger.addHandler(ch_result)

    # --- debug logger（DEBUG+，仅控制台） ---
    debug_logger = logging.getLogger("debug")
    debug_logger.setLevel(logging.DEBUG)
    ch_debug = logging.StreamHandler(sys.stdout)
    ch_debug.setLevel(logging.DEBUG)
    ch_debug.setFormatter(logging.Formatter("%(message)s"))
    debug_logger.addHandler(ch_debug)

    # 关闭 root logger 的默认 handler（避免重复）
    logging.getLogger().setLevel(logging.CRITICAL)
    logging.getLogger().handlers.clear()


def debug() -> logging.Logger:
    """DEBUG logger（仅控制台）"""
    return logging.getLogger("debug")


def result() -> logging.Logger:
    """INFO logger（文件+控制台，单行结果）"""
    return logging.getLogger("result")
