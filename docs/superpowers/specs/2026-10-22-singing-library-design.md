# 曲库点歌与 QQ 翻唱任务设计

- 日期：2026-10-22
- 状态：待用户评审
- 范围：QQ 曲库检索、翻唱任务队列、后台 RVC 渲染、MP3/语音条回传
- 不包含：微信侧点歌、用户上传任意歌曲、实时合唱、自动找副歌、商业授权

## 1. 目标

用户在 `data/music/` 放入喜欢的歌曲后，可以通过 QQ 点歌：

- 指定歌名：检索曲库并翻唱该首
- 不指定歌名：从曲库随机选一首
- 翻唱使用现有“大肥鱼”RVC 音色
- 完整 MP3 与 30 秒语音条由配置切换

## 2. 交互契约

### 2.1 指令

```text
/翻唱 天空之城
/随机翻唱
```

### 2.2 自然语言

```text
唱首天空之城
来一首天空之城
随便唱一首
```

### 2.3 触发条件

- 群聊：明确点歌如 `唱首歌`、`唱首天空之城`、`点歌 ...` 可直接触发；普通“唱歌”闲聊仍需 `@` 或 `/` 指令
- 私聊：直接触发
- 未被直接指向的普通群消息不进入点歌流程
- 群聊普通聊天中的“唱歌”不触发，避免误判
- 首版用规则解析指令和自然语言，不让模型自由决定是否点歌；后续如需模型意图识别，再单独接工具

### 2.4 回复

入队成功：

```text
收到，开始翻唱《天空之城》。完成后发给你。
```

曲库为空：

```text
曲库还是空的，先把歌曲放进 data/music。
```

找不到：

```text
没找到《xxx》。最接近的是：天空之城、天空之城（Live）。
```

任务失败：

```text
《天空之城》翻唱失败：人声分离失败。可以换一首或稍后再试。
```

同一会话已有任务：

```text
你已有一首在翻唱，等这首完成再来。
```

## 3. 曲库

### 3.1 位置

默认：

```text
data/music/
```

可通过 `SINGING_MUSIC_DIR` 修改。支持递归子目录。

### 3.2 文件

允许扩展名：

```text
.mp3 .wav .flac .m4a .ogg .opus
```

文件名规则：

- `天空之城.mp3` → 歌名 `天空之城`
- `李志 - 天空之城.mp3` → 歌名 `天空之城`，歌手 `李志`
- 子目录可以作为歌手补充信息，但不强制

### 3.3 扫描限制

- 跳过隐藏文件/目录
- 拒绝符号链接
- 只接受曲库根目录内的路径
- 单文件大小上限 100 MiB
- 音频时长上限 600 秒
- 曲库最多 5000 首，超过报配置错误

### 3.4 匹配

优先级：

1. 文件名完整匹配
2. 去掉扩展名后的完整匹配
3. `歌手 - 歌名` 中的歌名匹配
4. 不区分大小写/空格/常见标点的包含匹配
5. `difflib` 相似度排序

最多返回 3 个候选。匹配不唯一时让用户补充歌名，不随机选。

### 3.5 随机

- 使用 `secrets.SystemRandom`
- 同一会话最近 5 首不重复
- 曲库不足 5 首时允许重复
- 只有一个文件时直接选它

## 4. 任务队列

独立 SQLite：

```text
data/runtime/singing.sqlite3
```

任务字段：

```text
id, conversation_id, is_group, sender_id, query, song_path,
status, created_at, started_at, finished_at, output_path,
error_code, retry_count
```

状态：

```text
queued → running → done
                 → failed
```

规则：

- 单 worker 顺序处理
- 同一会话最多一个未完成任务
- 同一会话重复点同一首时，直接返回已有任务，不重复入队
- 不同会话可以各自入队；worker 开始时先查缓存，命中则直接发送，不重复渲染
- 队列上限 50；超过直接提示稍后再试
- 失败不自动无限重试，最多重试 1 次
- 进程重启后，`running` 任务重置为 `queued`

## 5. 渲染

### 5.1 组件

```text
Demucs 人声分离
  → RVC 音色转换（30 秒分段）
  → 拼接
  → 与伴奏混音
  → loudnorm 响度标准化
  → MP3 320 kbps
```

