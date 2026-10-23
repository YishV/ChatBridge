# 本地表情包收藏

开启后，QQ 和微信里出现的表情包会自动下载到同一个本地库；回复时优先使用本地收藏。

## 开关

全部在 `.env`，不在 Web 页面改：

```dotenv
# 是否自动收藏聊天中出现的表情包
WECHAT_AI_BRIDGE_MEME_COLLECT_ENABLED=false
# 是否也处理私聊图片
WECHAT_AI_BRIDGE_MEME_COLLECT_PRIVATE=true
# 每天最多收藏多少张
WECHAT_AI_BRIDGE_MEME_COLLECT_MAX_PER_DAY=20
# AI 判断为表情包的最低置信度
WECHAT_AI_BRIDGE_MEME_COLLECT_MIN_SCORE=0.75
# 回复时优先使用本地收藏
WECHAT_AI_BRIDGE_MEME_LOCAL_FIRST=true
# 本地库目录
WECHAT_AI_BRIDGE_MEME_LIBRARY_DIR=data/runtime/memes/library
```

改完重启微信网关或 QQ 网关。

## 采集流程

```text
允许的群/私聊收到图片
  → 格式、尺寸、大小检查
  → SHA-256 去重
  → 视觉模型判断
  → 过滤真人、截图、二维码、隐私内容
  → 转成 JPEG，去 EXIF
  → 写入共享库
```

只有同时满足以下条件才保存：

```text
is_meme = true
safe = true
score >= 配置阈值
```

## 共享库

```text
data/runtime/memes/library/
  blobs/<sha256>.jpg
  index.sqlite3
```

QQ 和微信共用，不按平台分目录。索引只记录来源平台，不记录发送者身份。

## 使用

```text
本地收藏
  > 官方表情素材
  > 不发表情
```

本地素材参与模型候选；选了以后从本地文件发送，不依赖远程 CDN。

## 边界

- 只处理白名单群和私聊
- 私聊可单独关闭
- 每天有上限
- 真人人像、截图、二维码、隐私文字由视觉模型过滤
- 图片会发送给当前配置的视觉模型进行判断，不是纯本地处理
- 不支持动画 GIF，第一版只保留静态图
- 表情包只做本地使用，不要公开转发

## 查看和清理

Web 页面只展示运行状态、日志、最近消息和私聊记忆；表情库不提供 Web 管理入口。

查看文件：

```powershell
dir data\runtime\memes\library\blobs
```

清空本地收藏：

```powershell
Remove-Item -Recurse -Force data\runtime\memes\library
```

下次启动会重新创建空库。
