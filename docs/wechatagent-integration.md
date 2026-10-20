# WeChatAgent 接入调查与现场验证（2026-10-20）

调查基于 [sourcepro5/WeChatAgent](https://github.com/sourcepro5/WeChatAgent) 提交 `57cb275cc17ed3f6880cf4797f8d6121040ce280`。本文件保留 2026-10-20 较早的基础接入阶段记录：真实读取、一次文本发送与服务器回执，以及原生 @ 和手打文字的差异。之后的持续网关与通用文字聊天已接通，当前状态见 [文字聊天验收](chat-acceptance.md)。下文的未接通/关闭状态仅描述基础阶段当时情况。

## 上游运行边界

- 上游当前 README 标记 `0.3.7-fix`，匹配 Windows x64 微信 `4.1.10.27`。公开源码不附带微信程序、第三方 Hook、编译好的原生组件或完整运行环境。[安装及源码说明](https://github.com/sourcepro5/WeChatAgent#安装使用)
- [Hook 说明](https://github.com/sourcepro5/WeChatAgent/blob/main/docs/WECHAT_HOOK.md)要求本机回环监听、令牌检查、账号与核心版本核对；发送后须核对新的匹配发出记录和服务器消息 ID。超时状态未知时不能直接重发。
- 上游问答依赖 DSH；本项目的模型层仍应独立于 DSH。不能将 WeChatAgent 的桌面程序直接当作本项目的 Python 库。

## 观察到的桥接契约

- `scripts/hook-onebot.mjs` 把读取服务的消息转成 OneBot 风格事件，经本机反向 WebSocket 发送。群消息包含 `post_type=message`、`message_type=group`、`message_id`、`group_id`、`user_id`、`self_id` 与消息段数组。
- 上游的 `group_id` 是映射后的数字 ID，不等于原始微信 `@chatroom` ID。白名单和发送目标必须使用同一套映射，不应混用群名称或原始 ID。
- 上游通过消息文本中的 `@昵称` 推断 `at` 段，不能把它当作微信原生艾特的证明。本项目不采用这一推断。
- 本项目的接收规范要求读取组件提供明确的被艾特账号 ID 列表；缺失时即使正文出现 `@机器人` 也不触发。现场样本已证明原生 `source` 字段包含可区分两者的 `atuserlist`，但上游 Reader 的默认消息字段未透传它，需要本项目单独解析。
- `send_group_msg` 接收文本段和数字 `group_id`；发送成功的上游响应须包含 `receipt=wechat-server-id`。接口超时或回执不明时，先查实际发出记录，不自动重发。

以上字段核对自上游 [`hook-onebot.mjs`](https://github.com/sourcepro5/WeChatAgent/blob/main/scripts/hook-onebot.mjs) 和 [`wechat-onebot-adapter.js`](https://github.com/sourcepro5/WeChatAgent/blob/main/components/qq-bridge/src/adapters/wechat/wechat-onebot-adapter.js)。

## 第 0 阶段现场验收

本机保留日用新版微信；另已准备腾讯签名有效的 `4.1.10.27` 客户端，并安装 Sandboxie Plus `1.18.8`。最初的 `WeChatBot` 沙盘曾自动更新到 `4.1.15.56`，其数据已保留。当前测试使用新建的 `WeChatBot411027` 沙盘，对独立旧版程序目录设置 `ReadFilePath`，固定程序文件版本；账号数据仍写入该沙盘。用户已重新登录，新旧版进程同时运行。

现场核对的是实际加载的 `Weixin.dll`，不能仅看宿主目录中的 `Weixin.exe` 文件版本。2026-10-20 已核对沙盘归属、实际核心 `4.1.10.27`、监听进程与已登录账号一致。沙盘只读目录的行为见 [Sandboxie ReadFilePath 文档](https://sandboxie-plus.github.io/sandboxie-docs/Content/ReadFilePath/)。

客户端、沙盘数据和第三方组件均存放于被 Git 忽略的 `data/runtime/`，不随项目源码上传。测试群名、真实群 ID、账号 ID 和本机路径只保存在本地准备记录中。联系人查询已精确匹配到唯一测试群与当前账号；现场消息查询和测试发送均限定到该真实群 ID。数据库读取凭据使用当前 Windows 用户的 DPAPI 保护，原始密钥不写入文件或日志。

上游 [aixed/WeChat-Hook](https://github.com/aixed/WeChat-Hook) 原版默认监听 `0.0.0.0:30001`，且公开说明中未给接口鉴权。原始 Release DLL 仅下载校验，未部署或加载。经用户批准，已安装 Visual Studio 2022 Build Tools `17.14.42`、MSVC v143 和 Windows SDK `10.0.22621`，按 WeChatAgent 的构建脚本与覆盖层编译固定提交 `e905d07ade50d2c6472e4eb3bd4f3fe19cf662c6`。

部署的自编译 `version.dll` SHA-256 为 `9278c9f226ca09e5107e8478f45144a6a67e632ef5974837b35e8a7a360778a6`。读取组件来自固定提交的 weflow-cli；SQLCipher 与 DPAPI 已通过实际数据库读取核验。第三方组件仍保留在本机，不将其二进制或源码混入本项目发行包。

| 验证项 | 2026-10-20 结果 |
| --- | --- |
| 本机监听 | 仅 `127.0.0.1:30001`，属指定沙盘中的旧版微信进程 |
| 鉴权 | 无令牌、错误令牌均返回 401；正确令牌返回 200 |
| 账号与版本 | 核心 `4.1.10.27`；健康接口只有一个账号，与读取目录匹配 |
| 测试群定位 | 群名精确匹配唯一联系人，使用真实群 ID 定向读取和发送 |
| 收消息 | 用户从另一账号发出的 A/B 两条群文本均读到，带服务器消息 ID |
| 原生 @ | A 的 `source` 存在 `atuserlist`，其中含机器人账号；B 无此元素 |
| 发消息 | 提交一次带唯一编号的测试文本，取得匹配的自发记录和正数服务器消息 ID |
| 样本本地重放 | 新解析器正确识别 A、忽略 B 与自发消息；白名单、暂停、重复消息及状态库重开后去重通过 |
| 发送次数核对 | 本地尝试次数为 1，同群匹配测试发出记录为 1；重复执行验证只读取已确认回执 |
| 自动问答 | 关闭；未连接模型、未开启持续监听或自动回复 |

### 现场字段与回执约束

固定版本群消息表提供 `source`、`packed_info_data`、`WCDB_CT_source` 等字段。A/B 样本的 `source` 以压缩字节保存，解压后为 `msgsource` XML；只读取其直属 `atuserlist` 的明确账号列表。字段损坏、未知格式或缺失时保持不触发；不能回退到正文匹配。样本正文和完整原生元数据不保存到项目测试夹具，仅使用虚构账号构造回归样本。

项目的 `wechat.native` 纯解析模块已实现该映射：压缩输入、解压窗口和解压结果各限制为 64 KiB，严格 UTF-8 解码，拒绝 NUL、DTD/实体声明、嵌套或重复提及列表。NUL 拦截同时覆盖无 BOM 的 UTF-16 与压缩包裹输入，避免 XML 解析器自动识别其他编码后绕过声明检查。消息身份由账号、群 ID 与正规化的正数服务器消息 ID 构造；本人判断使用解析后的发送者账号精确相等。该模块不打开数据库、不运行后台服务，读取层仍须校验数据来源和账号范围。

执行 `uv run --extra native python -m unittest discover -s tests -v`，41 项离线测试通过。另将本次真实 A/B 及测试发出记录经新解析器、消息规范化和调度器在临时本地状态库中重放，所有检查通过，模型调用为 0，未为重放创建发送器。状态库关闭再重开后的去重结果只证明持久去重规则，不能替代未来网关进程重启和断线恢复验收。

`POST /SendTextMsg` 的 `ret=0` 只表示原生调用已受理。确认必须查询同群新发出记录：发送者严格等于测试账号、完整文本匹配唯一编号、记录不在提交前基线中，且 `server_id` 为正数。基线身份同时包含分片名和本地 ID。现场微信时间比本机提交时间早约 6 秒，回执查询允许 30 秒时间容差，并继续使用上述条件，避免把时钟偏差当成发送失败。尝试记录在发送前落盘；重复运行只查回执，未确认时也不重发。

### 进入第 2 阶段前仍需完成

1. 将独立的 Python 网关与本项目调度、暂停及发送状态库连接；不要直接信任上游按正文生成的 OneBot `at` 段。
2. 完成实时轮询或事件推送、启动游标、账号变更停止、跨重启去重与故障恢复验证。
3. 在用户提供已审核资料和模型配置后，用同一白名单测试群验证端到端回答、费用与群级限流。
4. 持续运行和掉线恢复尚未验收；本次收发测试不等于可以直接迁移云端。