现有验证过的 RVC 环境：

```text
tools/RVC/
tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe
```

渲染通过独立子进程调用，不把 RVC 依赖装进 QQ 网关进程。

### 5.2 缓存

缓存目录：

```text
data/runtime/covers/
```

缓存键：

```text
sha256(源文件内容) + 模型版本 + 音高设置
```

命中缓存直接发送，不重复分离/转换。

### 5.3 音频约束

- 翻唱输出 MP3 320 kbps
- 语音条取前 30 秒
- 输出最大 30 MiB
- 生成失败不发送半成品

## 6. QQ 发送

### 6.1 新增 OneBot 能力

```text
upload_group_file(group_id, file, name)
upload_private_file(user_id, file, name)
```

文件必须在本机，NapCat 可读。

### 6.2 发送策略

配置：

```dotenv
SINGING_SEND_MP3=false
SINGING_VOICE_EXCERPT_SECONDS=30
```

- `SINGING_SEND_MP3=true`：发完整 MP3 文件
- `SINGING_SEND_MP3=false`：只发前 30 秒语音条
- MP3 上传失败：回退到 30 秒语音条，并说明文件发送失败
- 语音条失败：只回复失败原因，不重复刷屏

## 7. 配置

```dotenv
SINGING_ENABLED=true
SINGING_MUSIC_DIR=data/music
SINGING_SEND_MP3=false
SINGING_VOICE_EXCERPT_SECONDS=30
SINGING_MAX_DURATION_SECONDS=600
SINGING_QUEUE_DB=data/runtime/singing.sqlite3
SINGING_OUTPUT_DIR=data/runtime/covers
SINGING_RVC_HOME=tools/RVC
SINGING_RVC_PYTHON=tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe
SINGING_RVC_MODEL=assets/weights/da_fei_yu_rvc.pth
SINGING_PITCH=12
```

`SINGING_ENABLED=false` 时不扫描曲库、不启动 worker、不响应点歌。

## 8. 进程与启动

新增命令：

```text
chatbridge singing-worker
```

启动脚本：

- `scripts/start.ps1 -Target qq` 在 `SINGING_ENABLED=true` 时额外启动 worker
- worker 使用独立窗口/进程
- 网关只负责入队和回复，不执行 RVC

## 9. 代码边界

```text
src/chat_bridge/singing/
  library.py       扫描、匹配、随机
  intent.py        指令/自然语言解析
  queue.py         SQLite 队列
  renderer.py      调用 Demucs/RVC 子进程
  worker.py        消费队列、发送结果
  messages.py      固定回复文案
```

QQ 侧：

```text
qq/onebot.py       新增文件上传
qq/sender.py       新增文件发送
qq/gateway.py      点歌意图接入
```

## 10. 安全与边界

- 只处理白名单群/私聊
- 曲库路径固定，不接受用户传路径
- 拒绝 `..`、绝对路径、符号链接
- 日志不记录歌曲文件内容
- 不把用户歌曲上传到外部服务
- 曲库和翻唱成品不提交 Git
- 文件夹保留 `README.md`，音频内容不提交
- 数据集/RVC 音色许可：CC BY-NC 4.0，仅非商业
- 歌曲版权由使用者自行确认

## 11. 测试

离线测试：

- 曲库扫描、扩展名过滤、符号链接拒绝
- 精确/模糊匹配、重名、候选排序
- 随机避重
- 指令和自然语言解析
- 群聊未 @ 不触发
- 队列去重、会话并发限制、失败重试
- 缓存命中
- MP3 开关和语音条回退
- 假 OneBot 文件上传回执

真实 RVC/Demucs 只做手动验收，不进入单元测试。

## 12. 验收

1. 在 `data/music/` 放入 `天空之城.mp3`
2. 私聊发送 `/翻唱 天空之城`
3. 收到入队提示
4. worker 生成后：
   - `SINGING_SEND_MP3=true` 收到 MP3 文件
   - `false` 收到前 30 秒语音条
5. 再点同一首，命中缓存，明显更快
6. 发送“随便唱一首”，随机返回一首
7. 群聊不 @ 机器人时不触发
