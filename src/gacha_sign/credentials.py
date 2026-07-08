"""凭据存储：程序自动管理的运行时凭据。

与 config.yaml 分离：config.yaml 是用户编辑的配置（手机号、密码、token 等），
credentials.json 是程序自动获取/轮换的凭据（refreshToken、uid、roleId 等）。
用户不需要关心 credentials.json 的内容。

格式（JSON）::

    {
        "tajiduo:异环账号": {"refresh_token": "...", "uid": "...", "device_id": "..."},
        "kuro:鸣潮主号": {"user_id": "...", "role_id": "..."}
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("gacha_sign")

CREDENTIALS_FILENAME = "credentials.json"


class CredentialStore:
    """按 ``platform:name`` 键存取凭据，延迟写入磁盘。"""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("读取凭据文件失败: %s", e)
                self._data = {}

    @staticmethod
    def _key(platform: str, name: str) -> str:
        return f"{platform}:{name}"

    def get(self, platform: str, name: str, key: str, default: Any = None) -> Any:
        """读取单个凭据字段。"""
        return self._data.get(self._key(platform, name), {}).get(key, default)

    def get_all(self, platform: str, name: str) -> dict[str, Any]:
        """读取某账号的全部凭据。"""
        return dict(self._data.get(self._key(platform, name), {}))

    def set(self, platform: str, name: str, key: str, value: Any) -> None:
        """写入单个凭据字段（标记 dirty，延迟保存）。"""
        k = self._key(platform, name)
        if k not in self._data:
            self._data[k] = {}
        if self._data[k].get(key) != value:
            self._data[k][key] = value
            self._dirty = True

    def save(self) -> None:
        """若有变更则写入磁盘。"""
        if not self._dirty:
            return
        try:
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False
            logger.debug("凭据已保存到 %s", self._path)
        except OSError as e:
            logger.error("保存凭据失败: %s", e)
