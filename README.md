<h1 align="center">
  <img src="icon.png" alt="gacha-sign" width="200">
  <br>gacha-sign<br>
</h1>

<h3 align="center">二游社区 APP 签到工具</h3>

支持下列社区 APP 和游戏：

| 社区 APP | 平台代号 | 支持游戏 | 凭证方式 |
| --- | --- | --- | --- |
| 米游社 | `mihoyo` | 绝区零、原神 | APP 抓包 cookie + stoken |
| 库街区 | `kuro` | 鸣潮 | APP 抓包 token / 网页登录 token |
| 塔吉多 | `tajiduo` | 异环 | 手机号 + 密码 |
| 森空岛 | `skland` | 明日方舟、终末地 | 手机号 + 密码 |

每个平台支持三类签到动作：

- **游戏签到**（game_checkin）：领取游戏内每日奖励
- **社区签到**（bbs_checkin）：社区每日打卡，获得社区积分
- **社区任务**（bbs_tasks）：浏览/点赞/分享帖子，获得社区积分

## 初衷与致谢

我开发本项目的初衷如下：

- 作为多坑玩家，我希望用统一的工具管理多个社区 APP 签到和任务。
- 作为多操作系统用户，我希望该工具容易在 Windows、Linux、macOS 上跨平台运行。
- 工具的配置应当是声明式的，尽可能避免交互。

现有的开源项目大多只支持单一平台，且对社区任务的支持不全，遂决定自行开发。v1.0 版本开发工作主要由 GPT-5.5 和 GLM-5.2 完成。Agent 除了根据抓包结果自行分析协议外，还参考了下列开源项目的实现：

