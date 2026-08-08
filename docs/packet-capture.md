# 模拟器抓包

本文档记录获取社区 APP、在模拟器中抓包所需的**非通用**信息。
Android Studio / SDK / AVD 的安装与创建属通用操作，此处不再赘述。

## 下载社区 APP

项目自带下载脚本，会自动获取各 APP 最新版并保存到 `apks/`：

```bash
uv run python scripts/download_apks.py            # 全部
uv run python scripts/download_apks.py kuro skland  # 指定 APP
```

各 APP 的「最新版地址」获取方式（逆向自官方下载页，实现见 `scripts/download_apks.py`）：

| APP | 获取方式 | 关键点 |
| --- | --- | --- |
| 米游社 | `getLatestPkgVer` 版本接口取版本号，拼接 `download-bbs` 固定模板 | 模板为 `mihoyobbs_`（非 `miyoyobbs_`） |
| 库街区 | `webQueryVersion` 接口返回带 `auth_key` 签名的直链 | 必须 **GET** + `source: h5` 头，否则被 WAF 拦截（code 102） |
| 森空岛 | 读取 `app-config.json`，内含 android 下载直链 | 直链稳定，无需拼接 |
| 塔吉多 | 抓取下载页 HTML 中的 `.apk` 链接 | CDN 校验浏览器 User-Agent，非浏览器 UA 会 302 到错误页 |

> 库街区直链含有时效性 `auth_key`，下载脚本会在获取后立即下载。

## 模拟器与代理准备

需要一个 **可 root** 的模拟器（Android 11 Google APIs 镜像，非 Google Play 版）。
启动时加 `-writable-system` 以便将 mitmproxy CA 安装为系统证书；
代理指向宿主机 `10.0.2.2:8888`。这些都是标准流程，不在此展开。

下载好的 APK 通过 `adb install apks/xxx.apk` 装入模拟器即可。

## 抓包分析

> **状态：待补充。** 以下为各 APP 抓包后的凭证 / 注册登录机制分析，需标注实际测试的 APP 版本。
> 本项目需抓取的凭证：米游社 cookie、库街区 token。

### 米游社（绝区零）

- 测试版本：*待填*
- 凭证：cookie（含 `cookie_token_v2` 等 v2 字段）
- 抓取要点：*待填*

### 库街区（鸣潮）

- 测试版本：*待填*
- 凭证：token（`eyJ...` JWT，有效期约 30 天）
- 抓取要点：*待填*

### 塔吉多（异环）

- 测试版本：*待填*
- 凭证：手机号 + 密码（全自动登录，无需抓包）
