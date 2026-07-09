# gacha-sign

二游社区 APP 签到脚本，自动领取每日社区签到奖励。

| 游戏 | 社区 APP | 平台 | 凭证方式 |
| --- | --- | --- | --- |
| 绝区零 | 米游社 | `mihoyo` | APP 抓包 cookie |
| 鸣潮 | 库街区 | `kuro` | APP 抓包 token |
| 异环 | 塔吉多 | `tajiduo` | 手机号 + 密码（全自动） |

## 使用

```bash
uv sync
cp config.example.yaml config.yaml   # 填写凭证
uv run python cli.py run             # 签到
```

在 `config.yaml` 中设 `debug: true` 可输出详细日志到 `logs/`。

## 凭证配置

### 塔吉多（异环）— 全自动

在 `config.yaml` 填写手机号和密码即可，程序自动登录并管理 token：

```yaml
- name: "异环主号"
  platform: tajiduo
  phone: "手机号"
  password: "密码"
```

### 米游社（绝区零）/ 库街区（鸣潮）— APP 抓包

米游社需要 cookie（含 `cookie_token_v2` 等 v2 字段），库街区需要 token（`eyJ...` JWT，有效期约 30 天）。两者都通过 APP 抓包获取。

以下使用免费的 Google AVD（Android Studio 模拟器）进行抓包。也可以使用其他 root 模拟器或真机。

需要提前准备好：mitmproxy、Android SDK 命令行工具（`sdkmanager`、`avdmanager`、`adb`、`emulator`）。

#### 1. 创建 AVD

下载 Android 11 **Google APIs** 镜像并创建 AVD（必须用 Google APIs 版，非 Google Play，否则无法 root）：

```bash
sdkmanager "system-images;android-30;google_apis;arm64-v8a"
avdmanager create avd -n gacha_sign -k "system-images;android-30;google_apis;arm64-v8a" -d pixel_6
```

> **注意**：x86 平台请将 `arm64-v8a` 替换为 `x86_64`（模拟器会自动翻译运行 ARM APK）。

#### 2. 启动模拟器

```bash
emulator -avd gacha_sign -writable-system
```

`-writable-system` 参数让 `/system` 分区可写，以便安装抓包证书。

#### 3. 安装证书（每次重启模拟器后需重新执行）

```bash
# root 并挂载可写系统分区
adb root
sleep 2
adb remount

# 安装 mitmproxy CA 证书到系统目录
CERT=~/.mitmproxy/mitmproxy-ca-cert.pem
HASH=$(openssl x509 -inform PEM -subject_hash_old -in "$CERT" | head -1)
adb push "$CERT" /sdcard/mitm_ca.pem
adb shell cp /sdcard/mitm_ca.pem /system/etc/security/cacerts/${HASH}.0
adb shell chmod 644 /system/etc/security/cacerts/${HASH}.0

# 重启让证书生效（overlayfs 需要重启合并）
adb reboot
adb wait-for-device
adb root
```

#### 4. 设置代理并抓包

```bash
# 安装 APP

# 启动 mitmproxy
mitmdump -p 8888 -w /tmp/capture.flows &

# 设置代理
adb shell settings put global http_proxy 10.0.2.2:8888

# 打开 APP，登录并**进入签到页面**（米游社签到相关请求才带完整 cookie）
# 完成操作后提取凭证

# 清理代理
adb shell settings put global http_proxy :0
```

#### 5. 提取凭证

```bash
# 从抓包文件中提取米游社 cookie（取字段最多的那条）
cat > /tmp/extract.py << 'EOF'
from mitmproxy import http
import sys
best = {"cookie": "", "count": 0}
def request(flow):
    host = flow.request.pretty_host
    if "mihoyo" not in host and "miyoushe" not in host:
        return
    cookie = flow.request.headers.get("Cookie", "")
    if not cookie:
        return
    count = len([k for k in cookie.split(";") if "=" in k])
    if count > best["count"]:
        best["cookie"] = cookie
        best["count"] = count
def done():
    sys.stderr.write(best["cookie"] + "\n")
EOF
mitmdump -nr /tmp/capture.flows -s /tmp/extract.py --set flow_detail=0 2>&1 | grep -v "^\["
```

- **米游社**：将输出的完整 Cookie 填入 `config.yaml` 的 `cookie` 字段
- **库街区**：同上流程，将请求头中的 `token` 值（`eyJ...` 开头）填入 `token` 字段

## 命令

```
uv run python cli.py run [--platform mihoyo|kuro|tajiduo] [--account NAME]
uv run python cli.py check
```

## 致谢

本项目的协议实现参考了以下开源项目：

- [MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools) — 米游社签到接口、act_id、retcode 语义
- [Kuro-autosignin](https://github.com/mxyooR/Kuro-autosignin) — 库街区接口、gameId/serverId、header 模板
- [astrbot_plugin_nte](https://github.com/Candy-QAQ/astrbot_plugin_nte) — 塔吉多完整协议（登录、签名、签到流程）
- [MHY_Scanner](https://github.com/MR-LIYA/MHY_Scanner) — 米游社 passport API、扫码登录流程
