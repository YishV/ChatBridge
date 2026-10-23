# ChatBridge 命令与脚本总览

常用命令都放在这里，省得去翻脚本。项目旧名 `chatbridge` 仍保留为兼容命令；新命令是 `chatbridge`。

## 1. 首次准备

```powershell
# 安装 Python 依赖（微信 native 额外依赖）
uv sync --extra native

# 第一次使用时复制配置模板
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

`.env.example` 的每个配置项都有注释。

## 2. 启动

```powershell
# QQ + 微信两个网关
.\scripts\start.ps1 -Target all

# 只启动 QQ
.\scripts\start.ps1 -Target qq

# 只启动微信
.\scripts\start.ps1 -Target wechat

# 不等待 NapCat/GPT 就绪
.\scripts\start.ps1 -Target qq -NoWait

# 指定 NapCat 目录和等待秒数
.\scripts\start.ps1 -Target qq -NapCatHome 'tools\NapCat.Shell' -NapCatTimeoutSeconds 60
```

启动时会显示 Web 面板地址：

```text
http://127.0.0.1:8767
```

单独启动面板：

```powershell
.\scripts\start-panel.ps1
```

指定端口：

```powershell
.\scripts\start-panel.ps1 -Port 8787
```

## 3. 停止

```powershell
# 停止 QQ
.\scripts\stop.ps1 -Target qq

# 停止微信
.\scripts\stop.ps1 -Target wechat

# 停止翻唱 worker
.\scripts\stop.ps1 -Target singing

# 停止面板
.\scripts\stop.ps1 -Target panel

# 全部停止
.\scripts\stop.ps1 -Target all
```

## 4. Web 面板

```text
http://127.0.0.1:8767
```

页面包含：

```text
微信网关 / QQ webhook / NapCat / GPT-SoVITS 状态
翻唱队列
私聊记忆查看、删除、按用户清空
最近消息
日志尾部
```

只绑定本机，不要暴露到公网。

JSON：

```text
GET /api/health
GET /api/memories?q=关键词
```

## 5. GPT-SoVITS 语音

```powershell
# 独立启动语音 API（9880）
.\scripts\start-gpt-sovits.ps1

# 指定 GPT-SoVITS 目录
.\scripts\start-gpt-sovits.ps1 -GptHome 'tools\GPT-SoVITS-v2\GPT-SoVITS-v2-240821'
```

音色文件统一放在：

```text
data/voices/<音色名>/
```

当前 xiaoai：

```text
data/voices/xiaoai/rvc.pth
data/voices/xiaoai/gpt.ckpt
data/voices/xiaoai/sovits.pth
data/voices/xiaoai/reference.wav
data/voices/xiaoai/prompt.txt
```

换音色说明见 `data/voices/README.md`。

## 6. 曲库翻唱

```powershell
# QQ 点歌后，消费翻唱队列
.\scripts\start-singing-worker.ps1

# 只处理一个任务
python -m chat_bridge singing-worker --once
```

曲库目录：

```text
data/music/
```

相关配置：

```dotenv
SINGING_ENABLED=true
SINGING_MUSIC_DIR=data/music
SINGING_RVC_MODEL=data/voices/xiaoai/rvc.pth
SINGING_PITCH=0
```

## 7. 表情包

```powershell
# 同步官方表情索引
python -m chat_bridge memes sync

# 查看素材
python -m chat_bridge memes list

# 检查配置
python -m chat_bridge memes check

# 准备单张素材
python -m chat_bridge memes prepare --id 083
```

自动收藏在 `.env` 打开：

```dotenv
CHAT_BRIDGE_MEME_COLLECT_ENABLED=true
```

本地库：

```text
data/memes/library/
```

## 8. 本机调试

```powershell
# 模拟一条群消息，不发送
python -m chat_bridge ask --group 测试群 --sender 测试人 --message-id test-1 --text "你好"

# 天气
python -m chat_bridge weather --city 杭州,浙江省

