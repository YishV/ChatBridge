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

NapCat 的 OneBot 配置在 `tools/NapCat.Shell\config\onebot11_<QQ号>.json`（以实际安装目录为准），也可以在 NapCat WebUI 的“网络配置”里建：

- **HTTP 服务端**：给本项目调用 API；`host=127.0.0.1`、`port=3000`、`messagePostFormat=array`，设置 `token`。
- **HTTP 客户端**：把事件推给本项目；`url=http://127.0.0.1:8081/onebot`、`messagePostFormat=array`，设置 `token`（NapCat 用它做 HMAC-SHA1 签名）。
- 两个适配器都要勾选“保存时启用”。

三个值分别这样来：

- `QQ_ONEBOT_TOKEN`：NapCat HTTP 服务端的 token，不是 QQ 密码；任意随机串，本项目调用 API 时放在 `Authorization: Bearer`。
- `QQ_WEBHOOK_SECRET`：NapCat HTTP 客户端的 token；NapCat 用 HMAC-SHA1 签名，本项目校验 `X-Signature: sha1=...`。两边必须一致。
- `QQ_ALLOWED_GROUPS`：QQ 群号，不是群名。NapCat 重启并登录后执行：
  ```powershell
  uv run --extra native chatbridge qq groups
  ```
  输出里的 `group_id` 就是群号；填一个测试群，多个用逗号分隔。
- `QQ_ALLOWED_PRIVATE_USERS`：允许私聊的 QQ 号。先执行 `qq friends` 查看好友列表，再把 QQ 号填进去；不填则不响应任何私聊。

本项目也会把 `QQ_*` 配置写入本机 `.env`。如果使用 `qq groups` 前还没填 `QQ_ALLOWED_GROUPS`，`qq groups` 仍可运行（它只读群列表，不发送消息）。

## 配置示例

把 `.env.example` 的 QQ 段复制到本机 `.env`：

```dotenv
QQ_ONEBOT_API_URL=http://127.0.0.1:3000
QQ_ONEBOT_TOKEN=你的NapCatToken
QQ_WEBHOOK_HOST=127.0.0.1
QQ_WEBHOOK_PORT=8081
QQ_WEBHOOK_PATH=/onebot
QQ_WEBHOOK_SECRET=一段随机串
QQ_ALLOWED_GROUPS=测试群号
QQ_ALLOWED_PRIVATE_USERS=好友QQ号
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

## 参考音色与训练数据

推荐使用 [大肥鱼（自调少女音色）中文语音数据集](https://www.modelscope.cn/datasets/YinZilu/da-fei-yu-girl-voice-zh)：56 条中文语音、约 9 分 53 秒，带 `metadata.csv` 文本和 `style` 标签，已按 GPT-SoVITS 训练格式整理。当前参考片段选用 `dwg_0033.wav`（4.86 秒），对应文本：

```text
你又订阅别的人工智能了？行吧，我就当没看见。
```

当前本地位置：

```text
data/voices/xiaoai/dataset/train/audio/
data/voices/xiaoai/dataset/train/metadata.csv
data/voices/xiaoai/reference.wav
```

许可为 **CC BY-NC 4.0**，仅限非商业使用；转载或改编需署名 **尹紫鹭幻想社（Bilibili）**，并附数据集链接和许可链接。商业用途需另行取得授权。数据集为合成语音，不等同于 DeepSeek 官方音色。

## 启动与检查

```powershell
uv run --extra native chatbridge qq groups
uv run --extra native chatbridge qq friends
uv run --extra native chatbridge qq check
uv run --extra native chatbridge qq run
```

`qq groups`/`qq friends` 只读取并打印群或好友列表，不发送消息；`qq check` 只检查 OneBot 登录、webhook 端口、群/私聊配置和参考音频是否存在，不发送消息，也不调用 `/tts`。`qq run` 启动 webhook 和 QQ 网关；微信网关仍用原来的 `run` 命令，两个进程互不影响。

## 行为与边界

- 模型通过 `decide_reply` 的 `voice` 字段决定文字或语音；短句、口语、情绪化回复更适合语音，长说明和事实用文字。
- 表情包：QQ 复用微信侧的单帧 GIF 素材库，文字发送确认后最多再发一张，`image` 消息段交给 NapCat；每群/私聊沿用 60 秒冷却和最近 5 张避重。QQ 不依赖微信的 `SendStickerMsg`。
- 私聊：`QQ_ALLOWED_PRIVATE_USERS` 中的好友可以一对一聊天；私聊消息直接回答，发送走 OneBot `send_private_msg`，语音走 `send_private_msg` 的 `record` 段。未列出的好友不响应。
- QQ 语音链路：`GPT-SoVITS /tts → WAV → OneBot record → NapCat SILK → 群/私聊语音条`。
- QQ 看图：成员在群里发图后，在 `QQ_VISION_WINDOW_SECONDS`（默认 120 秒）内 @ 机器人并问图片内容，机器人会读取**该成员最近一张图**；普通图片不会主动上传，其他成员的图不会复用。图片只保存在内存。
- GPT-SoVITS 失败、超时或返回无效 WAV 时回退文字；`record` API 状态不明时不补发文字，避免重复。
- QQ v1 支持文字、私聊、看图、表情包和 GPT-SoVITS 语音条；不支持真 @ 提醒。
- NapCat 是个人号协议，存在账号风险；只使用测试小号，不要把 webhook/API 暴露到公网。
- 目前 QQ 文字已在测试群完成真实收发和回执确认；QQ 看图已用公网测试图经 webhook 验证，真实 QQ 图片仍需用户发一张确认；QQ 语音条仍需 GPT-SoVITS 服务、参考音频和一次群内现场验收。离线测试覆盖事件映射、webhook 签名、OneBot 收发、GPT-SoVITS 请求与语音决策，不能替代语音条现场验收。

## 常见问题

- `qq check` 报 `onebot_unavailable`：NapCat HTTP API 没启动、端口或 token 不对。
- webhook 401：`QQ_WEBHOOK_SECRET` 与 NapCat **HTTP 客户端**的 token 不一致；NapCat 使用 HMAC-SHA1，本项目按 `X-Signature: sha1=...` 校验。
- webhook 403：未设置 secret 时只接受本机请求；跨机器请设置 secret。
- 语音没有声音：确认 GPT-SoVITS 返回 WAV、NapCat 的 ffmpeg/silk 可用，并检查参考音频和 prompt 文本。
- 语音发送状态未知：不要自动重试；先检查 QQ 群实际消息。
