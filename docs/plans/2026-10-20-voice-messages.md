# 微信语音条（@/拍一拍）实施记录

## 结论

语音条已走通，不需要 Kits，也不需要改 native hook。当前实现：

```text
@/拍一拍回复文字
  → Edge TTS 生成 MP3（免费在线）
  → 播放到 VB-Cable CABLE Input
  → 临时把默认麦克风切到 CABLE Output
  → 微信窗口前台，点发送按钮左侧的圆形声波图标开始录音
  → 播完再点一次停止并发送
  → 读取目标群消息库，确认新的 local_type=34 自发语音行
```

自然接话仍保持文字，避免群聊被语音刷屏。

## 已验证

- 文件传输助手实际发出语音条，消息库出现 `local_type=34`、正数 `server_id`，元数据 `voicelength=4640ms`。
- 从 `media_0.db` 提取 `VoiceInfo.voice_data`，用 `pilk` 解码为 4.64 秒 WAV，RMS `0.0796`，不是静音。
- 生产 `VoiceSender` 也完成了文件传输助手端到端发送并返回 `confirmed=True`，最新一条约 4.82 秒；搜索侧栏打开时按聊天区域坐标点击，避免把录音图标点进搜索面板。
- 群聊搜索时网页结果排在前面，群聊区在下方；发送时先把选择移入结果列表，再按 `End` 跳到群聊区，最后 `Enter` 打开群，而不是在搜索框里直接回车。搜索没有真正打开时直接回退文字，避免把语音发给网页结果或错误会话。
- `voice check` 当前返回：Edge TTS 可用、CABLE Input/Output 可用、微信窗口可定位、配置群名唯一可解析。
- 全量离线测试覆盖：@ 走语音、自然走文字、语音未确认不补文字、准备阶段失败回退文字、录音开始后失败标记为“可能已发送”。

## 边界与风险

- 微信窗口必须前台可见；发送时会短暂占用鼠标和默认麦克风。不是后台静默能力。
- 语音条依赖 Edge TTS 网络服务，回复文字会发送到 Microsoft 的语音服务；不需要 API key，但需要联网。
- 只对 `@`/拍一拍使用；自然消息不配音。
- 语音准备失败（TTS、设备、窗口、群名、录音开始前）自动回退文字；已经开始录音后状态未知时不补发文字，避免重复。
- 群名必须唯一可解析；重名群会拒绝语音并回退文字，避免发错群。
- 真实群内语音条仍需用户在一次 @ 中现场确认；文件传输助手成功不能等同于群内验收。
- 不使用 Kits、不把音频文件当语音条、不修改 native hook。

## 配置

```dotenv
WECHAT_AI_BRIDGE_VOICE_ENABLED=true
WECHAT_AI_BRIDGE_VOICE_NAME=zh-CN-XiaoxiaoNeural
WECHAT_AI_BRIDGE_VOICE_MAX_CHARS=120
```

安装：

```powershell
uv sync --extra native --extra voice
```

检查（不发送）：

```powershell
uv run --extra native --extra voice wechat-ai-bridge voice check
```

显式发送一条测试语音（会真实发送，默认只建议测试群）：

```powershell
uv run --extra native --extra voice wechat-ai-bridge voice say --group 测试群ID --text "测试语音"
```

## 代码位置

- `voice/tts.py`：Edge TTS 合成与缓存。
- `voice/audio.py`：VB-Cable 播放与默认麦克风切换。
- `voice/wechat_ui.py`：微信窗口、搜索群名、语音图标点击。
- `voice/sender.py`：发送编排与 `local_type=34` 回执确认。
- `wechat/delivery.py`：仅对 directed 消息走语音；准备失败回退文字。
- `wechat/reader.py`：唯一群显示名解析，拒绝重名群。
