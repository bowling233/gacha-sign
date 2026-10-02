#!/usr/bin/env python3
"""下载各游戏社区 APP 最新版 APK（用于抓包模拟器安装）。

每个 APP 的「获取最新下载地址」方式不同，均在真实浏览器中逆向得出：

- 米游社：调用版本接口 getLatestPkgVer 拿到版本号，再拼接下载地址。
- 库街区：调用 webQueryVersion（GET，需 source:h5 头绕 WAF）拿带签名的 downloadUrl。
- 森空岛：读取 app-config.json，内含 android 下载直链。
- 塔吉多：抓取下载页 HTML 中的 .apk 链接（CDN 校验浏览器 UA，否则 302 到错误页）。

用法：
    uv run python scripts/download_apks.py            # 下载全部
    uv run python scripts/download_apks.py miyoushe   # 仅下载指定 APP
    uv run python scripts/download_apks.py miyoushe kuro --outdir apks
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

#: 通用浏览器 UA，规避部分 CDN 的反爬（塔吉多 CDN 会校验 UA）
BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 11; Pixel 6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
DEFAULT_HEADERS = {"User-Agent": BROWSER_UA}

#: 下载超时（APK 较大，连接 / 读取分别放宽）
TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)


# ---------------------------------------------------------------------------
# 各 APP：返回 (下载地址, 建议文件名)
# ---------------------------------------------------------------------------
def resolve_miyoushe(client: httpx.Client) -> tuple[str, str]:
    """米游社：版本接口取版本号，拼接 download-bbs 固定模板。"""
    url = "https://bbs-api.miyoushe.com/misc/wapi/getLatestPkgVer?channel=miyousheluodi"
    headers = {"Origin": "https://m.miyoushe.com", "Referer": "https://m.miyoushe.com/"}
    data = client.get(url, headers=headers).json()
    version = data["data"]["version"]
    apk_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{version}_miyousheluodi.apk"
    return apk_url, f"miyoushe_{version}.apk"


def resolve_kuro(client: httpx.Client) -> tuple[str, str]:
    """库街区：webQueryVersion 返回带 auth_key 签名的 downloadUrl（有时效，需尽快下载）。"""
    url = "https://api.kurobbs.com/config/version/webQueryVersion"
    # source:h5 + origin/referer 是绕过 WAF（code 102）的关键
    headers = {
        "source": "h5",
        "Origin": "https://www.kurobbs.com",
        "Referer": "https://www.kurobbs.com/",
    }
    data = client.get(url, headers=headers).json()["data"]
    return data["downloadUrl"], f"kurobbs_{data['versionNum']}.apk"


def resolve_skland(client: httpx.Client) -> tuple[str, str]:
    """森空岛：app-config.json 内嵌 android 下载直链。"""
    url = "https://assets.skland.com/common-config/json/app-config.json"
    data = client.get(url, headers={"x-client-app": "skland"}).json()
    return data["downloadUrl"]["android"], "skland.apk"


def resolve_tajiduo(client: httpx.Client) -> tuple[str, str]:
    """塔吉多：抓取下载页 HTML 中的 .apk 链接（CDN 校验浏览器 UA）。"""
    page = "https://www.tajiduo.com/download/index.html"
    html = client.get(page).text
    match = re.search(r'https://[^\s"\'<>]+\.apk[^\s"\'<>]*', html)
    if not match:
        raise RuntimeError("未在塔吉多下载页找到 APK 链接")
    apk_url = match.group(0)
    # 文件名形如 tgd_1.2.5_bbs_20260701.apk，直接取末段
    name = apk_url.rsplit("/", 1)[-1].split("?")[0]
    return apk_url, f"tajiduo_{name}".replace("tgd_", "")


RESOLVERS = {
    "miyoushe": resolve_miyoushe,
    "kuro": resolve_kuro,
    "skland": resolve_skland,
    "tajiduo": resolve_tajiduo,
}


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def download(client: httpx.Client, apk_url: str, dest: Path, referer: str = "") -> None:
    """流式下载 APK 到 dest，显示进度。"""
    headers = {"Referer": referer} if referer else {}
    with client.stream("GET", apk_url, headers=headers, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):  # 1 MiB
                f.write(chunk)
                written += len(chunk)
                _progress(dest.name, written, total)
        print()  # 进度换行


def _progress(name: str, written: int, total: int) -> None:
    if total:
        pct = written * 100 // total
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        print(f"\r{name} [{bar}] {pct:3d}% ({written >> 20}/{total >> 20} MiB)", end="")
    else:
        print(f"\r{name} {written >> 20} MiB", end="")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载游戏社区 APP 最新版 APK")
    parser.add_argument(
        "apps",
        nargs="*",
        default=list(RESOLVERS),
        help="要下载的 APP（默认全部）",
        choices=list(RESOLVERS),
    )
    parser.add_argument("--outdir", default="apks", help="保存目录（默认 apks）")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # referer 用于下载阶段（塔吉多 CDN 需要来源校验）
    DOWNLOAD_REFERER = {
        "miyoushe": "https://m.miyoushe.com/",
        "kuro": "https://www.kurobbs.com/",
        "skland": "https://m.skland.com/",
        "tajiduo": "https://www.tajiduo.com/download/index.html",
    }

    rc = 0
    with httpx.Client(headers=DEFAULT_HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for app in args.apps:
            print(f"\n=== {app} ===")
            try:
                apk_url, filename = RESOLVERS[app](client)
                print(f"地址: {apk_url}")
                dest = outdir / filename
                download(client, apk_url, dest, referer=DOWNLOAD_REFERER[app])
                print(f"已保存: {dest} ({dest.stat().st_size >> 20} MiB)")
            except Exception as e:  # noqa: BLE001
                print(f"✗ {app} 下载失败: {e}")
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
