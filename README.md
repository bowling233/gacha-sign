# gacha-sign

二游社区 APP 签到脚本，自动领取每日游戏签到奖励与社区积分。

每个平台支持三类签到动作：

- **游戏签到**（game_checkin）：领取游戏内每日奖励
- **社区签到**（bbs_checkin）：社区每日打卡，获得社区积分
- **社区任务**（bbs_tasks）：浏览/点赞/分享帖子，获得社区积分

| 社区 APP | 平台 | 支持游戏 | 凭证方式 |
| --- | --- | --- | --- |
| 米游社 | `mihoyo` | 绝区零、原神 | APP 抓包 cookie + stoken |
| 库街区 | `kuro` | 鸣潮 | APP 抓包 token |
| 塔吉多 | `tajiduo` | 异环 | 手机号 + 密码（全自动） |
| 森空岛 | `skland` | 明日方舟、终末地 | 手机号 + 密码（全自动） |

## 使用

```bash
uv sync
cp config.example.yaml config.yaml   # 填写凭证
uv run python cli.py run             # 签到
```

在 `config.yaml` 中设 `debug: true` 可输出详细日志到 `logs/`。

## 凭证配置

### 塔吉多（异环）/ 森空岛（明日方舟/终末地）— 全自动

在 `config.yaml` 填写手机号和密码即可，程序自动登录并管理 token：

```yaml
- name: "异环主号"
  platform: tajiduo
  phone: "手机号"
  password: "密码"
```

### 米游社（绝区零/原神）/ 库街区（鸣潮）— APP 抓包

米游社需要 cookie（含 `cookie_token_v2` 等 v2 字段）和 stoken，库街区需要 token（`eyJ...` JWT，有效期约 30 天）。两者都通过模拟器或真机抓包获取。

米游社可配置多个游戏（默认仅绝区零）：

```yaml
- name: "米游社主号"
  platform: mihoyo
  games: ["zzz", "genshin"]
  cookie: "account_id=...;cookie_token_v2=..."
  stoken: "v2_..."
```

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
