"""平台注册表。

通过 ``platform`` 字段名查找对应的平台实现类，避免在 runner 里写 if/elif。
新增平台时在此 import 并注册即可。
"""

from __future__ import annotations

from typing import Callable

from ..base import PlatformBase


def _build_registry() -> dict[str, type[PlatformBase]]:
    """惰性导入各平台实现，避免某一平台依赖缺失时整体不可用。"""
    registry: dict[str, type[PlatformBase]] = {}
    try:
        from .mihoyo import MihoyoPlatform

        registry["mihoyo"] = MihoyoPlatform
    except Exception:  # noqa: BLE001
        pass
    try:
        from .kuro import KuroPlatform

        registry["kuro"] = KuroPlatform
    except Exception:  # noqa: BLE001
        pass
    try:
        from .tajiduo import TajiduoPlatform

        registry["tajiduo"] = TajiduoPlatform
    except Exception:  # noqa: BLE001
        pass
    return registry


def get_platform_cls(name: str) -> type[PlatformBase] | None:
    """按平台名获取实现类，未注册返回 None。"""
    return _build_registry().get(name.lower())


def supported_platforms() -> list[str]:
    """返回已注册的平台名列表。"""
    return sorted(_build_registry().keys())


#: 工厂类型：由 runner 使用
PlatformFactory = Callable[..., PlatformBase]
