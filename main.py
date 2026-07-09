"""AstrBot 插件入口：gacha-sign 二游社区每日签到。

本文件同时作为 AstrBot 插件入口（Star 子类）。
CLI 入口在 cli.py。

命令：
  /gacha-sign          显示子命令列表
  /gacha-sign run      手动执行签到
  /gacha-sign check    校验凭证有效性
  /gacha-sign status   查看上次签到结果
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# 将 src/ 目录加入路径，使 gacha_sign 包可被导入。
# AstrBot 安装插件时 clone 整个仓库到 data/plugins/gacha_sign/，
# 但不执行 pip install，所以需要手动定位 src/gacha_sign/。
_src_dir = os.path.join(os.path.dirname(__file__), "src")
if os.path.isdir(_src_dir) and _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.api.star import Context, Star

from gacha_sign.base import Account, CheckinResult, CheckinStatus, AuthExpiredError
from gacha_sign.http import HttpClient
from gacha_sign.platforms import get_platform_cls


class KVCredentialStore:
    """将内存字典适配为 CredentialStore 接口。

    平台通过 account.cred_get/cred_set 读写凭据时，
    实际操作的是这个内存缓存。签到完成后批量写回 AstrBot KV Store。
    """

    def __init__(self, cache: dict[str, dict[str, Any]]):
        self._cache = cache

    def get(self, platform: str, name: str, key: str, default: Any = None) -> Any:
        return self._cache.get(f"{platform}:{name}", {}).get(key, default)

    def set(self, platform: str, name: str, key: str, value: Any) -> None:
        k = f"{platform}:{name}"
        if k not in self._cache:
            self._cache[k] = {}
        self._cache[k][key] = value

    def save(self) -> None:
        """实际保存在插件层批量写入 KV Store，此处空实现。"""
        pass


class GachaSignPlugin(Star):
    """gacha-sign AstrBot 插件。"""

    def __init__(self, context: Context, config: Any):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self._cred_cache: dict[str, dict[str, Any]] = {}
        self._last_results: list[CheckinResult] = []

    async def initialize(self) -> None:
        """插件加载时：加载凭据 + 启动定时任务。"""
        await self._load_credentials()
        if self.config.get("auto_sign_enabled", True):
            hour = self.config.get("auto_sign_hour", 9)
            minute = self.config.get("auto_sign_minute", 0)
            self._start_cron(hour, minute)
        if not self.scheduler.running:
            self.scheduler.start()
        logger.info(
            f"[gacha_sign] 插件已加载，定时签到: "
            f"{self.config.get('auto_sign_hour', 9):02d}:"
            f"{self.config.get('auto_sign_minute', 0):02d}"
        )

    async def terminate(self) -> None:
        """插件卸载时：关闭定时器。"""
        if self.scheduler.running:
            self.scheduler.shutdown()

    # ---- 凭据持久化（KV Store）----
    async def _load_credentials(self) -> None:
        """从 AstrBot KV Store 加载凭据到内存。"""
        data = await self.get_kv_data("credentials", {})
        if isinstance(data, dict):
            self._cred_cache = data
        else:
            self._cred_cache = {}

    async def _save_credentials(self) -> None:
        """将内存凭据写回 AstrBot KV Store。"""
        await self.put_kv_data("credentials", self._cred_cache)

    # ---- 定时任务 ----
    def _start_cron(self, hour: int, minute: int) -> None:
        trigger = CronTrigger(hour=hour, minute=minute)
        try:
            self.scheduler.remove_job("gacha_sign_auto")
        except Exception:
            pass
        self.scheduler.add_job(
            self._auto_signin,
            trigger=trigger,
            id="gacha_sign_auto",
            misfire_grace_time=3600,
        )

    async def _auto_signin(self) -> None:
        """定时签到任务：执行签到并推送结果。"""
        logger.info("[gacha_sign] 定时签到开始")
        await self._do_signin()
        notify_session = self.config.get("notify_session", "")
        if notify_session:
            msg = self._format_results()
            try:
                umo = json.loads(notify_session)
                await self.context.send_message(umo, MessageChain().message(msg))
            except Exception as e:
                logger.warning(f"[gacha_sign] 推送失败: {e}")

    # ---- 签到核心逻辑 ----
    def _build_accounts(self) -> tuple[list[Account], KVCredentialStore]:
        """从插件配置构造 Account 列表 + 凭据存储。"""
        raw = self.config.get("accounts", "[]")
        if isinstance(raw, str):
            accounts_data = json.loads(raw)
        elif isinstance(raw, list):
            accounts_data = raw
        else:
            accounts_data = []

        cred_store = KVCredentialStore(self._cred_cache)
        accounts: list[Account] = []
        for item in accounts_data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("platform", "unknown"))
            platform = str(item.get("platform", "")).strip()
            if not platform:
                continue
            acc = Account(name=name, platform=platform, data=item)
            acc.bind_credentials(cred_store)
            accounts.append(acc)
        return accounts, cred_store

    async def _do_signin(self) -> None:
        """执行签到（定时任务和手动命令共用）。"""
        accounts, _ = self._build_accounts()
        if not accounts:
            self._last_results = []
            return

        results: list[CheckinResult] = []
        with HttpClient() as http:
            for acc in accounts:
                label = f"{acc.platform}/{acc.name}"
                cls = get_platform_cls(acc.platform)
                if cls is None:
                    logger.warning(f"[gacha_sign] 不支持的平台 {acc.platform}")
                    continue
                platform = cls(acc, http, {})
                try:
                    if not platform.verify_credential():
                        logger.warning(f"[gacha_sign] 凭证无效: {label}")
                        continue
                except Exception as e:
                    logger.warning(f"[gacha_sign] 凭证校验异常 {label}: {e}")
                    continue
                logger.info(f"[gacha_sign] 签到中: {label}")
                results.extend(platform.run_all())

        self._last_results = results
        await self._save_credentials()

    def _format_results(self) -> str:
        """格式化签到结果为可读文本。"""
        if not self._last_results:
            return "暂无签到结果。使用 /gacha-sign run 执行签到。"
        lines = ["=== gacha-sign 签到结果 ==="]
        for r in self._last_results:
            lines.append(f"  {r}")
        ok = sum(1 for r in self._last_results if r.status.is_ok)
        lines.append(f"=== {ok}/{len(self._last_results)} 成功 ===")
        return "\n".join(lines)

    # ---- 命令处理 ----
    @filter.command_group("gacha-sign")
    def gacha_sign_group(self):
        """gacha-sign 二游签到"""
        pass

    @gacha_sign_group.command("run")
    async def cmd_run(self, event: AstrMessageEvent):
        """手动执行签到"""
        yield event.plain_result("正在执行签到...")
        await self._do_signin()
        yield event.plain_result(self._format_results())

    @gacha_sign_group.command("check")
    async def cmd_check(self, event: AstrMessageEvent):
        """校验凭证有效性"""
        accounts, _ = self._build_accounts()
        if not accounts:
            yield event.plain_result("未配置任何账号。")
            return
        lines = ["=== 凭证校验 ==="]
        with HttpClient() as http:
            for acc in accounts:
                label = f"{acc.platform}/{acc.name}"
                cls = get_platform_cls(acc.platform)
                if cls is None:
                    lines.append(f"  ✗ {label} 不支持的平台")
                    continue
                platform = cls(acc, http, {})
                try:
                    ok = platform.verify_credential()
                except AuthExpiredError as e:
                    ok = False
                    lines.append(f"  ✗ {label} 凭证失效: {e}")
                    continue
                except Exception as e:
                    ok = False
                    lines.append(f"  ✗ {label} 校验异常: {e}")
                    continue
                mark = "✓" if ok else "✗"
                lines.append(f"  {mark} {label}")
        await self._save_credentials()
        yield event.plain_result("\n".join(lines))

    @gacha_sign_group.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看上次签到结果"""
        yield event.plain_result(self._format_results())
