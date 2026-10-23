# 配置说明

所有本机配置放 `.env`（不要提交）。从模板开始：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

## 1. 核心与微信

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WECHAT_AI_BRIDGE_MODEL_API_KEY` | 空 | DeepSeek 密钥，必填 |
| `WECHAT_AI_BRIDGE_MODEL_NAME` | `deepseek-flash` | 模型名 |
| `WECHAT_AI_BRIDGE_MODEL_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容地址 |
| `WECHAT_AI_BRIDGE_ALLOWED_MODEL_HOSTS` | `api.deepseek.com` | 允许的模型主机 |
| `WECHAT_AI_BRIDGE_MODEL_THINKING` | `disabled` | `enabled`/`disabled` |
| `WECHAT_AI_BRIDGE_MODEL_TIMEOUT_SECONDS` | `45` | 1～120 秒 |
| `WECHAT_AI_BRIDGE_MODEL_MAX_TOKENS` | `1200` | 128～8192 |
| `WECHAT_AI_BRIDGE_STATE_DB` | `data/runtime/chat-state.sqlite3` | 微信状态库 |
| `WECHAT_AI_BRIDGE_GATEWAY_CONFIG` | `data/runtime/native-hook/gateway.json` | 微信 hook 配置 |
| `WECHAT_AI_BRIDGE_PROMPT_FILE` | `config/persona.md` | 人设文件 |
| `WECHAT_AI_BRIDGE_PARTICIPATION_MODE` | `natural` | `natural`/`mentions` |
| `WECHAT_AI_BRIDGE_COOLDOWN_SECONDS` | `5` | 直接互动冷却 |
| `WECHAT_AI_BRIDGE_SPONTANEOUS_COOLDOWN_SECONDS` | `5` | 自然接话合并等待，最少 5 |
| `WECHAT_AI_BRIDGE_HOURLY_QUOTA` | `60` | 每群每小时模型调用上限 |
| `WECHAT_AI_BRIDGE_HISTORY_CONTEXT_MESSAGES` | `50` | 恢复最近条数 1～200 |
| `WECHAT_AI_BRIDGE_MEMES_ENABLED` | `true` | 表情包总开关 |
| `WECHAT_AI_BRIDGE_MEME_CACHE_DIR` | `data/runtime/memes` | 素材索引/缓存 |
| `WECHAT_AI_BRIDGE_MEME_COOLDOWN_SECONDS` | `60` | 每群图片冷却 |
| `WECHAT_AI_BRIDGE_MEME_RECENT_COUNT` | `5` | 最近避重张数 |
| `WECHAT_AI_BRIDGE_WEATHER_ENABLED` | `true` | 天气开关 |
| `WECHAT_AI_BRIDGE_WEATHER_TIMEOUT_SECONDS` | `5` | 1～30 秒 |
| `WECHAT_AI_BRIDGE_WEATHER_CACHE_SECONDS` | `600` | 0～3600 秒 |
| `WECHAT_AI_BRIDGE_VISION_ENABLED` | `true` | 微信看图开关 |
| `WECHAT_AI_BRIDGE_VISION_WINDOW_SECONDS` | `120` | 30～300 秒 |
| `WECHAT_AI_BRIDGE_VOICE_ENABLED` | `false` | 微信语音条开关，默认关闭 |
| `WECHAT_AI_BRIDGE_VOICE_NAME` | `zh-CN-XiaoxiaoNeural` | Edge TTS 音色 |
| `WECHAT_AI_BRIDGE_VOICE_MAX_CHARS` | `120` | 语音最大字数 |
| `WECHAT_AI_BRIDGE_MEMORY_ENABLED` | `true` | 私聊长期记忆总开关 |
| `WECHAT_AI_BRIDGE_MEMORY_DB` | `data/runtime/memory.sqlite3` | 记忆与摘要数据库 |
| `WECHAT_AI_BRIDGE_MEMORY_UPDATE_EVERY` | `3` | 每 N 轮私聊抽取一次记忆 |
| `WECHAT_AI_BRIDGE_MEMORY_SUMMARY_EVERY` | `30` | 每 N 轮生成一次摘要；0 关闭 |
| `WECHAT_AI_BRIDGE_MEMORY_QUEUE_SIZE` | `64` | 记忆后台队列上限 |
| `WECHAT_AI_BRIDGE_LOG_DIR` | `data/runtime/logs` | 轮转日志目录 |
| `WECHAT_AI_BRIDGE_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

微信私聊额外配置在 `data/runtime/native-hook/gateway.json`：

```json
{
  "allowedGroupIds": ["群ID@chatroom"],
  "allowedPrivateIds": ["wxid_好友"]
}
```

`allowedPrivateIds` 只填一对一 wxid，不填群 ID。用下面的命令查 wxid：

```powershell
uv run --extra native wechat-ai-bridge contacts --query 对方昵称
```

## 2. QQ / NapCat

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QQ_ONEBOT_API_URL` | `http://127.0.0.1:3000` | NapCat OneBot HTTP API |
| `QQ_ONEBOT_TOKEN` | 空 | NapCat HTTP 服务端 token，不是 QQ 密码 |
| `QQ_WEBHOOK_HOST` | `127.0.0.1` | 本机 webhook 监听地址 |
| `QQ_WEBHOOK_PORT` | `8081` | 本机 webhook 端口 |
| `QQ_WEBHOOK_PATH` | `/onebot` | webhook 路径 |
| `QQ_WEBHOOK_SECRET` | 空 | NapCat HTTP 客户端 token；HMAC-SHA1 |
| `QQ_ALLOWED_GROUPS` | 空 | 允许的 QQ 群号，逗号分隔 |
| `QQ_ALLOWED_PRIVATE_USERS` | 空 | 允许私聊的 QQ 号，逗号分隔 |
| `QQ_STATE_DB` | `data/runtime/qq-chat-state.sqlite3` | QQ 独立状态库 |
| `QQ_PARTICIPATION_MODE` | `natural` | 群聊 `natural`/`mentions` |
| `QQ_COOLDOWN_SECONDS` | `5` | 冷却 |
| `QQ_HOURLY_QUOTA` | `60` | 每小时配额 |
| `QQ_SPONTANEOUS_COOLDOWN_SECONDS` | `5` | 自然接话等待 |
| `QQ_VOICE_ENABLED` | `true` | QQ 语音条开关 |
| `QQ_GPT_SOVITS_URL` | `http://127.0.0.1:9880` | 本机 GPT-SoVITS |
| `QQ_GPT_SOVITS_REF_AUDIO` | `data/runtime/gpt-sovits/reference.wav` | 参考音频绝对路径 |
| `QQ_GPT_SOVITS_PROMPT_TEXT` | 空 | 参考音频对应文字 |
| `QQ_GPT_SOVITS_PROMPT_LANG` | `zh` | 参考音频语言 |
| `QQ_GPT_SOVITS_TEXT_LANG` | `zh` | 合成语言 |
| `QQ_GPT_SOVITS_TIMEOUT_SECONDS` | `30` | 1～180 秒 |
| `QQ_VOICE_MAX_CHARS` | `120` | 语音最大字数 |
| `QQ_VISION_ENABLED` | `true` | QQ 看图开关 |
| `QQ_VISION_WINDOW_SECONDS` | `120` | 30～300 秒 |

