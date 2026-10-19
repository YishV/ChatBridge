# wechat-ai-bridge

《燕云十六声》微信群 AI 助手的项目骨架。需求与试运行边界见 [requirements.md](requirements.md)，流程与部署准入见 [workflow.md](workflow.md)。当前仅定义模块边界，不包含微信 Hook、模型调用或可运行的机器人服务。

## 环境

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

安装项目：

```powershell
uv sync
```

复制 `.env.example` 为本地 `.env` 后填入测试配置。`.env` 已被 Git 忽略；配置读取逻辑尚未实现。

## 目录

```text
src/wechat_ai_bridge/
  wechat/      微信消息收发适配；不负责回答
  dispatch/    白名单、@ 触发、去重、排队、限流和暂停
  qa/          资料检索、提示词构造和回复整理
  models/      DeepSeek 与其他模型提供商的适配边界
  knowledge/   已审核资料及来源元数据的读取
  state/       短期上下文、持久配置和运行指标
data/knowledge/  群主审核的资料文件
tests/           后续实现的自动化测试
```

模块间按 [需求草案的技术边界](requirements.md#5-技术边界) 连接。微信接入可替换，模型层独立于具体微信 Hook 或 DSH。管理员配置和普通群消息分开处理。

## 开发阶段

1. 本机 Windows 验证测试账号、测试群与微信接入组件。
2. 独立实现调度、资料和模型适配，用手工入口测试问答。
3. 联调一个白名单测试群，验证去重、暂停、故障和费用。
4. 通过云端准入检查后才迁移微信进程；失败时保留本机网关。

详见 [workflow.md](workflow.md)。启用前需取得群主授权并告知群友数据流向。
