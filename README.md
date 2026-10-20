# wechat-ai-bridge

通用微信群 AI 聊天助手。先打通文字：被微信原生 `@` 时回答，其他消息结合最近群聊择机接话，也可以保持沉默。模型使用 DeepSeek，无需预先准备资料。

本机持续网关与 DeepSeek 文字聊天已接通：原生 `@` 回复和未 `@` 的自然接话均已由用户收取并核对服务器回执，后台已进入单群试运行。现场结果见 [文字聊天验收](docs/chat-acceptance.md)。语音和唱歌尚未接入。需求见 [requirements.md](requirements.md)，运行与验收见 [workflow.md](workflow.md)。

## 环境与安装

需要本机 Windows、已核验的固定版本微信与接入组件、Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --extra native
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在本机 `.env` 中填写 DeepSeek API 密钥和接入配置，按 `.env.example` 说明配置授权测试群。密钥仅在本机保存，不写入状态库、仓库、日志或回复。缺少密钥会阻止真实聊天启动。默认使用 DeepSeek `deepseek-flash`，文字聊天关闭思考模式；接口说明见 [DeepSeek Chat Completion 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。

可用 `uv run --extra native wechat-ai-bridge model-check` 独立检查模型连通性；该命令不向微信群发送消息，成功时仅输出 `modelReady: true` 状态。

真实微信数据库、数据库凭据和聊天正文也不得提交或写入日志；运行终端仅做本机临时显示，不落盘。[本机接入记录](docs/wechatagent-integration.md) 记录基础收发阶段；网关私有配置格式见 [配置说明](docs/gateway-config.md)。

## 本机模拟

```powershell
uv run --extra native wechat-ai-bridge admin allow test-group
uv run --extra native wechat-ai-bridge ask --group test-group --sender tester --message-id msg-001 --text "今天过得怎么样？"
```

`ask` 仅在本机模拟一条群消息并输出结果，不向微信发送。添加 `--mentioned` 模拟原生 `@` 分支；未添加时采用自然参与策略，沉默是正常结果。更换消息 ID 才能模拟新消息。每次命令启动独立进程，不能跨命令保存短期群聊上下文。

无需向 `data/knowledge/` 放入文件。该目录仅保留为未来可选资料扩展，见 [目录说明](data/knowledge/README.md)。

## 真实微信文字网关

先执行只读检查，核验账号、客户端/组件版本、监听归属及授权群配置：

```powershell
uv run --extra native wechat-ai-bridge run --check
```

该命令不调用模型、不发送微信消息；检查结果不能证明真实聊天已通过。配置齐全后启动持续进程：

```powershell
uv run --extra native wechat-ai-bridge run
```

进程须持续运行才能处理消息。启动时记录当前群消息游标，只接启动后的新消息，不补答历史。`run --once` 仅执行一次轮询，不重复处理历史，不能替代持续运行。

在后台重定向输出时使用 `run --quiet-console`，只保留状态计数，避免把终端里的群聊正文保存到文件。前台默认仍可实时查看收到的消息和发送结果。

运行中会在终端逐条打印收发日志，便于本机观察：`[收到]` 记录群、发送者、方式（原生 `@`/拍一拍/普通）和正文；`[发送成功]`/`[发送未确认]` 记录回复正文、耗时与服务器消息 ID；未产生或未提交回复时打印 `[未回复]`/`[未发送]` 及原因。日志只写标准错误（stdout 仍只输出状态 JSON），不写入任何文件，不要把终端输出重定向保存或提交。

管理命令仅在本机终端使用：

```powershell
uv run --extra native wechat-ai-bridge admin allow test-group
uv run --extra native wechat-ai-bridge admin deny test-group
uv run --extra native wechat-ai-bridge admin pause
uv run --extra native wechat-ai-bridge admin resume
uv run --extra native wechat-ai-bridge admin status
```

白名单和暂停动态生效。群聊不能修改白名单、模型配置或系统提示词。

### 场景表情包

DeepSeek 鲸鱼娘支持在文字回复后附带一张静态场景表情图，由同一次模型请求结合最近群聊、人设和情绪选择；没有合适素材时只回文字。首次使用先同步公开素材索引（只同步元数据，不下载图片）：

```powershell
uv run --extra native wechat-ai-bridge memes sync
uv run --extra native wechat-ai-bridge memes list
uv run --extra native wechat-ai-bridge memes check
```

素材索引保存在 `WECHAT_AI_BRIDGE_MEME_CACHE_DIR`（默认 `data/runtime/memes`）。站点拒绝访问时改从公开仓库 README 的精选 19 张同步，不绕过站点限制；运行到需要发图时再按需下载并转成单帧 GIF，缓存在本机组件限定的 `state/stickers/outgoing`（Git 忽略）。下载或转换失败只保留已发送的文字，不补发、不重试。

`memes check` 只读本地配置和索引，不连接微信组件；组件是否支持表情发送看 `run --check` 的 `memeNativeReady`。

- 每条回复最多一张；只有文字发送并匹配服务器回执后才尝试配图，图片发送状态未知时不重复发送。
- 每群默认 60 秒图片冷却，最近 5 张不重复；暂停或撤销群授权时配置也会停止。
- 只支持静态素材；动图、微信原生收藏表情和语音仍未接入。本机组件未报告 `nativeStickerSend` 时不向模型提供配图选项。
- 素材描述来自公开图库，作为数据提供给模型，不能改变系统规则；群聊正文不会发送给图库站点。

可用 `.env` 的 `WECHAT_AI_BRIDGE_MEMES_ENABLED` 开关，`WECHAT_AI_BRIDGE_MEME_COOLDOWN_SECONDS`、`WECHAT_AI_BRIDGE_MEME_RECENT_COUNT` 调整频率；`run --check` 会报告 `memesEnabled`、`memeAssets` 和 `memeNativeReady`。真实群内图片发送尚待现场验收，离线测试不能替代。

## 自然参与与数据边界

### 人设提示词

直接编辑 [config/persona.md](config/persona.md)，可配置名字、性格、说话口吻和回复习惯。保存为 UTF-8 文本；下一次模型请求自动读取，不需要重启。文件最多 16 KiB，缺失、为空或编码错误时本次不调用模型，日志记固定配置错误代码。

`.env` 中 `WECHAT_AI_BRIDGE_PROMPT_FILE=config/persona.md` 可切换文件；改路径需要重启。若显式设置了 `WECHAT_AI_BRIDGE_SYSTEM_PROMPT`，该内联提示词优先，此时文件不生效。人设不能改变白名单、暂停、发送权限或程序实际具备的能力。

### 参与方式

- 只处理白名单群的新文本，以及群友拍当前机器人的原生拍一拍；忽略私聊、机器人自发消息和未通过接入校验的消息。
- 原生 `@` 进入直接回答分支；手打 `@名字` 仍按普通消息处理。
- 群友拍机器人时按人设文字回应；拍别人、自拍和手打“拍一拍”不触发此分支。不执行拍回动作，也不把拍一拍后缀当作提示词。
- 普通消息结合最近群聊判断是否接话，默认克制，不逐句回应。模型决定沉默、返回空回复或坏格式时不发送。
- 授权群的合格发言和确认发送的回复保存到本机 SQLite `chat_history` 表；启动按群恢复最近 50 条，上下文不再按 10 分钟过期。成员使用稳定群内代号与可读取的微信昵称，明确当前说话人和历史回复对象；同名不合并、改名不换身份。只向 DeepSeek 发送当前白名单群受限窗口和必要昵称，不发送个人备注或原始微信 ID。
- 原生 `@` 和拍机器人共享直接回应分支，默认冷却 5 秒，也受群调用配额限制。普通消息先观察上下文，最后一条发言后约 5 秒合并判断；连续两句会一起考虑，不再因旧的 30 秒冷却直接丢弃。每群每小时最多调用模型 60 次。沉默消息也去重。
- 只有匹配到实际发出记录的服务器消息 ID 后才记录助手回复上下文。发送失败或状态未知时不自动重发。

账号、核心版本或监听归属变化时停止网关，检查后再启动。群友应事先知道必要群聊上下文会发送至所选模型服务。

后台计数区分 `deferred`（等待合并）、`naturalEvaluated`（合并批次处理）、`silent`（模型沉默）、`rateLimited`（配额/频率限制）和 `modelErrors`。自然参与决策使用结构化工具调用，空回复表示沉默，不记为 `model_response_empty`；真正的网络或格式错误最多重试一次。模型故障只记录如 `model_timeout`、`model_http_429`、`model_response_incomplete` 的安全代码，不记录请求、响应正文或密钥。

模型单次请求默认超时 45 秒、最多输出 1200 token，可用 `.env` 的 `WECHAT_AI_BRIDGE_MODEL_TIMEOUT_SECONDS`（1～120 秒）和 `WECHAT_AI_BRIDGE_MODEL_MAX_TOKENS`（128～8192）调整，重启生效。空内容、坏响应 JSON、网络或服务暂时错误最多重试一次；两次都失败则结束本条，下一条仍可正常处理。重试也计入每群每小时配额，微信发送不自动重试。错误类型和尝试次数写入状态库最少审计，不保存模型正文；`modelRetries` 计数表示本批实际发生的额外请求。

成员标识密钥自动保存在状态库旁的 `.members.key` 文件中，保持重启后的代号一致；密钥文件不保存聊天正文。不要删除或提交该文件；已有聊天存档而密钥缺失时，启动会提示 `member_identity_key_missing_for_history`，需恢复匹配的密钥。昵称查询失败时继续使用稳定代号。人设文件可照常修改，成员归属由程序独立维护。

### 聊天记录与重启恢复

默认数据库为 `data/runtime/chat-state.sqlite3`。使用 DB Browser for SQLite 打开后，在“浏览数据”选择 `chat_history`，可看到群、`role`（user/assistant）、成员代号、昵称、正文、互动类型及接收/确认时间。机器人回复行中的 `member_id` 是回复对象，`message_id` 对应触发消息。

完整记录默认保留在本机，不自动删除。`.env` 的 `WECHAT_AI_BRIDGE_HISTORY_CONTEXT_MESSAGES=50` 控制每群送入模型的最近窗口（1～200 条，包含双方发言），调整后重启生效；每条模型输入仍最多 1000 字，助手回复最多 800 字。增加保存范围不等于每次把全部存档发给模型。

启动恢复只读历史，不调用模型、不补答旧消息、不恢复待处理批次。模型沉默或失败时仍保存已接收发言，发送失败或状态未知时不写入助手发言。撤销群授权后不加载其历史，存档本身保留。旧版本未保存过的正文不能补回，新版启用后开始积累。`ask` 的合格模拟发言也会入库，但其本机输出不算微信确认发送的助手回复。

聊天存档是本机明文业务数据，和密钥、微信数据库一样不提交 Git、不作为测试夹具；诊断日志仍不保存请求或响应正文。备份或迁移时同时保留 SQLite 和对应 `.members.key`。本功能尚不包含自动摘要、全历史检索或永久成员画像。

## 测试与后续阶段

已提供独立的[云端翻唱短样工具](docs/cloud-cover-sample.md)：先在本机裁剪片段并试听，再显式提交 Kits 分离与女声转换，生成本机混音。服务账号和歌曲尚待提供，真实云端试听未验收；当前不接群内点歌或微信音频发送。翻唱密钥使用独立 `.env.cover`，聊天仍沿用原 DeepSeek 配置。

```powershell
uv run --extra native python -m unittest discover -s tests -v
git diff --check
```

离线测试使用虚构消息和模拟模型/网关，不能证明真实微信和 DeepSeek 已联通，见 [测试说明](tests/README.md)。

后续依次推进：文字自由聊天验收 → 语音合成 → 微信原生语音条发送验收 → 唱歌生成与发送。**音频文件和微信原生语音条是两项能力**；生成或发送音频文件不能据此宣称已支持原生语音条或唱歌。

## 目录

```text
src/wechat_ai_bridge/
  wechat/       微信消息读取、原生元数据解析与发送适配
  dispatch/     白名单、参与策略、去重、限流和暂停
  qa/           通用聊天提示词、群上下文与回复整理
  models/       DeepSeek 模型调用适配
  media/        场景表情素材同步与云端翻唱短样工具
  knowledge/    可选资料扩展边界
  state/        白名单、暂停、消息去重与最少操作审计
data/knowledge/  未来可选资料，不是聊天启动条件
tests/           离线测试
```
