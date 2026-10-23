# 项目架构

## 一句话

一个仓库、两个独立网关进程，共用聊天核心；微信走 native hook，QQ 走 NapCat/OneBot 11；语音可选接本机 GPT-SoVITS。

## 进程

```text
chatbridge run       微信网关（现有 WeChat + native hook）
chatbridge qq run    QQ 网关（NapCat + OneBot 11）
```

两个进程各自登录、各自重连、各自 SQLite，互不阻塞。也可以由 `scripts/start.ps1 -Target all` 分别拉起。

## 分层

```text
core
  message.py          IncomingMessage / ProcessResult / ChatReply
  dispatch/processor  去重、冷却、配额、自然接话队列
  qa/service          ChatService、历史上下文、模型决策
  state/store         控制、去重、投递记录、聊天历史

platforms
  wechat/             原生 hook、加密消息库、微信 UI 语音
  qq/                 OneBot HTTP/webhook、record/image 消息段、私聊

providers
  media/memes         单帧 GIF 素材与冷却
  weather/            Open-Meteo 查询
  vision/             微信图片归一化与看图
  speech/             GPT-SoVITS 本机 HTTP 客户端
  voice/              微信 VB-Cable + UI 语音条
```

## 消息流

```text
群/私聊消息
  → 平台适配器映射为 IncomingMessage
  → MessageProcessor 去重、冷却、配额
  → ChatService 生成文字 / 表情 / 语音决策
  → DeliveryService 文字确认后按需发图、发语音
  → 平台发送器写回执
```

## 平台能力

| 能力 | 微信 | QQ |
| --- | --- | --- |
| 群文字 | 支持 | 支持 |
| 私聊文字 | 支持（`allowedPrivateIds`） | 支持（`QQ_ALLOWED_PRIVATE_USERS`） |
| 表情包 | 原生 sticker | image 消息段 |
| 看图 | 同群成员最近一张图 | 同成员最近一张图 |
| 语音条 | Edge TTS + VB-Cable + UI | GPT-SoVITS + record |
| 天气 | 支持 | 支持 |
| 真 @ 提醒 | 不支持 | 不支持 |

## 数据边界

- 微信数据库、QQ 登录态、模型密钥、NapCat token 和 webhook secret 不出本机。
- 两个平台不共享消息 ID 和会话状态。
- 图片只按同一成员最近一张、短期内存缓存；不保存聊天图片。
- 公网 webhook 不推荐；NapCat 默认只监听本机。

## 外部依赖

- 微信：固定版本 WeChat 4.1.10.27 + 已核验 native hook。
- QQ：NapCat（个人测试号）+ 独立 Python 3.10 GPT-SoVITS 环境（仅语音需要）。
- 模型：DeepSeek Chat Completions。
- 天气：Open-Meteo；TTS：Edge TTS（微信语音）。
