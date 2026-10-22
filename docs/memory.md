# 私聊长期记忆

QQ 和微信私聊支持按用户保存长期记忆。群聊不读取、不注入私聊记忆。

## 开关

```dotenv
WECHAT_AI_BRIDGE_MEMORY_ENABLED=true
WECHAT_AI_BRIDGE_MEMORY_DB=data/runtime/memory.sqlite3
```

修改 `.env` 后重启对应网关。QQ 和微信共用一个数据库，但用 `platform` 字段隔离。

## 用户指令

私聊里直接发：

```text
记住：我不吃香菜
保存记忆：我不吃香菜
查看记忆
忘记香菜
清空我的记忆
```

- `记住` / `保存记忆` / `记下来` 会立刻写入，不调用模型。
- 普通私聊也会自动判断，不需要每次都说“保存记忆”。
- `查看记忆` 只显示当前私聊用户自己的记忆和摘要。
- `忘记` 按关键词删除。
- `清空我的记忆` 删除该用户在对应平台的全部记忆、摘要和这段私聊的原始聊天记录。

## 自动记忆

每轮私聊回复完成后，后台异步判断这轮有没有值得长期保存的信息：

```text
稳定偏好、身份、重要事件、约定、关系变化
```

以下内容默认不记：

```text
临时情绪、玩笑、助手自己的说法
密码、住址、财务、健康等敏感信息
低置信度内容
```

自动判断失败不会影响正常回复，也不会写入半条错误记忆。

## 回复时怎么使用

只在私聊里注入：

```text
这个用户的长期记忆
+ 较早对话摘要
+ 最近 50 条原始对话
```

最多注入少量最相关记忆，避免把整个数据库塞进提示词。群聊完全不注入私聊记忆。

## 摘要

每 30 轮回复左右，后台把最近对话压缩成一段摘要，保存到 `conversation_summaries`。这样超过 50 条上下文后，较早的背景还在。

## 数据表

```text
memories
  platform / user_key / kind / memory_key / content
  confidence / importance / source_message_id / created_at / updated_at

conversation_summaries
  platform / user_key / summary / message_count / updated_at
```

`user_key` 使用私聊会话 ID，例如：

```text
qq:private:123456
wechat:private:wxid_xxx
```

QQ 和微信的同一个人默认不合并，避免隐私串线。

## 隐私边界

- 只对白名单内的私聊生效
- 群聊不读取私聊记忆
- 数据库在本地 `data/runtime/`，已被 `.gitignore` 忽略
- 支持用户主动查看、忘记、清空
- 敏感信息默认不自动保存
- 模型调用会把当前对话和已有记忆发送给模型服务商，这是使用云端模型的必要代价

## 查看数据库

```powershell
sqlite3 data/runtime/memory.sqlite3 "select platform,user_key,kind,content from memories;"
```

没有 `sqlite3` 命令时，可以用 DB Browser for SQLite 打开。
