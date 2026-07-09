#!/usr/bin/env python3
"""多游戏社区签到脚本 CLI 入口。

用法：
    uv run python main.py run                           # 执行所有账号签到
    uv run python main.py run --platform kuro            # 仅执行库街区账号
    uv run python main.py run --account "鸣潮主号"        # 仅执行指定账号
    uv run python main.py check                          # 校验所有账号凭证是否有效
"""

from __future__ import annotations

import argparse
import logging
import sys

from gacha_sign import debug_log
from gacha_sign.config import ConfigError, load_config
from gacha_sign.credentials import CredentialStore
from gacha_sign.notify import send as notify_send
from gacha_sign.runner import run as run_all


def setup_logging(verbose: bool = False, config_data: dict | None = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    debug_log.setup(config_data)


def _teardown_debug() -> None:
    debug_log.teardown()


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    summary = run_all(
        config,
        platform_filter=args.platform,
        account_filter=args.account,
    )
    notify_send(summary, config.defaults.get("push"))
    failed = [r for r in summary.results if r.status.value == "failed"]
    return 1 if failed else 0


def cmd_check(args: argparse.Namespace) -> int:
    """校验所有账号凭证。不执行任何签到动作。"""
    from gacha_sign.base import AuthExpiredError
    from gacha_sign.http import HttpClient
    from gacha_sign.platforms import get_platform_cls

    config = load_config(args.config)
    cred_store = CredentialStore(config.path.parent / "credentials.json")
    all_ok = True
    with HttpClient() as http:
        for acc in config.accounts:
            label = f"{acc.platform}/{acc.name}"
            if args.platform and acc.platform != args.platform.lower():
                continue
            cls = get_platform_cls(acc.platform)
            if cls is None:
                print(f"  ✗ {label} 不支持的平台")
                all_ok = False
                continue
            acc.bind_credentials(cred_store)
            platform = cls(acc, http, dict(config.defaults))
            try:
                ok = platform.verify_credential()
            except AuthExpiredError as e:
                ok = False
                print(f"  ✗ {label} 凭证失效: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ✗ {label} 校验异常: {e}")
            else:
                mark = "✓" if ok else "✗"
                print(f"  {mark} {label}")
            all_ok = all_ok and ok
    config.save()
    cred_store.save()
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gacha-sign",
        description="多游戏社区 APP 自动签到（米游社 / 库街区 / 塔吉多）",
    )
    parser.add_argument("-c", "--config", default=None, help="配置文件路径（默认 config.yaml）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")

    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="执行签到")
    p_run.add_argument("--platform", default=None, help="仅执行该平台 (mihoyo/kuro/tajiduo)")
    p_run.add_argument("--account", default=None, help="仅执行该名称的账号")
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check", help="校验所有账号凭证")
    p_check.add_argument("--platform", default=None, help="仅校验该平台")
    p_check.set_defaults(func=cmd_check)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    try:
        try:
            from gacha_sign.config import find_config_path
            import yaml
            cfg_path = find_config_path(args.config)
            if cfg_path.exists():
                with cfg_path.open("r", encoding="utf-8") as f:
                    cfg_data = yaml.safe_load(f) or {}
                setup_logging(args.verbose, cfg_data)
                log_path = debug_log.log_file_path()
                if log_path:
                    print(f"📋 debug 日志已启用: {log_path}")
        except Exception:  # noqa: BLE001
            pass

        return args.func(args)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    finally:
        _teardown_debug()


if __name__ == "__main__":
    sys.exit(main())