### NapCat OneBot 配置

在 `tools/NapCat.Shell\config\onebot11_<QQ号>.json`（或 WebUI 网络配置）中设置：

```json
{
  "network": {
    "httpServers": [{
      "name": "wechat-ai-bridge-api",
      "enable": true,
      "host": "127.0.0.1",
      "port": 3000,
      "messagePostFormat": "array",
      "token": "<QQ_ONEBOT_TOKEN>"
    }],
    "httpClients": [{
      "name": "wechat-ai-bridge-webhook",
      "enable": true,
      "url": "http://127.0.0.1:8081/onebot",
      "messagePostFormat": "array",
      "reportSelfMessage": false,
      "token": "<QQ_WEBHOOK_SECRET>"
    }]
  }
}
```

- `httpServers` 给本项目调用 API；`httpClients` 把事件推给本项目。
- 两边 token 不要混用；分别对应 `.env` 的 `QQ_ONEBOT_TOKEN` 和 `QQ_WEBHOOK_SECRET`。
- NapCat 的 webhook 签名是 **HMAC-SHA1**，本项目按 `X-Signature: sha1=...` 校验。
- 修改后重启 NapCat。

### 群号与好友号

```powershell
uv run --extra native wechat-ai-bridge qq groups
uv run --extra native wechat-ai-bridge qq friends
```

把 `group_id` 填入 `QQ_ALLOWED_GROUPS`，把 `user_id` 填入 `QQ_ALLOWED_PRIVATE_USERS`。

## 3. GPT-SoVITS

GPT-SoVITS 必须独立环境，不装进本项目虚拟环境：

```bash
# 在 GPT-SoVITS 自己的 Python 3.10 环境
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

`.env` 里参考音频必须是 GPT-SoVITS 进程能访问的路径；Docker 部署要做路径映射。先 zero-shot 验证，再训练专属音色。

## 4. 安全

- `.env`、`gateway.json`、数据库密钥、NapCat token/secret 不提交 Git。
- 私聊和群都只处理显式白名单。
- webhook/API 只监听本机；需要跨机器时加 secret 并自行做网络隔离。
- 使用测试 QQ 小号；NapCat 是非官方协议，有账号风控风险。
