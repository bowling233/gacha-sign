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
    game: str = ""               # 游戏中文名（如「明日方舟」「绝区零」）
    display_platform: str = ""   # 平台中文名（如「米游社」「森空岛」）

    def __str__(self) -> str:
        tag = f"[{self.status.value}]"
        text = f"{self.platform}/{self.account} {self.action} {tag}"
        if self.reward:
            text += f" 奖励:{self.reward}"
        if self.message:
            text += f" {self.message}"
        return text


def format_results(results: list[CheckinResult]) -> str:
    """将签到结果格式化为简洁的分组列表（供 CLI 输出与插件推送共用）。

    输出示例::

        - ✅ 森空岛/森空岛主号
            - 明日方舟: 龙门币×100
            - 终末地: 至纯源石×1
        - ❌ 米游社/绝区零主号
            - 凭证失效
    """
    if not results:
        return "没有签到结果。"
    # 按 (platform, account) 分组，保留出现顺序
    groups: dict[tuple[str, str], list[CheckinResult]] = {}
    order: list[tuple[str, str]] = []
    for r in results:
        key = (r.platform, r.account)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    lines: list[str] = []
    for key in order:
        items = groups[key]
        display = items[0].display_platform or key[0]
        # 整体状态：所有非跳过项都 ok 才算成功
        meaningful = [r for r in items if not r.status.is_neutral]
        all_ok = all(r.status.is_ok for r in meaningful) if meaningful else True
        emoji = "✅" if all_ok else "❌"
        lines.append(f"- {emoji} {display}/{key[1]}")
        for r in items:
            if r.status.is_ok:
                detail = r.reward or r.message or "签到成功"
            else:
                detail = r.message or "签到失败"
            if r.game:
                lines.append(f"    - {r.game}: {detail}")
            else:
                lines.append(f"    - {detail}")
    return "\n".join(lines)


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
    """社区 APP 签到的统一接口。

    子类至少实现 ``verify_credential`` 和 ``game_signin``。
    ``bbs_checkin`` / ``bbs_tasks`` 为可选，默认跳过。
    """

    name: str = ""
    display_name: str = ""  #: 平台中文名，子类覆盖（如「米游社」「森空岛」）

    def __init__(self, account: Account, http: Any, options: dict[str, Any] | None = None):
        self.account = account
        self._http = http
        self.options = options or {}

    @abstractmethod
    def verify_credential(self) -> bool:
        """校验凭证是否有效。"""

    @abstractmethod
    def game_signin(self) -> list[CheckinResult]:
        """游戏签到（领游戏内奖励），返回每个游戏/角色的结果列表。"""

    def bbs_checkin(self) -> list[CheckinResult]:
        """社区每日签到（领社区积分），子类按需覆盖。"""
        return [self._skipped("bbs_checkin", "社区签到", "该平台暂不支持社区签到")]

    def bbs_tasks(self) -> list[CheckinResult]:
        """社区每日任务（浏览/点赞/分享等，领社区积分），子类按需覆盖。"""
        return [self._skipped("bbs_tasks", "社区任务", "该平台暂不支持社区任务")]

    #: ``run_all`` 依次执行的 (方法名, 中文标签) 列表
    _ACTIONS: list[tuple[str, str]] = [
        ("game_signin", "游戏签到"),
        ("bbs_checkin", "社区签到"),
        ("bbs_tasks", "社区任务"),
    ]

    def run_all(self) -> list[CheckinResult]:
        """依次执行所有签到动作，每个动作独立 try/except。token 失效则跳过后续。"""
        results: list[CheckinResult] = []
        for method_name, label in self._ACTIONS:
            try:
                results.extend(getattr(self, method_name)())
            except AuthExpiredError as e:
                results.append(CheckinResult(
                    platform=self.name, account=self.account.name, display_platform=self.display_name,
                    action=method_name, status=CheckinStatus.AUTH_EXPIRED, message=str(e), game=label,
                ))
                break  # token 失效，后续动作也会失败
            except CaptchaNeededError as e:
                results.append(CheckinResult(
                    platform=self.name, account=self.account.name, display_platform=self.display_name,
                    action=method_name, status=CheckinStatus.CAPTCHA_NEEDED, message=str(e), game=label,
                ))
            except Exception as e:  # noqa: BLE001
                logger.exception("%s/%s %s异常", self.name, self.account.name, label)
                results.append(CheckinResult(
                    platform=self.name, account=self.account.name, display_platform=self.display_name,
                    action=method_name, status=CheckinStatus.FAILED, message=repr(e), game=label,
                ))
        return results

    def _skipped(self, action: str, game: str, message: str = "") -> CheckinResult:
        """构造跳过结果。"""
        return CheckinResult(
            platform=self.name, account=self.account.name, action=action,
            status=CheckinStatus.SKIPPED, message=message, game=game,
            display_platform=self.display_name,
        )
