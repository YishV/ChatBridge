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

## 自然参与与数据边界

### 人设提示词

直接编辑 [config/persona.md](config/persona.md)，可配置名字、性格、说话口吻和回复习惯。保存为 UTF-8 文本；下一次模型请求自动读取，不需要重启。文件最多 16 KiB，缺失、为空或编码错误时本次不调用模型，日志记固定配置错误代码。

`.env` 中 `WECHAT_AI_BRIDGE_PROMPT_FILE=config/persona.md` 可切换文件；改路径需要重启。若显式设置了 `WECHAT_AI_BRIDGE_SYSTEM_PROMPT`，该内联提示词优先，此时文件不生效。人设不能改变白名单、暂停、发送权限或程序实际具备的能力。

### 参与方式

- 只处理白名单群的新文本，以及群友拍当前机器人的原生拍一拍；忽略私聊、机器人自发消息和未通过接入校验的消息。
- 原生 `@` 进入直接回答分支；手打 `@名字` 仍按普通消息处理。
- 群友拍机器人时按人设文字回应；拍别人、自拍和手打“拍一拍”不触发此分支。不执行拍回动作，也不把拍一拍后缀当作提示词。
- 普通消息结合最近群聊判断是否接话，默认克制，不逐句回应。模型决定沉默、返回空回复或坏格式时不发送。
- 每群共享最多 20 条、最长 10 分钟的内存上下文，群间隔离。模型中的成员使用群内匿名代号，只向 DeepSeek 发送白名单群必要上下文，不维护完整群聊档案。
- 原生 `@` 和拍机器人共享直接回应分支，默认冷却 5 秒，也受群调用配额限制。普通消息先观察上下文，最后一条发言后约 5 秒合并判断；连续两句会一起考虑，不再因旧的 30 秒冷却直接丢弃。每群每小时最多调用模型 60 次。沉默消息也去重。
- 只有匹配到实际发出记录的服务器消息 ID 后才记录助手回复上下文。发送失败或状态未知时不自动重发。

账号、核心版本或监听归属变化时停止网关，检查后再启动。群友应事先知道必要群聊上下文会发送至所选模型服务。

后台计数区分 `deferred`（等待合并）、`naturalEvaluated`（合并批次处理）、`silent`（模型沉默）、`rateLimited`（配额/频率限制）和 `modelErrors`。模型故障只记录如 `model_timeout`、`model_http_429`、`model_response_incomplete` 的安全代码，不记录请求、响应正文或密钥。

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
  knowledge/    可选资料扩展边界
  state/        白名单、暂停、消息去重与最少操作审计
data/knowledge/  未来可选资料，不是聊天启动条件
tests/           离线测试
```
