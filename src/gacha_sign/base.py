"""平台签到抽象基类与结果模型。

每个平台只需实现 ``verify_credential`` 和 ``game_signin`` 两个方法。
新增平台时继承 ``PlatformBase`` 并在 platforms/ 注册即可。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("gacha_sign")


class CheckinStatus(Enum):
    """签到结果状态。"""

    SUCCESS = "success"
    ALREADY_SIGNED = "already_signed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CAPTCHA_NEEDED = "captcha_needed"
    AUTH_EXPIRED = "auth_expired"

    @property
    def is_ok(self) -> bool:
        """状态是否代表签到完成（成功或已签）。"""
        return self in (CheckinStatus.SUCCESS, CheckinStatus.ALREADY_SIGNED)

    @property
    def is_neutral(self) -> bool:
        """状态是否为中性（跳过），不计入成败判定。"""
        return self is CheckinStatus.SKIPPED


@dataclass
class CheckinResult:
    """游戏签到的执行结果。"""

    platform: str
    account: str
    action: str
    status: CheckinStatus
    message: str = ""
    reward: str = ""

    def __str__(self) -> str:
        tag = f"[{self.status.value}]"
        text = f"{self.platform}/{self.account} {self.action} {tag}"
        if self.reward:
            text += f" 奖励:{self.reward}"
        if self.message:
            text += f" {self.message}"
        return text


class AuthExpiredError(Exception):
    """凭证失效异常。"""


class CaptchaNeededError(Exception):
    """触发验证码异常。"""


@dataclass
class Account:
    """一个账号的配置。

    ``data`` 保存用户配置字段（config.yaml），``credentials`` 保存运行时凭据
    （credentials.json）。平台通过 ``get/set`` 读写配置，通过 ``cred_get/cred_set``
    读写凭据。
    """

    name: str
    platform: str
    data: dict[str, Any]
    on_update: Any = field(default=None, repr=False)
    _cred_store: Any = field(default=None, repr=False)

    def bind_credentials(self, store: Any) -> None:
        """绑定凭据存储（由 runner 在创建平台前调用）。"""
        self._cred_store = store

    # ---- 配置读写（config.yaml）----
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        if self.on_update is not None:
            self.on_update()

    # ---- 凭据读写（credentials.json）----
    def cred_get(self, key: str, default: Any = None) -> Any:
        if self._cred_store is None:
            return default
        return self._cred_store.get(self.platform, self.name, key, default)

    def cred_set(self, key: str, value: Any) -> None:
        if self._cred_store is not None:
            self._cred_store.set(self.platform, self.name, key, value)


class PlatformBase(ABC):
    """社区 APP 游戏签到的统一接口。"""

    name: str = ""

    def __init__(self, account: Account, http: Any, options: dict[str, Any] | None = None):
        self.account = account
        self._http = http
        self.options = options or {}

    @abstractmethod
    def verify_credential(self) -> bool:
        """校验凭证是否有效。"""

    @abstractmethod
    def game_signin(self) -> CheckinResult:
        """游戏签到（领游戏内奖励）。"""

    def run_all(self) -> list[CheckinResult]:
        """执行游戏签到，返回结果列表。"""
        try:
            result = self.game_signin()
        except AuthExpiredError as e:
            result = CheckinResult(
                platform=self.name, account=self.account.name,
                action="game_signin", status=CheckinStatus.AUTH_EXPIRED, message=str(e),
            )
        except CaptchaNeededError as e:
            result = CheckinResult(
                platform=self.name, account=self.account.name,
                action="game_signin", status=CheckinStatus.CAPTCHA_NEEDED, message=str(e),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("%s/%s 游戏签到异常", self.name, self.account.name)
            result = CheckinResult(
                platform=self.name, account=self.account.name,
                action="game_signin", status=CheckinStatus.FAILED, message=repr(e),
            )
        logger.info(str(result))
        return [result]
