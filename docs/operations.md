# 运行手册

## 1. 环境

- Windows 10/11
- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- 微信：固定版本 WeChat 4.1.10.27 和已核验 native hook
- QQ：NapCat（个人测试号）
- 语音：可选，独立 Python 3.10 GPT-SoVITS 环境

安装：

```powershell
cd D:\workspace\自用\wechat-ai-bridge
uv sync --extra native
# 微信语音条需要：
uv sync --extra native --extra voice
```

首次配置：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

配置项见 [configuration.md](configuration.md)。

## 2. 启动顺序

### 2.1 微信

1. 启动微信并登录机器人账号；
2. 确认 native hook 已注入、`run --check` 通过；
3. 启动网关：

```powershell
uv run --extra native --extra voice wechat-ai-bridge run
```

只检查、不发送：

```powershell
uv run --extra native wechat-ai-bridge run --check
```

### 2.2 QQ

1. 启动 NapCat 并登录测试小号；
2. 确认 NapCat OneBot HTTP API、HTTP webhook 已启用；
3. 启动网关：

```powershell
uv run --extra native wechat-ai-bridge qq run
```

检查：

```powershell
uv run --extra native wechat-ai-bridge qq groups
uv run --extra native wechat-ai-bridge qq friends
uv run --extra native wechat-ai-bridge qq check
```

注意：`qq run` 运行时不要再执行 `qq check`，因为它会占用 8081 端口。

### 2.3 推荐：启动脚本

```powershell
# 只启动 QQ（会尝试拉起 NapCat；语音开启时还会拉起 GPT-SoVITS）
.\scripts\start.ps1 -Target qq

# 只启动微信
.\scripts\start.ps1 -Target wechat

# 两个平台各开一个窗口
.\scripts\start.ps1 -Target all
```

脚本参数：

```powershell
.\scripts\start.ps1 -Target qq -NapCatHome 'tools\NapCat.Shell'
```

如果 NapCat 需要扫码，脚本会提示先登录，然后再启动网关。

QQ 语音开启时，脚本会在 9880 未监听的情况下用独立窗口启动 GPT-SoVITS，并等待 API 就绪。也可以单独启动：

```powershell
.\scripts\start-gpt-sovits.ps1
```

## 3. 验证

- 微信群：发一条 `@机器人` 消息，观察终端 `[收到]` / `[发送成功]`。
- QQ 群：在测试群 `@机器人`；确认 `qq run` 终端有 `[收到]` / `[发送成功]`。
- QQ 私聊：用 `QQ_ALLOWED_PRIVATE_USERS` 中的好友发消息。
- 微信私聊：`gateway.json` 的 `allowedPrivateIds` 中的 wxid 发消息。
- 表情包：触发一次适合配图的回复，检查文字后是否跟一张图。
- 天气：`@机器人 北京天气怎么样`。
- 看图：先发图，120 秒内 `@机器人 看图，这是什么`。
- 语音：GPT-SoVITS 启动且 `QQ_VOICE_ENABLED=true` 后，让模型选择语音回复。

## 4. 停止与重启

前台进程直接 `Ctrl+C`。后台/卡住时：

```powershell
# 查看端口占用
netstat -ano | findstr :8081
netstat -ano | findstr :3000
```

只停止本项目进程，不要无条件结束所有 `python.exe`。`qq run` 有单实例锁；再次启动报 `gateway_already_running` 时，先停掉旧进程。

修改 `.env`、`gateway.json`、`config/persona.md`：

- `.env`、网关配置：重启进程；
- 人设文件：下一次模型请求自动读取，无需重启。

## 5. 常见问题

| 现象 | 处理 |
| --- | --- |
| NapCat 报 `ECONNREFUSED 127.0.0.1:8081` | QQ 网关没运行或已退出；重新执行 `qq run` |
| `webhook 400` | 确认服务端为当前版本（支持 chunked body），检查 webhook 路径 |
| `webhook 401` | `QQ_WEBHOOK_SECRET` 与 NapCat HTTP 客户端 token 不一致 |
| `onebot_unavailable` | NapCat API 没启动、端口或 `QQ_ONEBOT_TOKEN` 不一致 |
| `qq_groups_missing` | `.env` 的 `QQ_ALLOWED_GROUPS` 为空 |
| `gateway_already_running` | 已有 `qq run`/`run` 进程，先停旧进程 |
| QQ 不回消息 | 检查群/私聊白名单、`qq run` 终端、模型密钥、暂停状态 |
| 微信不回消息 | 检查 `run --check`、群/私聊白名单、`is_paused`、模型密钥 |
| 没有表情包 | 执行 `memes sync`，确认 `memesEnabled`；模型不一定每条都配图 |
| 看图失败 | 必须是同一成员最近一张、120 秒内、明确 `@` 并要求看图 |
| 语音失败 | 检查 GPT-SoVITS `/tts`、参考音频、`QQ_VOICE_ENABLED`；微信语音还需前台窗口和 VB-Cable |
| 温度/天气失败 | 检查 `WECHAT_AI_BRIDGE_WEATHER_ENABLED` 和外网 |

## 6. 数据与安全

- 控制台只做本机临时观察，不写入日志文件；不要把控制台输出重定向到公开文件。
- 聊天历史、数据库、`.env`、`gateway.json` 和 NapCat token 不提交 Git。
- QQ 小号有风控风险；微信 native hook 依赖固定版本，升级微信可能导致失效。
- 不要把 8081/3000 暴露到公网；跨机器必须自行加网络隔离和 secret。