- [MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools) — 米游社签到接口、act_id、retcode 语义
- [MHY_Scanner](https://github.com/MR-LIYA/MHY_Scanner) — 米游社 passport API、扫码登录流程
- [Kuro-autosignin](https://github.com/mxyooR/Kuro-autosignin) — 库街区接口、gameId/serverId、header 模板
- [astrbot_plugin_nte](https://github.com/Candy-QAQ/astrbot_plugin_nte) — 塔吉多完整协议（登录、签名、签到流程）
- [Skland-Sign-In](https://github.com/kafuneri/Skland-Sign-In) — 森空岛 dId 生成、HMAC 签名算法、签到流程
- [skland-daily-attendance](https://github.com/AEtherside/skland-daily-attendance) — 森空岛签到 TypeScript 参考实现

## 使用方式

### 凭证配置

- 塔吉多（异环）/ 森空岛（明日方舟/终末地）：在 `config.yaml` 填写手机号和密码即可，程序自动登录并管理 token：

  ```yaml
  - name: "账号名称"
    platform: tajiduo
    phone: "手机号"
    password: "密码"
  ```

- 库街区（鸣潮）：打开 [https://www.kurobbs.com/mc/home/9](https://www.kurobbs.com/mc/home/9)，使用手机验证码登录。登录成功后，在浏览器开发者工具 → Application → Local Storage 中找到 `auth_token` 的值，即为所需 token。

  ```yaml
  - name: "账号名称"
    platform: kuro
    token: "eyJ..."          # 网页登录 localStorage 或 APP 抓包获取
  ```

- 米游社（绝区零/原神）：米游社 APP 抓包获取 cookie（含 `cookie_token_v2` 等 v2 字段）和 stoken：

  ```yaml
  - name: "账号名称"
    platform: mihoyo
    games: ["zzz", "genshin"]
    cookie: "account_id=...;cookie_token_v2=..."
    stoken: "v2_..."
  ```

### 命令行（CLI）

```bash
uv sync
cp config.example.yaml config.yaml   # 填写凭证
uv run python cli.py run             # 签到
```

在 `config.yaml` 中设 `debug: true` 可输出详细日志到 `logs/`。

CLI 命令：

```bash
uv run python cli.py run [--account NAME]
uv run python cli.py check
```

### AstrBot 插件

AstrBot 插件命令：

```text
/gacha-sign help     # 显示帮助
/gacha-sign bind     # 将定时签到结果推送目标绑定到当前会话（管理员）
/gacha-sign run      # 手动执行签到
/gacha-sign check    # 校验凭证有效性
/gacha-sign status   # 查看上次签到结果
```

推送目标也可在插件配置中填写 `unified_msg_origin`，例如
`astrbot:GroupMessage:xxx`；留空则不推送。

### 不支持的方式

- GitHub Actions：违反 [GitHub Terms for Additional Products and Features - GitHub Docs](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)

  > If using GitHub-hosted runners, any other activity **unrelated to the production, testing, deployment, or publication of the software project** associated with the repository where GitHub Actions are used.
  >
  > Misuse of GitHub Actions may result in termination of jobs, restrictions in your ability to use GitHub Actions, disabling of repositories ... or in some cases, **suspension or termination of your GitHub account**.

## 开发

本项目的核心是对各社区 APP 的 API 协议分析。当 APP 发布新版本时，按照下列流程维护更新：

- 检查 `api` 目录下的 API 是否发生变化，更新版本和 API 描述。
- 如果旧的 API 无法正常工作，则抓包分析新版本 API。

- `scripts/download_apks.py` 用于自动获取各 APP 的最新版本并保存到 `apks/`。

### 抓包环境搭建

开发本项目最直接的手段是抓包，获取 APP 与服务器交互的原始请求与响应。搭建抓包环境有多种方法，当 Agent 没有足够的资料时，搭建环境可能绕弯路。下面记录一些搭建经验供参考：

- 使用自带 root 的安卓模拟器（如 Mumu 模拟器等）。
    - Windows 推荐使用此方式。
    - macOS [Mumu 模拟器](https://mumu.163.com/) 很好用，自带 root 且速度很快，但仅有 7 天免费试用。
- 使用 [Google 安卓虚拟设备管理器（Android Virtual Device Manager）](https://developer.android.com/tools/avdmanager)。
    - Linux x86 平台不推荐使用此方式。x86 平台上 arm64 的 APK 依赖[转译](https://android-developers.googleblog.com/2020/03/run-arm-apps-on-android-emulator.html)，森空岛、米游社等均崩溃，仅库街区能够运行。
    - macOS arm64 推荐使用此方式。
- 使用已 root 的真机 + [Magisk](https://github.com/topjohnwu/Magisk) 配合 [AlwaysTrustUserCerts](https://github.com/NVISOsecurity/AlwaysTrustUserCerts) 等模块。

#### macOS arm64 + avdmanager

```shell
# 0. 安装所需工具
brew install --cask android-commandlinetools     # SDK (sdkmanager / avdmanager / emulator / adb)
brew install mitmproxy                             # 抓包代理

# 1. 使用 Google APIs 镜像（非 Play Store）
yes | sdkmanager --install "system-images;android-36;google_apis;arm64-v8a"

# 2. 创建 AVD 并启动，需要 -writable-system
avdmanager create avd -n gacha_sign \
  -k "system-images;android-36;google_apis;arm64-v8a" --force
emulator -avd gacha_sign -writable-system -no-snapshot

# 3. 安装 CA，需要向 **两个位置** 注入证书：
HASH=$(openssl x509 -inform PEM -subject_hash_old \
  -in ~/.mitmproxy/mitmproxy-ca-cert.pem | head -1)   # 例: c8750f0d
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /tmp/${HASH}.0
# 3.1 /system 分区：overlayfs
adb root
adb shell avbctl disable-verification
adb reboot
adb wait-for-device && adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
adb root
adb remount          # 通过 overlayfs 将 /system 挂载为 RW
adb push /tmp/${HASH}.0 /system/etc/security/cacerts/${HASH}.0
adb shell "chmod 644 /system/etc/security/cacerts/${HASH}.0"
adb shell "chcon u:object_r:system_security_cacerts_file:s0 /system/etc/security/cacerts/${HASH}.0"
# 3.2 注入 conscrypt APEX：通过 nsenter 在 zygote 的 mount namespace 中执行挂载。这样 zygote fork 出的所有 APP 进程都会继承受信任的证书。
adb shell "mkdir -p /data/local/tmp/cacerts-rw"
adb shell "cp /apex/com.android.conscrypt/cacerts/* /data/local/tmp/cacerts-rw/"
adb shell "cp /system/etc/security/cacerts/${HASH}.0 /data/local/tmp/cacerts-rw/"
ZYGOTE_PID=$(adb shell ps -A | grep 'zygote64' | grep -v webview | awk '{print $2}')
adb shell "nsenter -t ${ZYGOTE_PID} -m -- sh -c '\
  mount -t tmpfs tmpfs /apex/com.android.conscrypt/cacerts && \
  cp /data/local/tmp/cacerts-rw/* /apex/com.android.conscrypt/cacerts/ && \
  chown root:root /apex/com.android.conscrypt/cacerts/* && \
  chmod 644 /apex/com.android.conscrypt/cacerts/* && \
  chcon u:object_r:system_security_cacerts_file:s0 /apex/com.android.conscrypt/cacerts/*'"

# 4. 配置代理
# 启动 mitmproxy（单独终端）
mitmdump -p 8888 -w /tmp/capture.flows
# 设置模拟器代理（10.0.2.2 是 AVD 中宿主机的地址）
adb shell settings put global http_proxy 10.0.2.2:8888
# 完成后清除
adb shell settings put global http_proxy :0
```

已测试的环境：

- macOS 27 / MacBook M2 Air

#### 真机 + Magisk + AlwaysTrustUserCerts

```bash
# 1. 手机需已安装 Magisk（解锁 Bootloader → patch boot.img → fastboot 刷入）
# 2. 在 Magisk 设置中启用 Zygisk，重启
# 3. 将 mitmproxy CA 作为用户证书安装
adb push ~/.mitmproxy/mitmproxy-ca-cert.pem /data/local/tmp/mitm_ca.pem
adb shell su -c "mkdir -p /data/misc/user/0/cacerts-added && \
  cp /data/local/tmp/mitm_ca.pem /data/misc/user/0/cacerts-added/${HASH}.0 && \
  chmod 644 /data/misc/user/0/cacerts-added/${HASH}.0"
# 4. 安装 AlwaysTrustUserCerts 模块并重启
adb push AlwaysTrustUserCerts_v1.3.zip /data/local/tmp/
adb shell su -c "magisk --install-module /data/local/tmp/AlwaysTrustUserCerts_v1.3.zip"
adb reboot
```

已测试的环境：

- Redmi K40s / Pixel Experience Android 14 / Magisk 30.7

注意事项：

- 一些手机 bootloader fastboot 有下载大小限制，需用 `adb reboot fastboot` 进入 fastbootd 模式刷入。

## 免责声明

本项目仅供个人学习、研究和技术交流使用，不得用于任何商业用途。使用本项目前，请务必仔细阅读并理解以下内容：

1. **违反平台协议的风险**：本项目通过模拟 APP 客户端调用社区 API 完成签到与任务，属于使用自动化脚本/非官方授权第三方工具访问服务。上述行为**可能违反各平台用户协议**，相关禁止性条款包括但不限于：
   - [米游社用户服务协议](https://www.miyoushe.com/ys/agreement) 第二条第 15 款：禁止以机器人软件、爬虫软件等自动程序、脚本获取服务信息与数据
   - [库街区用户协议](https://www.kurobbs.com/p/privacy_policy.html) 第三章第 2.2 条：禁止使用未授权第三方软件获取库洛币
   - [森空岛使用许可及服务协议](https://assets.skland.com/protocols/agreement.html) 第四条：禁止使用插件、外挂或非经合法授权的第三方工具接入系统
   - [塔吉多用户协议](https://webstatic.tajiduo.com/bbs/p/user_protocol.html) 第 6.2.2 条：禁止以机器人软件、刷屏软件等非认可方式访问或登录

2. **账号处罚风险**：各平台有权对使用自动化工具的账号采取**警告、禁言、限制或禁止功能、清零社区积分（米游币/库洛币/塔币等）、封禁账号、注销账号**等措施。使用者需自行承担由此产生的一切后果。

3. **个人责任**：使用者应确保仅操作**本人合法持有**的账号，因使用本项目导致的任何直接或间接损失（包括但不限于账号封禁、数据丢失、虚拟财产清零），由使用者自行承担，项目作者及贡献者不承担任何责任。

4. **非官方、无担保**：本项目是**非官方第三方工具**，与米哈游、库洛、鹰角网络、完美世界均无任何关联。本项目不保证功能的可用性、稳定性或合法性，各平台 API 可能随时变更导致功能失效。

5. **知识产权声明**：本项目不对各平台的服务、软件、商标、内容主张任何权利，相关知识产权均归各自权利人所有。本项目代码以 MIT 开源协议发布，使用者应遵守相关开源许可证。

6. **使用即视为知悉并接受上述全部风险与条款**。如不同意，请立即停止使用并删除本项目。
