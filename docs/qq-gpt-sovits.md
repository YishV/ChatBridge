# QQ 接入：NapCat + GPT-SoVITS 语音条

这是实验性的第二平台接入。它和微信网关是两个独立进程，共用本项目的人设、模型和聊天核心，但不共用登录态或状态库。

## 为什么这样拆

- NapCat 是个人 QQ 号的非官方 OneBot 11 实现，账号有风控/封号风险，不能和正式微信账号混在一起。
- GPT-SoVITS 需要 PyTorch 和 Python 3.10/3.11，而本项目固定 Python 3.12；把它做成独立本机 HTTP 服务，避免依赖冲突。
- QQ 语音条由 NapCat 负责把 WAV 转成 SILK；本项目只生成 WAV，不直接操作 QQ 客户端。

## 前置

1. Windows 本机，NapCat 已安装并用**测试小号**登录。
2. NapCat 开启 OneBot 11：
   - HTTP API 服务，例如 `http://127.0.0.1:3000`；
   - 设置 access token；
   - HTTP POST webhook（HTTP Client）指向 `http://127.0.0.1:8081/onebot`；
   - 群消息开启“接收所有消息”，否则自然接话收不到普通群聊。
3. GPT-SoVITS 独立环境（建议官方整合包或 Conda Python 3.10）能启动 `api_v2.py`，并准备好参考音频和对应文本。
4. 一个测试 QQ 群；把群号填入 `QQ_ALLOWED_GROUPS`。

NapCat 的 webhook 与 OneBot 配置见官方文档：[HTTP POST Webhook](https://mintlify.wiki/NapNeko/NapCatQQ/api/network/webhook)、[OneBot 配置](https://mintlify.wiki/NapNeko/NapCatQQ/config/onebot-config)。语音发送时 NapCat 会调用内置 silk/ffmpeg 转码。

## GPT-SoVITS

官方项目：[RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)。它支持 5 秒 zero-shot、1 分钟 few-shot 和微调；你的 8GB 显存可以推理，训练需要小 batch、更多时间，或改用云端训练。

建议先用 zero-shot 跑通 QQ 语音条，再训练专属音色：

```bash
# 在 GPT-SoVITS 自己的环境里
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

训练完成后，把推理用的 GPT/SoVITS 权重填入 `tts_infer.yaml`，或通过 `/set_gpt_weights`、`/set_sovits_weights` 切换。参考音频必须是自录或有权使用的声音；参考音频和项目里的 `QQ_GPT_SOVITS_PROMPT_TEXT` 要对应。

本项目只访问本机回环地址的 `/tts`，请求字段包括 `text`、`text_lang`、`ref_audio_path`、`prompt_text`、`prompt_lang`、`media_type=wav`，并校验返回的单声道 16-bit WAV。接口说明见 [api_v2.py](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)。

## 配置

把 `.env.example` 的 QQ 段复制到本机 `.env`：

```dotenv
QQ_ONEBOT_API_URL=http://127.0.0.1:3000
QQ_ONEBOT_TOKEN=你的NapCatToken
QQ_WEBHOOK_HOST=127.0.0.1
QQ_WEBHOOK_PORT=8081
QQ_WEBHOOK_PATH=/onebot
QQ_WEBHOOK_SECRET=一段随机串
QQ_ALLOWED_GROUPS=测试群号
QQ_STATE_DB=data/runtime/qq-chat-state.sqlite3
QQ_PARTICIPATION_MODE=natural
QQ_COOLDOWN_SECONDS=5
QQ_HOURLY_QUOTA=60
QQ_SPONTANEOUS_COOLDOWN_SECONDS=5
QQ_VOICE_ENABLED=true
QQ_GPT_SOVITS_URL=http://127.0.0.1:9880
QQ_GPT_SOVITS_REF_AUDIO=D:/GPT-SoVITS/reference/whale.wav
QQ_GPT_SOVITS_PROMPT_TEXT=参考音频里实际说出的文字
QQ_GPT_SOVITS_PROMPT_LANG=zh
QQ_GPT_SOVITS_TEXT_LANG=zh
QQ_GPT_SOVITS_TIMEOUT_SECONDS=30
QQ_VOICE_MAX_CHARS=120
```

`QQ_GPT_SOVITS_REF_AUDIO` 必须是 GPT-SoVITS 进程能读到的本机路径；如果 GPT-SoVITS 在 Docker 里，路径要映射到容器内同一位置。

## 启动与检查

```powershell
uv run --extra native wechat-ai-bridge qq check
uv run --extra native wechat-ai-bridge qq run
```

`qq check` 只检查 OneBot 登录、webhook 端口、群数量和参考音频是否存在，不发送消息，也不调用 `/tts`。`qq run` 启动 webhook 和 QQ 网关；微信网关仍用原来的 `run` 命令，两个进程互不影响。

## 行为与边界

- 模型通过 `decide_reply` 的 `voice` 字段决定文字或语音；短句、口语、情绪化回复更适合语音，长说明和事实用文字。
- QQ 语音链路：`GPT-SoVITS /tts → WAV → OneBot record → NapCat SILK → 群语音条`。
- GPT-SoVITS 失败、超时或返回无效 WAV 时回退文字；`record` API 状态不明时不补发文字，避免重复。
- QQ v1 不发送表情包、不做看图、不做真 @ 提醒；这些能力仍只在微信侧或后续版本。
- NapCat 是个人号协议，存在账号风险；只使用测试小号，不要把 webhook/API 暴露到公网。
- 目前没有在真实 NapCat + QQ 群上完成端到端验收；离线测试覆盖事件映射、webhook 签名、OneBot 收发、GPT-SoVITS 请求与语音决策。真实语音条需要一次群内测试。

## 常见问题

- `qq check` 报 `onebot_unavailable`：NapCat HTTP API 没启动、端口或 token 不对。
- webhook 401：`QQ_WEBHOOK_SECRET` 与 NapCat webhook 密钥不一致。
- webhook 403：未设置 secret 时只接受本机请求；跨机器请设置 secret。
- 语音没有声音：确认 GPT-SoVITS 返回 WAV、NapCat 的 ffmpeg/silk 可用，并检查参考音频和 prompt 文本。
- 语音发送状态未知：不要自动重试；先检查 QQ 群实际消息。
