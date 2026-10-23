# 本机运行面板

查看服务状态、翻唱队列、日志尾部，并管理私聊长期记忆。

```powershell
python -m wechat_ai_bridge panel
```

或者：

```powershell
.\scripts\start-panel.ps1
```

关闭：

```powershell
.\scripts\stop.ps1 -Target panel
```

浏览器打开：

```text
http://127.0.0.1:8767
```

面板只允许绑定 `127.0.0.1`/`localhost`/`::1`，没有登录认证，不要反向代理到公网。

## 页面内容

```text
微信网关：通过 SingleInstance 锁判断
QQ webhook：8081
NapCat：3000
GPT-SoVITS：9880
翻唱队列：queued / running / failed / done
私聊记忆：条数、平台、会话、内容、重要度
日志尾部：最近 200 行
```

每 15 秒自动刷新。

## 记忆管理

- 搜索内容或 key
- 删除单条记忆
- 按平台 + 私聊会话清空，同时删除摘要

清空操作不可撤销。

## 日志

默认：

```text
data/runtime/logs/app.log
```

轮转策略：

```text
单文件 5 MiB，保留 5 个备份
```

应用只记录请求 id、错误码、计数和模型状态，不主动写聊天正文。不要用日志文件保存聊天内容。

配置：

```dotenv
WECHAT_AI_BRIDGE_LOG_DIR=data/runtime/logs
WECHAT_AI_BRIDGE_LOG_LEVEL=INFO
```

## JSON 接口

```text
GET /api/health
GET /api/memories?q=冰美式
```

接口同样只监听本机，无认证。
