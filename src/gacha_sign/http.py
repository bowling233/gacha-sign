"""统一的 HTTP 客户端封装。

所有平台通过 ``HttpClient`` 发起请求，返回标准化的 :class:`ApiResponse`。
封装了超时、重试、UA、JSON 解析等公共逻辑，平台层无需关心底层细节。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger("gacha_sign")

#: 默认请求超时（秒）
DEFAULT_TIMEOUT = 30.0
#: 默认重试次数（针对网络错误 / 5xx / 429）
DEFAULT_RETRIES = 3
#: 重试基础退避（秒），实际退避 = base * (attempt) + 随机抖动
RETRY_BACKOFF = 2.0


class ApiResponse:
    """统一的响应包装，屏蔽 httpx.Response 的细节。

    业务字段通过 ``code`` / ``message`` / ``data`` 访问，这三者是各平台
    最常见的返回结构。平台按需自行从 :attr:`raw_json` 读取其它字段。
    """

    def __init__(self, response: httpx.Response):
        self.raw = response
        self.status_code: int = response.status_code
        self.text: str = response.text
        self.raw_json: Any = None
        try:
            self.raw_json = response.json()
        except (ValueError, httpx.DecodingError):
            self.raw_json = None
        # 标准化业务字段：优先常见的 data/retcode、其次 data/code、再次顶层 code
        self.code: int | None = self._extract_code()
        self.message: str = self._extract_message()
        self.data: Any = self.raw_json.get("data") if isinstance(self.raw_json, dict) else None

    def _extract_code(self) -> int | None:
        j = self.raw_json
        if not isinstance(j, dict):
            return None
        for key in ("retcode", "code"):
            v = j.get(key)
            if isinstance(v, int):
                return v
        inner = j.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("code"), int):
            return inner["code"]
        return None

    def _extract_message(self) -> str:
        j = self.raw_json
        if not isinstance(j, dict):
            return self.text[:200] if self.text else ""
        for key in ("message", "msg"):
            v = j.get(key)
            if isinstance(v, str) and v:
                return v
        inner = j.get("data")
        if isinstance(inner, dict):
            for key in ("message", "msg"):
                v = inner.get(key)
                if isinstance(v, str) and v:
                    return v
        return ""

    @property
    def is_http_ok(self) -> bool:
        return 200 <= self.status_code < 300

    def __repr__(self) -> str:
        return f"ApiResponse(status={self.status_code}, code={self.code}, msg={self.message!r})"


class HttpClient:
    """基于 httpx 的同步客户端，支持自定义 headers 与自动重试。

    平台通过 :meth:`get` / :meth:`post_form` / :meth:`post_json` 发起请求。
    每次调用可传入 ``headers`` 覆盖默认头、``params``/``data``/``json`` 作为负载。
    """

    def __init__(
        self,
        default_headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.default_headers: dict[str, str] = default_headers or {}
        self.retries = max(0, retries)

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        merged = dict(self.default_headers)
        if headers:
            merged.update(headers)
        return merged

    def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> ApiResponse:
        kwargs.setdefault("headers", {})
        kwargs["headers"] = self._merge_headers(kwargs["headers"] or None)
        # 提取请求负载用于 debug 日志
        req_body = kwargs.get("data") or kwargs.get("json") or kwargs.get("content")
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                # 429 / 5xx 触发重试
                if resp.status_code in (429, *range(500, 600)) and attempt < self.retries:
                    from . import debug_log
                    debug_log.http_log(
                        method, url, kwargs["headers"], req_body,
                        status_code=resp.status_code, resp_text=resp.text,
                        error=f"触发重试({resp.status_code})", attempt=attempt,
                    )
                    self._sleep_backoff(attempt)
                    continue
                api_resp = ApiResponse(resp)
                from . import debug_log
                debug_log.http_log(
                    method, url, kwargs["headers"], req_body,
                    status_code=resp.status_code,
                    resp_headers=dict(resp.headers),
                    resp_text=resp.text, attempt=attempt,
                )
                return api_resp
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                from . import debug_log
                debug_log.http_log(
                    method, url, kwargs["headers"], req_body,
                    error=f"{type(e).__name__}: {e}", attempt=attempt,
                )
                if attempt < self.retries:
                    logger.warning("请求 %s 失败(第%d次): %s，重试中", url, attempt, e)
                    self._sleep_backoff(attempt)
                    continue
                raise
        # 理论上不会到达
        if last_exc:
            raise last_exc
        raise RuntimeError(f"请求 {url} 重试耗尽")

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(RETRY_BACKOFF * attempt + random.uniform(0, 1))

    def get(self, url: str, **kwargs: Any) -> ApiResponse:
        return self._request_with_retry("GET", url, **kwargs)

    def post_form(self, url: str, data: Any = None, **kwargs: Any) -> ApiResponse:
        """以 application/x-www-form-urlencoded 提交表单。"""
        kwargs.setdefault("headers", {})
        # 仅当调用方未设置任何 content-type 时才补充默认值（大小写不敏感）
        existing_ct = any(k.lower() == "content-type" for k in kwargs["headers"])
        if not existing_ct:
            kwargs["headers"]["Content-Type"] = "application/x-www-form-urlencoded"
        return self._request_with_retry("POST", url, data=data, **kwargs)

    def post_json(self, url: str, json_body: Any = None, **kwargs: Any) -> ApiResponse:
        """以 application/json 提交。"""
        return self._request_with_retry("POST", url, json=json_body, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
