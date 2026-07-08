"""配置加载与多账号管理。

配置文件为单个 YAML（默认 config.yaml），顶层结构::

    debug: false
    accounts:
      - name: "账号名"
        platform: mihoyo       # mihoyo / kuro / tajiduo
        ...平台相关字段...

运行时凭据（refreshToken、roleId 等）自动存储到 credentials.json，
无需用户关心。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .base import Account

logger = logging.getLogger("gacha_sign")

#: 默认配置文件名
DEFAULT_CONFIG_NAME = "config.yaml"
#: 配置文件查找路径（按顺序）：环境变量 > 当前目录 > 脚本同级
CONFIG_SEARCH_PATHS = [
    os.environ.get("GACHA_SIGN_CONFIG_PATH"),
    DEFAULT_CONFIG_NAME,
]


class ConfigError(Exception):
    """配置加载/校验错误。"""


class AppConfig:
    """整体配置，持有 defaults 与 accounts 列表。

    通过 :meth:`save` 把账号字段的变更持久化回 YAML 文件。
    """

    def __init__(self, data: dict[str, Any], path: Path):
        self._raw = data
        self.path = path
        self.defaults: dict[str, Any] = data.get("defaults", {}) or {}
        raw_accounts = data.get("accounts") or []
        if not isinstance(raw_accounts, list):
            raise ConfigError("accounts 必须是列表")
        self.accounts: list[Account] = []
        for i, item in enumerate(raw_accounts):
            if not isinstance(item, dict):
                raise ConfigError(f"accounts[{i}] 必须是字典")
            name = str(item.get("name") or f"account-{i}")
            platform = str(item.get("platform") or "").strip()
            if not platform:
                raise ConfigError(f"accounts[{i}] 缺少 platform 字段")
            acc = Account(
                name=name,
                platform=platform,
                data=item,
                on_update=self._mark_dirty,
            )
            self.accounts.append(acc)
        self._dirty = False

    # ---- 持久化 ----
    _dirty: bool = False

    def _mark_dirty(self) -> None:
        self._dirty = True

    def save(self) -> None:
        """若配置有变更，则写回 YAML 文件。"""
        if not self._dirty:
            return
        try:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self._raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False
                )
            self._dirty = False
            logger.info("配置已保存到 %s", self.path)
        except OSError as e:
            logger.error("保存配置失败: %s", e)


def find_config_path(explicit: str | None = None) -> Path:
    """按优先级查找配置文件路径。"""
    candidates = [explicit] if explicit else []
    candidates += [p for p in CONFIG_SEARCH_PATHS if p]
    for c in candidates:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.exists():
            return p
    # 兜底：默认名（即使不存在，便于上层提示）
    return Path(explicit or DEFAULT_CONFIG_NAME)


def load_config(explicit: str | None = None) -> AppConfig:
    """加载配置文件。文件不存在时抛出 :class:`ConfigError`。"""
    path = find_config_path(explicit)
    if not path.exists():
        raise ConfigError(
            f"配置文件不存在: {path}\n"
            f"请复制 config.example.yaml 为 config.yaml 并填写账号信息。"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误，应为 YAML 字典: {path}")
    return AppConfig(data, path.resolve())
