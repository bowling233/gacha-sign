"""结果通知。

首版仅打印到控制台。预留 :func:`send` 接口，后续可接入 Bark /
Server酱 / Telegram 等推送渠道（参考各 reference 仓库的 push 实现）。
"""

from __future__ import annotations

import logging

from .runner import RunSummary

logger = logging.getLogger("gacha_sign")


def send(summary: RunSummary, push_config: dict | None = None) -> None:
    """输出签到汇总。

    :param summary: 运行汇总。
    :param push_config: 预留推送配置（首版未使用）。
    """
    text = summary.format_text()
    print()
    print(text)

    if push_config and push_config.get("enable"):
        logger.info("推送功能尚未实现，仅打印结果。")
