# 曲库翻唱

## 曲库

默认目录：

```text
data/music/
```

支持 `mp3/wav/flac/m4a/ogg/opus`，可以放子目录。文件名即歌名；`歌手 - 歌名.mp3` 会识别歌手。

## 点歌

```text
/翻唱 天空之城
/随机翻唱
唱首天空之城
```

- 群聊明确点歌（`唱首歌`、`唱首天空之城`、`点歌 ...`）可直接触发；普通“唱歌”闲聊仍不触发
- 私聊可以直接说
- 没指定歌名时随机选一首
- 找不到会返回最接近的候选

## 配置

```dotenv
SINGING_ENABLED=true
SINGING_MUSIC_DIR=data/music
SINGING_SEND_MP3=false
SINGING_VOICE_EXCERPT_SECONDS=30
SINGING_MAX_DURATION_SECONDS=600
SINGING_RVC_HOME=tools/RVC
SINGING_RVC_PYTHON=tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe
SINGING_RVC_MODEL=assets/weights/xiaoai.pth
SINGING_TORCH_HOME=tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/cache/torch
SINGING_PITCH=0
```

- `SINGING_SEND_MP3=true`：发完整 MP3 文件
- `false`：只发前 30 秒语音条
- 改了配置后重启 QQ 网关和 worker

## 启动

```powershell
.\scripts\start.ps1 -Target qq
```

脚本会在 `SINGING_ENABLED=true` 时额外启动 worker。也可以单独启动：

```powershell
python -m wechat_ai_bridge singing-worker
```

## 处理流程

```text
QQ 点歌
  → 曲库匹配/随机
  → SQLite 队列
  → 立即回复“开始翻唱”
  → worker: Demucs 分离 → RVC 转换 → 混音
  → 发送 MP3 或 30 秒语音条
```

成品缓存在 `data/runtime/covers/`。同一首歌第二次点歌直接命中缓存，不重复渲染。

## 失败与限制

- 曲库为空、找不到歌、重复点歌都有固定回复
- 同一会话一次只处理一首
- RVC/Demucs 失败不发送半成品
- MP3 上传失败会回退到 30 秒语音条
- 长任务在独立 worker 中执行，不阻塞 QQ 网关

## 版权

- 歌曲版权由使用者自行确认
- 大肥鱼 RVC 音色数据集为 **CC BY-NC 4.0**，仅限非商业使用
- 翻唱成品和曲库不提交 Git
