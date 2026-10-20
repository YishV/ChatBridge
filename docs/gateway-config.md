# 本机网关配置

从项目根目录运行。`.env` 的 `WECHAT_AI_BRIDGE_GATEWAY_CONFIG` 指向本地 JSON，默认 `data/runtime/native-hook/gateway.json`。本次现场配置已在本机生成，复制项目时需要重新核验对应环境。

文件包含以下字段，真实值只写本机配置，不写公开示例或 Git：

| 字段 | 含义 |
| --- | --- |
| `expectedExecutable` | 独立旧版微信 `Weixin.exe` 的绝对路径 |
| `sandbox` | Sandboxie 沙盘名称，仅允许字母、数字、下划线和连字符 |
| `sandboxRoot` | 该沙盘数据根目录；读取账号目录必须在其内部 |
| `credentialFile` | 本机读取凭据 JSON 的路径 |
| `tokenFile` | 已部署 native 组件的本机令牌文件路径 |
| `accountId` | 经联系人记录明确匹配的当前账号 ID，不使用昵称推断 |
| `allowedGroupIds` | 授权群的真实 `@chatroom` ID 数组，不填写群名 |
| `hookSha256` | 当前审核并部署的 native DLL SHA-256 |

读取凭据 JSON 含 `ok: true`、`dbPath` 和 `protectedKey`；密钥必须为当前 Windows 用户可解密的 `userdpapi:` 格式。组件准备、用户登录和读取凭据核验沿用 [基础接入记录](wechatagent-integration.md)。网关不接受明文数据库密钥，不扫描其他账号或群。

所有路径应使用当前测试机的实际值。模型密钥和聊天行为参数放在 `.env`，格式见 [配置模板](../.env.example)。`admin allow` 的群 ID 必须同时存在于网关的 `allowedGroupIds`，这两层共同限定可读取和发送的范围；动态 `deny` 或 `pause` 会阻止后续处理。

后台日志只输出状态、错误代码和计数。需要暂停时执行：

```powershell
uv run --extra native wechat-ai-bridge admin pause
```

恢复使用 `admin resume`；账号/版本/监听归属异常导致网关退出时，先修复环境，再重新执行 `run`。同一状态库对应的网关进程有操作系统文件锁，不能重复启动两份。
