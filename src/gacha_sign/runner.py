"""签到调度器：遍历账号 × 平台，编排执行并汇总结果。

runner 是 CLI 与未来 astrbot 插件包装层的共同入口。它负责：
1. 为每个账号实例化对应的平台实现；
2. 校验凭证（失败则跳过签到）；
3. 调用平台 ``run_all`` 执行签到动作；
4. 汇总所有 :class:`CheckinResult`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .base import Account, CheckinResult, PlatformBase
from .config import AppConfig
from .credentials import CredentialStore
from .http import HttpClient
from .platforms import get_platform_cls

logger = logging.getLogger("gacha_sign")


@dataclass
class RunSummary:
    """一次完整运行的汇总。"""

    results: list[CheckinResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 因凭证无效等原因跳过的账号

    @property
    def success_count(self) -> int:
        # 以账号为单位：所有非跳过动作都 ok（成功/已签）视为该账号成功
        return sum(
            1 for results in self._by_account().values()
            if all(r.status.is_ok for r in results if not r.status.is_neutral)
        )

    def _by_account(self) -> dict[str, list[CheckinResult]]:
        m: dict[str, list[CheckinResult]] = {}
        for r in self.results:
            m.setdefault(f"{r.platform}/{r.account}", []).append(r)
        return m

    def format_text(self) -> str:
        """生成人类可读的汇总文本。"""
        if not self.results and not self.skipped:
            return "没有需要执行的账号。"
        lines = ["=== 签到结果汇总 ==="]
        for r in self.results:
            lines.append(f"  {r}")
        if self.skipped:
            lines.append("=== 跳过的账号 ===")
            for s in self.skipped:
                lines.append(f"  - {s}")
        ok = self.success_count
        total = len(self._by_account())
        lines.append(f"=== 成功 {ok}/{total} 个账号 ===")
        return "\n".join(lines)


def run(
    config: AppConfig,
    *,
    platform_filter: str | None = None,
    account_filter: str | None = None,
) -> RunSummary:
    """执行所有（或筛选后的）账号签到。

    :param platform_filter: 仅执行该平台（如 "kuro"）。
    :param account_filter:  仅执行该名称的账号。
    """
    summary = RunSummary()
    options = dict(config.defaults)
    accounts = config.accounts
    cred_store = CredentialStore(config.path.parent / "credentials.json")

    with HttpClient() as http:
        for acc in accounts:
            label = f"{acc.platform}/{acc.name}"
            if platform_filter and acc.platform != platform_filter.lower():
                continue
            if account_filter and acc.name != account_filter:
                continue

            cls = get_platform_cls(acc.platform)
            if cls is None:
                logger.warning("不支持的平台 %s，跳过 %s", acc.platform, label)
                summary.skipped.append(f"{label} (不支持的平台)")
                continue

            acc.bind_credentials(cred_store)
            platform: PlatformBase = cls(acc, http, options)
            try:
                logger.info("校验凭证 %s ...", label)
                from . import debug_log
                debug_log.event_log("verify_credential", platform=acc.platform, account=acc.name)
                if not platform.verify_credential():
                    logger.warning("凭证无效，跳过 %s", label)
                    debug_log.event_log("credential_invalid", platform=acc.platform, account=acc.name)
                    summary.skipped.append(f"{label} (凭证无效)")
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("校验凭证异常 %s: %s，跳过", label, e)
                debug_log.event_log("credential_error", platform=acc.platform, account=acc.name, error=str(e))
                summary.skipped.append(f"{label} (凭证校验异常: {e})")
                continue

            logger.info("开始签到 %s", label)
            debug_log.event_log("run_all_start", platform=acc.platform, account=acc.name)
            results = platform.run_all()
            debug_log.event_log(
                "run_all_done", platform=acc.platform, account=acc.name,
                actions=[{"action": r.action, "status": r.status.value, "msg": r.message} for r in results],
            )
            summary.results.extend(results)

    # 持久化：config 回填 + 凭据保存
    config.save()
    cred_store.save()
    return summary
