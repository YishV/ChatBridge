# 清理缓存和数据库

脚本：

```powershell
.\scripts\clean.ps1
```

默认只预览，不删除。

## 正式清理

先停止网关、worker 和面板：

```powershell
.\scripts\stop.ps1 -Target qq
.\scripts\stop.ps1 -Target wechat
.\scripts\stop.ps1 -Target singing
.\scripts\stop.ps1 -Target panel
```

然后：

```powershell
# 清理缓存和聊天/翻唱数据库，保留长期记忆
.\scripts\clean.ps1 -Apply

# 同时清理长期记忆
.\scripts\clean.ps1 -Apply -IncludeMemory

# 同时删除 data/runtime/downloads 里的安装包缓存
.\scripts\clean.ps1 -Apply -IncludeDownloads

# 不备份数据库（默认会备份到 data/backups/cleanup-*）
.\scripts\clean.ps1 -Apply -NoBackup
```

如果确认服务已停止但脚本仍报占用，可加 `-Force`。

## 会清理

```text
data/runtime/logs
data/runtime/cover
data/runtime/cover-verification
data/runtime/covers
data/runtime/vision
data/runtime/vision-trigger-*
data/runtime/voice-cache
data/runtime/voice-check
data/runtime/voice-e2e
data/runtime/voice-test
data/runtime/qq-voice
data/runtime/singing
.pytest_cache
.ruff_cache
__pycache__ / *.pyc

data/runtime/chat-state.sqlite3
data/runtime/qq-chat-state.sqlite3
data/runtime/singing.sqlite3
data/runtime/memory.sqlite3     仅 -IncludeMemory
data/runtime/downloads          仅 -IncludeDownloads
```

## 默认保护

```text
data/music                    曲库
data/memes                    表情包（QQ/微信共享）
data/voices                   音色参考、权重、训练样本
state/stickers                表情素材
data/runtime/gpt-sovits       音色参考、训练样本、音色缓存
tools/RVC/assets/weights      RVC 模型
tools/GPT-SoVITS-v2           GPT-SoVITS 权重与运行环境
tools/NapCat.Shell            NapCat 登录/运行文件
data/runtime/native-hook      微信凭据和白名单
data/runtime/reader-env       微信读取环境
data/runtime/sandboxes        沙箱运行环境
data/runtime/wechat-*         微信 runtime
data/runtime/*-reference      外部参考仓库
data/knowledge                知识库
config                        人设和配置
.env                          本机真实配置
data/runtime/memory.sqlite3   长期记忆，默认保护
data/runtime/downloads        安装包缓存，默认保护
```

长期记忆默认不删，因为它不是“缓存”，而是用户数据。

## 仍然不建议清理

```text
data/runtime/gpt-sovits      重新准备参考音频和训练样本很麻烦
tools/RVC/assets/weights     RVC 模型，删了就要重新训练/拷贝
tools/GPT-SoVITS-v2          权重和 runtime，删了恢复成本高
.env                         真实密钥、白名单和端口
data/runtime/native-hook     微信登录凭据和 gateway 配置
data/music                   曲库
data/memes                   收藏的表情包
data/voices                  音色参考、权重和训练样本
```

这些不在清理目标里，脚本不会碰。
