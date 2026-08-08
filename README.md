# gacha-sign

二游社区 APP 签到脚本，自动领取每日社区签到奖励。

| 游戏 | 社区 APP | 平台 | 凭证方式 |
| --- | --- | --- | --- |
| 绝区零 | 米游社 | `mihoyo` | APP 抓包 cookie |
| 鸣潮 | 库街区 | `kuro` | APP 抓包 token |
| 异环 | 塔吉多 | `tajiduo` | 手机号 + 密码（全自动） |
| 明日方舟/终末地 | 森空岛 | `skland` | 手机号 + 密码（全自动） |

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

### 森空岛（明日方舟/终末地）— 全自动

同理，填写鹰角通行证手机号和密码即可，程序自动登录、生成设备指纹并签到：

```yaml
- name: "森空岛主号"
  platform: skland
  phone: "手机号"
  password: "密码"
```

### 米游社（绝区零）/ 库街区（鸣潮）— APP 抓包

米游社需要 cookie（含 `cookie_token_v2` 等 v2 字段），库街区需要 token（`eyJ...` JWT，有效期约 30 天）。两者都通过模拟器抓包获取。

抓包环境搭建、APP 下载与抓包分析见 **[docs/packet-capture.md](docs/packet-capture.md)**。

## 命令

AstrBot 插件命令：

```
/gacha-sign help     # 显示帮助
/gacha-sign bind     # 将定时签到结果推送目标绑定到当前会话（管理员）
/gacha-sign run      # 手动执行签到
/gacha-sign check    # 校验凭证有效性
/gacha-sign status   # 查看上次签到结果
```

推送目标也可在插件配置中填写 `unified_msg_origin`，例如
`astrbot:GroupMessage:xxx`；留空则不推送。

CLI 命令：

```
uv run python cli.py run [--account NAME]
uv run python cli.py check
```

## 致谢

本项目的协议实现参考了以下开源项目：

- [MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools) — 米游社签到接口、act_id、retcode 语义
- [Kuro-autosignin](https://github.com/mxyooR/Kuro-autosignin) — 库街区接口、gameId/serverId、header 模板
- [astrbot_plugin_nte](https://github.com/Candy-QAQ/astrbot_plugin_nte) — 塔吉多完整协议（登录、签名、签到流程）
- [Skland-Sign-In](https://github.com/kafuneri/Skland-Sign-In) — 森空岛 dId 生成、HMAC 签名算法、签到流程
- [skland-daily-attendance](https://github.com/AEtherside/skland-daily-attendance) — 森空岛签到 TypeScript 参考实现
- [MHY_Scanner](https://github.com/MR-LIYA/MHY_Scanner) — 米游社 passport API、扫码登录流程
