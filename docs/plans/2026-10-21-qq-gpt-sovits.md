# QQ 多平台与 GPT-SoVITS 语音接入

## 已确认的取舍

- 同一个仓库，两个独立进程：`run`（微信）和 `qq run`（QQ）；共用人设、模型和聊天核心，不共用登录态和状态库。
- QQ 走 **NapCat + OneBot 11** 个人测试号；不接 QQ 官方机器人。接受个人号风控风险，只用小号。
- QQ 收事件用 **HTTP POST webhook**（NapCat HTTP Client），发消息用 NapCat HTTP API；不引入 WebSocket 客户端。
- GPT-SoVITS 作为**独立本机 HTTP 服务**接入，不进入本项目 Python 3.12 虚拟环境。
- 模型通过 `decide_reply.voice` 决定文字或语音；语音失败回退文字，状态未知不补发。
- 用户已有 1 分钟以上授权录音和转写；8GB 显存先 zero-shot 跑通，再训练专属音色。

## 代码结构

```text
src/chat_bridge/
  speech/
    gpt_sovits.py     本机 /tts 客户端、WAV 校验、回环地址限制
  qq/
    config.py         QQ_* 配置
    events.py         OneBot 事件 → IncomingMessage
    onebot.py         OneBot HTTP API 客户端，text/record 消息段
    webhook.py        NapCat HTTP POST webhook，HMAC 校验
    sender.py         QQ 文本/语音发送
    gateway.py        事件队列、去重前处理、共享 DeliveryService
```

微信现有 `VoiceSender` 不参与 QQ；QQ 语音不经过 VB-Cable 或 UI 自动化。

## 行为

```text
QQ 群消息/拍一拍
  → webhook → 事件队列 → map_event
  → MessageProcessor / ChatService（voice_enabled=true）
  → decide_reply 返回 reply + voice
  → text: OneBot text 消息段
  → voice: GPT-SoVITS /tts → WAV → OneBot record → NapCat SILK
  → OneBot message_id 作为发送回执
```

- 文字和语音都走 `DeliveryService`；语音使用 `voice_policy=model`，不再按“是否 @”强制语音。
- 只有 `QQ_ALLOWED_GROUPS` 中的群和 `QQ_ALLOWED_PRIVATE_USERS` 中的好友会被处理；群和私聊共用 QQ 状态库但使用不同会话 ID（私聊为 `private:<QQ号>`）。
- 私聊直接回应，发送走 `send_private_msg`；QQ 私聊语音也复用同一 record 逻辑。
- QQ v1 支持文字、私聊、看图、表情包和 GPT-SoVITS 语音条；不支持真 @ 提醒。
- webhook 未设置 secret 时只接受本机请求；设置 secret 后校验 `X-Signature` HMAC-SHA1。

## 配置与命令

见 [QQ 接入说明](../qq-gpt-sovits.md) 和 `.env.example` 的 QQ 段。

```powershell
uv run --extra native chatbridge qq check
uv run --extra native chatbridge qq run
```

## 离线验证

- 新增 GPT-SoVITS 客户端测试：请求体、WAV 校验、回环限制、缺参考音频、坏音频。
- 新增 QQ 测试：@/拍一拍映射、自身消息过滤、webhook HMAC、OneBot text/record 段、网关 tick、QQ 语音回执。
- 新增长期语音决策测试：模型选语音、失败回退文字、未确认不补发。
- 全量离线测试 **403 项通过（2 项平台跳过）**；离线测试用本机假 HTTP 服务，不连接真实 NapCat 或 GPT-SoVITS。
- `qq check` 已对着假 OneBot API 实测通过（`qqReady=true`）；`qq run` 已实测启动并绑定 webhook，`/health` 返回 200，说明命令和进程骨架可运行。
- QQ 文字已现场验证：测试群消息经 NapCat webhook 进入，模型生成回复后通过 OneBot 文字消息发送成功并获得 `message_id` 回执。天气查询也已现场返回 Open-Meteo 实时结果；看图用公网测试图经 webhook 验证；表情包 083 也已现场发送成功并获得 `message_id`。
- 私聊也已完成实际发送验证：测试好友消息经 webhook 进入，回复走 `send_private_msg` 并取得回执。
- 语音条已实际发送并现场确认：v2 整合包 zero-shot `/tts` 成功，私聊和测试群各发出一次 record，均取得 `message_id`，客户端显示为语音条。
- 专属音色训练完成：SoVITS 12 epoch（`da_fei_yu_e12_s372.pth`）、GPT 10 epoch（`da_fei_yu-e10.ckpt`）；API 已加载新权重并生成 `trained.wav`。训练使用大肥鱼数据集 56 条音频，CC BY-NC 4.0，仅非商业使用。

## 待现场验收

1. 安装 NapCat、登录测试小号、配置 OneBot HTTP + webhook，开启群消息全量接收。
2. 启动 GPT-SoVITS `api_v2.py`，准备参考音频/prompt，先 zero-shot；训练完成后切模型。
3. `qq check` 通过后，在测试群发一条普通消息和一条 @ 消息；确认文字、语音条、回执和失败回退。
4. 训练专属音色：用 1 分钟以上授权录音，按 GPT-SoVITS 官方流程切片、ASR、校对、微调；8GB 显存使用小 batch。
5. 观察个人号风控和 NapCat 稳定性；不要用主号，不暴露 webhook 到公网。

## 风险

- NapCat 非官方协议，账号可能被风控；本项目不保存 QQ 密码，但无法消除平台风险。
- GPT-SoVITS 训练/推理依赖 GPU 和独立环境，首次部署周期不可由离线测试替代。
- QQ 语音条最终由 NapCat 转 SILK；WAV 生成成功不等于群内一定显示语音条，必须现场验收。
- 个人号语音/聊天内容涉及隐私和授权；参考音频必须有权使用。
