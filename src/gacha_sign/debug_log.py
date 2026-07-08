"""Debug 日志：记录每次运行的详细 HTTP 请求/响应到文件。

开启方式：config.yaml 顶层设置 ``debug: true``。
日志路径：``logs/run_YYYYMMDD_HHMMSS.log``（每次运行一个文件）。

:func:`http_log` 供 :mod:`http` 模块在每个请求后调用，仅当 debug 开启时写入。
:func:`event_log` 供业务层记录关键事件（登录、签到等）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

#: debug 是否已启用（由 setup() 设置）
_enabled: bool = False
#: 当前运行的日志文件路径
_log_file: Path | None = None
#: 专用 logger（写入文件，不受控制台 verbose 级别影响）
_logger: logging.Logger | None = None

#: 日志目录名
LOG_DIR = "logs"


def is_enabled() -> bool:
    """debug 日志是否已启用。"""
    return _enabled


def setup(config_data: dict[str, Any] | None = None, base_dir: str | Path = ".") -> Path | None:
    """根据配置初始化 debug 日志。

    :param config_data: 配置字典，读取顶层 ``debug`` 字段。
    :param base_dir: 日志目录的基准路径（logs/ 会建在此目录下）。
    :return: 若启用则返回日志文件路径，否则 None。
    """
    global _enabled, _log_file, _logger
    debug_flag = bool((config_data or {}).get("debug", False))
    if not debug_flag:
        _enabled = False
        return None

    log_dir = Path(base_dir) / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _log_file = log_dir / filename

    _logger = logging.getLogger("gacha_sign.debug")
    _logger.setLevel(logging.DEBUG)
    _logger.handlers.clear()  # 避免重复添加
    # 不冒泡到 root logger（避免控制台重复输出）
    _logger.propagate = False
    fh = logging.FileHandler(_log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S.%f"))
    _logger.addHandler(fh)

    _enabled = True
    _logger.info("=" * 60)
    _logger.info("debug 日志已启用")
    _logger.info("config debug=true, 日志文件: %s", _log_file)
    _logger.info("Python %s, pid=%s", sys_platform_info(), os.getpid())
    _logger.info("=" * 60)
    return _log_file


def sys_platform_info() -> str:
    import platform
    return f"{platform.system()} {platform.release()}"


def teardown() -> None:
    """关闭 debug 日志（刷新缓冲）。"""
    global _enabled, _log_file, _logger
    if _logger:
        for h in _logger.handlers:
            h.flush()
            h.close()
        _logger.handlers.clear()
    _enabled = False
    _log_file = None
    _logger = None


def log_file_path() -> Path | None:
    """返回当前运行的日志文件路径（未启用则 None）。"""
    return _log_file


# ---------------------------------------------------------------------------
# HTTP 请求/响应记录
# ---------------------------------------------------------------------------
def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """脱敏 headers 中的敏感字段（token/cookie/authorization 等部分掩码）。"""
    sensitive = ("cookie", "authorization", "token", "set-cookie", "stoken")
    redacted = {}
    for k, v in headers.items():
        if k.lower() in sensitive and isinstance(v, str) and len(v) > 12:
            redacted[k] = v[:8] + "***" + v[-4:]
        else:
            redacted[k] = v
    return redacted


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[截断，共{len(text)}字符]"


def http_log(
    method: str,
    url: str,
    req_headers: dict[str, str],
    req_body: Any = None,
    status_code: int | None = None,
    resp_headers: dict[str, str] | None = None,
    resp_text: str = "",
    error: str | None = None,
    attempt: int = 1,
) -> None:
    """记录一次 HTTP 请求的完整细节（仅 debug 模式生效）。"""
    if not _enabled or not _logger:
        return
    parts = [
        f"{'='*50}",
        f"[HTTP] {method} {url}  (attempt={attempt})",
    ]
    if req_headers:
        parts.append(f"  请求头: {json.dumps(_redact_headers(req_headers), ensure_ascii=False)}")
    if req_body is not None:
        body_str = req_body if isinstance(req_body, str) else json.dumps(req_body, ensure_ascii=False, default=str)
        parts.append(f"  请求体: {_truncate(body_str)}")
    if error:
        parts.append(f"  ✗ 错误: {error}")
    else:
        parts.append(f"  状态码: {status_code}")
        if resp_headers:
            # 只记录关键响应头
            key_headers = {k: v for k, v in resp_headers.items()
                           if k.lower() in ("content-type", "set-cookie")}
            if key_headers:
                # Set-Cookie 不脱敏（需要看到 cookie 名用于调试登录），但值截断
                safe = {}
                for k, v in key_headers.items():
                    if k.lower() == "set-cookie":
                        # 保留 cookie 名，值截断
                        safe[k] = v[:120] + "..." if len(v) > 120 else v
                    else:
                        safe[k] = v
                parts.append(f"  响应头(关键): {json.dumps(safe, ensure_ascii=False)}")
        if resp_text:
            parts.append(f"  响应体: {_truncate(resp_text)}")
    _logger.info("\n".join(parts))


def event_log(event: str, **details: Any) -> None:
    """记录业务事件（登录步骤、签到结果等），仅 debug 模式生效。"""
    if not _enabled or not _logger:
        return
    if details:
        _logger.info("[EVENT] %s | %s", event, json.dumps(details, ensure_ascii=False, default=str))
    else:
        _logger.info("[EVENT] %s", event)