# 模型连通检查
python -m chat_bridge model-check

# 微信配置检查，不处理消息
python -m chat_bridge run --check

# 微信只轮询一次
python -m chat_bridge run --once

# 微信联系人
python -m chat_bridge contacts --query 昵称

# 微信白名单/暂停
python -m chat_bridge admin allow 群ID@chatroom
python -m chat_bridge admin deny 群ID@chatroom
python -m chat_bridge admin pause
python -m chat_bridge admin resume
python -m chat_bridge admin status

# QQ 检查
python -m chat_bridge qq check
python -m chat_bridge qq groups
python -m chat_bridge qq friends

# 语音条检查（不进入聊天）
python -m chat_bridge voice check

# 显式发一条语音到指定群
python -m chat_bridge voice say --group 群ID --text "测试语音"
```

## 9. 云端短样

```powershell
python -m chat_bridge cover check
python -m chat_bridge cover voices --page 1
python -m chat_bridge cover prepare --input data/music/某首歌.mp3 --song 歌名 --artist 歌手 --start 0 --duration 20 --voice-id 1
python -m chat_bridge cover run --session <会话ID>
python -m chat_bridge cover status --session <会话ID>
```

这是云端短样流程，和本地 RVC 翻唱不是同一条链路。

## 10. 清理缓存和数据库

```powershell
# 预览，不删除
.\scripts\clean.ps1

# 清理缓存和聊天/翻唱数据库，保留长期记忆
.\scripts\clean.ps1 -Apply

# 连长期记忆一起清理
.\scripts\clean.ps1 -Apply -IncludeMemory

# 清理安装包缓存
.\scripts\clean.ps1 -Apply -IncludeDownloads

# 不备份数据库
.\scripts\clean.ps1 -Apply -NoBackup
```

执行前先停止网关、worker 和面板。数据库默认备份到：

```text
data/backups/cleanup-YYYYMMDD-HHMMSS/
```

保护目录见 [cleanup.md](cleanup.md)。

## 11. 测试

```powershell
# 全量离线测试
python -m unittest discover -s tests

# 单个模块
python -m unittest tests.test_local_memes
python -m unittest tests.test_panel

# 所有 CLI 帮助
python -m chat_bridge --help
python -m chat_bridge qq --help
python -m chat_bridge cover --help
```

## 12. 内部脚本

```text
scripts/rvc_runner.py     RVC 渲染内部入口，由翻唱 worker 调用，不要直接运行
scripts/start-singing-worker.ps1  翻唱 worker 的独立窗口启动器
```

## 13. 端口

```text
3000   NapCat OneBot API
8081   QQ webhook
8767   Web 面板
9880   GPT-SoVITS API
30001  微信 native hook
```

## 14. 目录

```text
data/music                 曲库
data/memes                 表情包
data/voices                音色
data/runtime               状态、数据库、缓存
data/knowledge             知识库
config                     人设
.env                       本机配置
```

## 15. 用户私聊命令

在 QQ/微信私聊里发：

```text
记住：我不吃香菜
保存记忆：我不吃香菜
查看记忆
忘记香菜
清空我的记忆
```

## 16. 启动顺序建议

```text
1. .\scripts\start-gpt-sovits.ps1
2. .\scripts\start-panel.ps1
3. .\scripts\start.ps1 -Target qq
4. .\scripts\start-singing-worker.ps1
```

`start.ps1` 会自动尝试拉起 NapCat、GPT-SoVITS 和翻唱 worker；面板需要单独启动。

## 17. 还值得补的运维项

- 定期备份 `data/music`、`data/memes`、`data/voices`，这三个是资源，不是缓存。
- 面板是本地无认证页面，不要做公网端口映射。
- 清理脚本不要改成“删除整个 data/runtime”；`native-hook`、`reader-env`、`sandboxes` 不是缓存。
- 音色切换后要重启 GPT-SoVITS；RVC 路径改动后要重启翻唱 worker。
- `.env` 修改后要重启对应网关。
