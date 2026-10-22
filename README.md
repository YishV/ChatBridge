# wechat-ai-bridge

本机微信 + QQ 群聊/私聊 AI 桥接。微信走已核验的 native hook，QQ 走 NapCat/OneBot 11；模型使用 DeepSeek，语音可选接本机 GPT-SoVITS。

## 能力

| 能力 | 微信 | QQ |
| --- | --- | --- |
| 群聊文字 | 支持 | 支持 |
| 私聊文字 | 支持（wxid 白名单） | 支持（QQ 号白名单） |
| 自然接话 | 支持 | 支持 |
| 表情包 | 微信原生 sticker | image 消息段 |
| 看图 | 同群同成员最近一张 | 同成员最近一张 |
| 天气 | Open-Meteo | Open-Meteo |
| 语音条 | Edge TTS + VB-Cable + UI | GPT-SoVITS + record |
| 真 @ 提醒 | 未实现 | 未实现 |

微信和 QQ 是**两个独立进程**，各自登录、各自重连、各自 SQLite，互不阻塞。

## 快速开始

要求：Windows、Python 3.12、[uv](https://docs.astral.sh/uv/)、已核验的微信 native hook 或已登录的 NapCat。

```powershell
cd D:\workspace\自用\wechat-ai-bridge
uv sync --extra native
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

### 推荐：启动脚本

```powershell
# QQ：自动尝试拉起 NapCat、GPT-SoVITS 和翻唱 worker
.\scripts\start.ps1 -Target qq

# 微信：只启动微信网关
.\scripts\start.ps1 -Target wechat

# 两个平台各开一个窗口
.\scripts\start.ps1 -Target all
```

停止：

```powershell
.\scripts\stop.ps1 -Target qq
.\scripts\stop.ps1 -Target wechat
.\scripts\stop.ps1 -Target all
```

NapCat 不在默认路径时：

```powershell
.\scripts\start.ps1 -Target qq -NapCatHome 'tools\NapCat.Shell'
```

### 手动启动

```powershell
# 微信：先启动微信/native hook
uv run --extra native wechat-ai-bridge run --check
uv run --extra native wechat-ai-bridge run

# QQ：先启动 NapCat 并登录
uv run --extra native wechat-ai-bridge qq groups
uv run --extra native wechat-ai-bridge qq friends
uv run --extra native wechat-ai-bridge qq check
uv run --extra native wechat-ai-bridge qq run
```

`qq run` 运行期间不要再跑 `qq check`，因为检查会占用 8081 端口。

## 文档

- [运行手册](docs/operations.md)：启动/停止、验收、故障处理
- [配置说明](docs/configuration.md)：`.env`、微信 gateway、NapCat、GPT-SoVITS
- [架构](docs/architecture.md)：进程、分层、数据流
- [完成度与后续建议](docs/roadmap.md)
- [QQ 接入说明](docs/qq-gpt-sovits.md)
- [曲库翻唱](docs/singing-library.md)
- [私聊长期记忆](docs/memory.md)
- [文字聊天验收](docs/chat-acceptance.md)
- [需求](requirements.md) / [验收流程](workflow.md)

## 运行时

```text
wechat-ai-bridge run            微信网关
wechat-ai-bridge qq run         QQ 网关
wechat-ai-bridge singing-worker 曲库翻唱 worker
```

- 微信状态库：`data/runtime/chat-state.sqlite3`
- QQ 状态库：`data/runtime/qq-chat-state.sqlite3`
- 人设：`config/persona.md`
- 微信群/私聊白名单：`data/runtime/native-hook/gateway.json`
- QQ 群/私聊白名单：`.env` 的 `QQ_ALLOWED_GROUPS` / `QQ_ALLOWED_PRIVATE_USERS`

控制台日志只写标准错误，用于本机观察，不写入文件。不要把包含聊天正文的输出重定向保存或提交。

## 测试

```powershell
uv run --extra native python -m unittest discover -s tests -v
git diff --check
```

离线测试使用虚构消息和假 HTTP 服务，不能证明真实微信、NapCat 或 GPT-SoVITS 已联通。

## 注意

- 只使用授权测试群和测试 QQ 小号；NapCat 是非官方协议，有账号风控风险。
- `.env`、`gateway.json`、数据库密钥、NapCat token/secret 不提交 Git。
- 微信语音条需要前台窗口和 VB-Cable；QQ 语音需要独立 Python 3.10 的 GPT-SoVITS 服务，不能装进本项目虚拟环境。
- 当前 QQ 语音条仍需现场验收；GPT-SoVITS 未安装时 `QQ_VOICE_ENABLED=false`。

## 目录

```text
src/wechat_ai_bridge/
  wechat/       微信读取、native hook、发送与语音 UI
  qq/           NapCat/OneBot、webhook、私聊、图片、语音发送
  dispatch/     去重、冷却、配额与自然接话
  qa/           聊天上下文、决策工具、人设
  models/       DeepSeek 适配
  media/        表情素材与 GIF 缓存
  weather/      Open-Meteo
  vision/       图片归一化与看图
  speech/       GPT-SoVITS 本机客户端
  state/        SQLite 状态与历史
scripts/        启动脚本
docs/           运行、配置、架构和路线图
tests/          离线测试
```
