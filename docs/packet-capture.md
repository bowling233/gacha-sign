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

### 镜像选择

使用 **API 30 (Android 11) google_apis x86_64** 镜像。原因：

- **自带 ARM 翻译**（`libndk_translation.so`）：这些社区 APP 均只含 arm64 原生库，x86_64 镜像靠翻译层运行。API 33+ 的 google_apis x86_64 镜像**不含** ARM 翻译，安装 arm64 APK 会报 `INSTALL_FAILED_NO_MATCHING_ABIS`。
- **使用旧版证书目录** `/system/etc/security/cacerts/`：API 34 (Android 14) 将证书目录迁移到 `/apex/com.android.conscrypt/cacerts`（只读 APEX），安装证书需要 Magisk + tmpfs bind-mount，复杂且容易导致启动失败。

> **不要用** API 33 google_apis（无 ARM 翻译）或 API 34（APEX 证书目录）。

### 安装 mitmproxy CA 证书（关键，易出错）

#### Android 14+（真机推荐方案：Magisk + AlwaysTrustUserCerts）

Android 14 将系统证书目录从 `/system/etc/security/cacerts/` 迁移到
`/apex/com.android.conscrypt/cacerts/`（只读 APEX），直接写入或 tmpfs 挂载
都会被 SELinux 阻止。推荐使用 **[AlwaysTrustUserCerts](https://github.com/NVISOsecurity/AlwaysTrustUserCerts)**
Magisk 模块，它通过 `nsenter` 将证书注入每个 APP 进程的挂载命名空间。

```bash
# 1. 手机需已安装 Magisk（解锁 Bootloader → patch boot.img → fastbootd 刷入）
# 2. 在 Magisk 设置中启用 Zygisk，重启
# 3. 将 mitmproxy CA 作为用户证书安装
adb push ~/.mitmproxy/mitmproxy-ca-cert.pem /data/local/tmp/mitm_ca.pem
adb shell su -c "mkdir -p /data/misc/user/0/cacerts-added && \
  cp /data/local/tmp/mitm_ca.pem /data/misc/user/0/cacerts-added/c8750f0d.0 && \
  chmod 644 /data/misc/user/0/cacerts-added/c8750f0d.0"
# 4. 安装 AlwaysTrustUserCerts 模块并重启
adb push AlwaysTrustUserCerts_v1.3.zip /data/local/tmp/
adb shell su -c "magisk --install-module /data/local/tmp/AlwaysTrustUserCerts_v1.3.zip"
adb reboot
```

模块日志在 `/data/adb/modules/trustusercerts/log.txt`，应显示 `Injecting into zygote` 和各子进程。

> **适用场景**：真机（Redmi K40s / Pixel Experience Android 14 / Magisk 30.7 实测通过）。
> 也适用于 API 34+ 的模拟器（需 Magisk）。
> **Redmi K40s 刷入 Magisk 注意**：bootloader fastboot 有下载大小限制，
> 需用 `adb reboot fastboot` 进入 fastbootd 模式后 `fastboot flash boot_b` 刷入。

#### Android 11-13（模拟器方案：avbctl + writable-system）

```bash
emulator -avd gacha_sign -writable-system -no-snapshot

adb root
adb shell avbctl disable-verification   # 正确方式；不要用 adb disable-verity
adb reboot                               # 此重启正常存活
adb wait-for-device

adb root
adb remount                              # 现在 /system 可写

CERT=~/.mitmproxy/mitmproxy-ca-cert.pem
HASH=$(openssl x509 -inform PEM -subject_hash_old -in "$CERT" | head -1)
adb push "$CERT" /sdcard/mitm_ca.pem
adb shell "cp /sdcard/mitm_ca.pem /system/etc/security/cacerts/${HASH}.0"
adb shell "chmod 644 /system/etc/security/cacerts/${HASH}.0"
```

安装后 `ls -Z` 应显示 SELinux context 为 `u:object_r:system_security_cacerts_file:s0`。

> **常见陷阱**：
> - 用 `adb disable-verity` → overlayfs 重启卡死（必须冷重启 + wipe-data 恢复）
> - 用 tmpfs 挂载覆盖证书目录 → SELinux context 错误（`appdomain_tmpfs`），APP 无法读取证书
> - API 33 google_apis x86_64 → **不含 ARM 翻译**，arm64 APK 报 `INSTALL_FAILED_NO_MATCHING_ABIS`
> - 模拟器上的 tmpfs 运行时挂载 → 不传播到 APP 挂载命名空间（Android 10+ 隔离）
> - Android 14 → 证书在 APEX 只读分区，必须用 AlwaysTrustUserCerts 模块

### 设置代理

```bash
# 启动 mitmproxy（在单独的终端中）
mitmdump -p 8888 -w /tmp/capture.flows

# 设置模拟器代理（指向宿主机）
adb shell settings put global http_proxy 10.0.2.2:8888

# 完成后清除
adb shell settings put global http_proxy :0
```

下载好的 APK 通过 `adb install apks/xxx.apk` 装入模拟器。

## 抓包分析

> 本项目需抓取的凭证：米游社 cookie、库街区 token。
> 森空岛、塔吉多为手机号+密码全自动登录，无需抓包。

### 库街区（鸣潮）

- 测试版本：v3.1.3
- 凭证：token（`eyJ...` JWT，有效期约 30 天）
- 登录方式：手机号 + 短信验证码（需 GeeTest 人机验证，无法全自动）
- 抓取要点：登录后从 `POST /user/sdkLogin` 响应中提取 `token`，或从任意已登录请求的 `token` 头获取

所有 API 请求共用头部：`source: android`、`version: 3.1.3`、`token: <JWT>`、
`devCode: <32位hex>`、`Content-Type: application/x-www-form-urlencoded`。
游戏签到接口额外需要 WebView 头部（`Origin: https://web-static.kurobbs.com`、
`Referer`、WebView `User-Agent`、`X-Requested-With: com.kurogame.kjq`）。

#### game_checkin（游戏签到 — 鸣潮 gameId=3）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `POST /user/role/findRoleList` | gameId=3 | 获取游戏角色 |
| `POST /encourage/signIn/initSignInV2` | gameId, serverId, roleId, userId | 签到状态（`isSigIn`、`omissionNnm` 漏签天数） |
| `POST /encourage/signIn/v2` | gameId, serverId, roleId, userId, reqMonth | **执行签到** → 游戏内奖励 |
| `POST /encourage/signIn/repleSigInV2` | gameId, serverId, roleId, userId | 补签（消耗 200 社区金币/次，每月最多 3 次） |

#### bbs_checkin（社区签到 — gameId=2）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `POST /user/signIn/info` | gameId=2 | 签到状态（`hasSignIn`、`continueDays`） |
| `POST /user/signIn` | gameId=2 | **执行签到** → 30 社区金币（无需 GeeTest） |

#### bbs_tasks（社区每日任务）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `POST /forum/getPostDetail` | postId | 浏览帖子（×3 → 20 金币） |
| `POST /forum/like` | forumId, gameId, postId, likeType=1, operateType=1 | 点赞帖子（×5 → 20 金币） |
| `POST /encourage/level/shareTask` | gameId, postId | 分享帖子（×1 → 10 金币） |
| `POST /encourage/level/getTaskProcess` | gameId=0, userId | 查询任务进度 |
| `POST /encourage/gold/getTotalGold` | — | 查询社区金币余额 |

### 米游社（绝区零 / 原神）

- 测试版本：v2.112.0（真机 Redmi K40s 抓包 + MihoyoBBSTools 参考）
- 凭证：cookie（含 `cookie_token_v2` 等 v2 字段）+ stoken（社区接口必需）
- stoken 获取：密码登录 → `checkRiskVerified` 返回 token(type=1) → `exchange` 换取 stoken(type=2)；
  或从登录后的 BBS 请求 cookie 中提取 `stoken` 字段

所有接口需要 **DS（Dynamic Secret）** 签名。游戏签到用简单 DS（`md5(salt&ts&rand)`，
salt=`47f15f1b66bee46b816115d8e8e6ebb6`）；社区签到用 DS2（`md5(salt&ts&rand&body&query)`，
salt=`t0qEgfub6cvueAPgR5m9aQWWVciEer7v`）。

#### game_checkin（游戏签到）

| 游戏 | game_biz | act_id | API 路径 | signgame |
| --- | --- | --- | --- | --- |
| 绝区零 | `nap_cn` | `e202406242138391` | `act-nap-api.mihoyo.com/event/luna/zzz/{info,sign}` | `zzz` |
| 原神 | `hk4e_cn` | `e202311201442471` | `api-takumi.mihoyo.com/event/luna/hk4e/{info,sign}` | `hk4e` |

> 原神签到必须带 `x-rpc-signgame: hk4e` 头和 DS 签名，否则返回 `retcode=-500012`。

#### bbs_checkin（社区签到）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /apihub/wapi/getUserMissionsState` | `?point_sn=myb` | 任务状态（含签到 task_id=58） |
| `POST /apihub/app/api/signIn` | `{"gids":"8"}` + DS2 | **执行签到** → 30 米游币（需 stoken） |

> `gids` 是论坛的 `id` 字段（绝区零=8, 原神=2），**不是** `forumId`（57/26）。
> 每日只需签到一次即得全部积分，多次签到积分返回 0。

#### bbs_tasks（社区每日任务）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /post/api/getPostFull` | `?post_id=X` | **浏览帖子**（×3, task_id=59） |
| `POST /apihub/sapi/upvotePost` | `{"post_id":X}` | **点赞**（×5, task_id=60） |
| `GET /apihub/api/getShareConf` | `?entity_id=X&entity_type=1` | **分享**（×1, task_id=61） |
| `GET /post/api/getForumPostList` | `?forum_id=57` | 帖子列表（用 forumId，获取 post_id） |

> 社区接口 Base URL：`https://bbs-api.miyoushe.com`
> 浏览/点赞/分享用 stoken cookie + 简单 DS；任务状态查询用完整 cookie 无 DS。

#### 密码登录流程（参考，未实现自动化）

```
POST passport-api.mihoyo.com/account/ma-cn-passport/app/loginByPassword
  body: {account: "<RSA加密>", password: "<RSA加密>"}  → retcode=-3235 (需验证)
POST .../verifier/createMobileCaptchaByActionTicket  → 发送短信
POST .../verifier/verifyActionTicketPartly            → {mobile_captcha: "验证码"}
POST .../app/checkRiskVerified                        → token(type=1)
POST .../ma-cn-session/app/exchange                   → stoken(type=2)
POST bbs-api.miyoushe.com/user/api/login              → BBS 登录完成
```

### 森空岛（明日方舟/终末地）

- 测试版本：v1.52.1（真机 Redmi K40s / Pixel Experience Android 14 抓包）
- 凭证：手机号 + 密码（鹰角通行证全自动登录）
- 登录链路：密码登录 → hypergryph token → OAuth grant code → skland cred（HMAC 签名）
- 设备指纹：数美（ShuMei）dId 生成（DES/AES/RSA 加密 → portal101.cn）

所有社区 API 请求共用签名：HMAC-SHA256(credToken, `path+body+ts+headerJSON`) → MD5 → `sign` 头。
头部：`cred`、`sign`、`platform: 1`、`timestamp`、`dId`、`vName: 1.0.0`。
社区 gameId：明日方舟=1，终末地=3。

#### game_checkin（游戏签到）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `POST /api/v1/game/attendance` | gameId, uid（明日方舟） | **明日方舟签到** |
| `POST /web/v1/game/endfield/attendance` | sk-game-role 头 | **终末地签到** |

#### bbs_checkin（登岛检票）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /api/v1/score/ischeckin` | — | 检票状态（各游戏 `checked` 字段） |
| `POST /api/v1/score/checkin` | `{"gameId": 3}` | **执行检票** → 5 积分/游戏 |

#### bbs_tasks（社区每日任务）

| 接口 | 参数 | 用途 | 积分 |
| --- | --- | --- | --- |
| `GET /api/v1/item?id={postId}&teenager=0` | postId | **浏览内容**（×5） | 1/次 |
| `POST /api/v1/action/item/like` | `{"itemId":"..."}` | **点赞**（×10） | 1/次 |
| `POST /api/v1/score/share` | `{"gameId":3}` | **分享**（×1/游戏） | 3/次 |
| `GET /api/v1/rec/index?gameId=0&cateId=0` | — | 推荐流（获取帖子 ID） | — |
| `GET /api/v1/score/tasks` | — | 任务定义 + 积分等级 | — |
| `GET /api/v1/score/daily-task?gameId=3` | — | 每日任务完成进度 | — |

> 每日可自动完成：检票(5) + 浏览(5) + 点赞(10) + 分享(3) = **23 积分/游戏**。
> 发布内容、发评论也可获得积分但较难自动化。
> 「被点赞/被评论/被收藏」为被动积分，无法自动化。
>
> 模拟器上森空岛 APP 因 Secneo 加固 + ARM 翻译冲突无法运行（`libDexHelper` 崩溃），
> 需在真机上抓包。

### 塔吉多（异环）

- 测试版本：v1.2.5（真机 Redmi K40s / Pixel Experience Android 14 抓包）
- 凭证：手机号 + 密码（全自动登录）
- 头部：`authorization`、`uid`、`deviceid`、`appversion: 1.2.5`、`platform: android`、
  `Content-Type: application/x-www-form-urlencoded`、`User-Agent: okhttp/4.12.0`

#### game_checkin（游戏签到 — 异环）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `POST /usercenter/api/v2/getGameRoles` | gameId | 获取游戏角色 |
| `POST /apihub/awapi/sign` | roleId, gameId | **执行签到** → 游戏内奖励 |
| `GET /apihub/awapi/signin/state` | gameId | 签到状态 |
| `GET /apihub/awapi/sign/rewards` | gameId, roleId | 签到奖励列表 |

#### bbs_checkin（社区签到 — 异环社区 communityId=2）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /apihub/api/getSignState` | communityId=2 | 签到状态（`true`/`false`） |
| `POST /apihub/api/signin` | communityId=2 | **执行签到** → `{exp, goldCoin}` |

#### bbs_tasks（社区每日任务）

| 接口 | 参数 | 用途 |
| --- | --- | --- |
| `GET /bbs/api/getPostFull` | postId | **浏览帖子**（×3） |
| `POST /bbs/api/post/like` | postId | **点赞**（×3） |
| `GET /bbs/api/post/getShareData` | postId | **分享**（×1） |
| `GET /bbs/api/getRecommendPostList` | communityId, count, page | 推荐帖子列表（获取 postId） |
| `GET /apihub/api/getUserCoinTaskState` | — | 金币任务进度（`todayGet/todayTotal/total`） |
| `GET /apihub/api/getUserTasks` | gid=1 | 任务列表（含永久成就任务） |
