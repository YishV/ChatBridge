# 自然参与空响应修复（改用决策工具调用）

## 现象

用户反馈自然接话批次频繁出现：

```text
[未回复] ... reason=模型错误(model_response_empty)
{"modelErrors": 1, "naturalEvaluated": 1, "modelRetries": 1}
```

重试已经发生（`modelRetries=1`），但第二次仍失败，问题不是偶发。

## 诊断

- 旧实现让自然参与走 DeepSeek JSON Output（`response_format={"type":"json_object"}`），客户端把 `finish_reason=stop` 且 `content.strip()` 为空判为 `model_response_empty`；处理器按可重试错误重试一次。
- 2026-10-20 用真实 `deepseek-flash` 与当前人设做本机探测（合成消息，不发微信）：
  - 同一长人设下，6/6 次 JSON Output 请求返回纯空白（10～25 个空格），`finish_reason=stop`，`reasoning_content` 为空；普通文本请求正常返回中文。
  - 替换为最小 JSON 提示后可以正常返回 `{"ok": true}` / `{"reply": null}`，说明不是模型或账号完全不可用，而是**长人设 + JSON Output** 稳定触发空内容。
  - DeepSeek 官方文档也说明 JSON Output 可能返回空内容，只建议改提示词缓解；重试同一请求没有作用。
- 因此这不是可以靠“多试几次”解决的间歇故障；原有 1 次重试只会重复空白响应并消耗每群配额。

## 修复

- `models/client.py` 新增 `complete_decision()`：用强制的 `decide_reply` function call 返回 `reply` / `meme_id`，不再使用 `response_format`。
  - `reply=""` 表示沉默，是合法决策，不是错误；`reply:null` 归一为沉默。
  - 工具参数缺失、格式错误、错误工具名、超长或空响应仍保留安全错误码；真正可恢复错误仍由处理器重试一次。
  - 工具参数中的额外字段被忽略；`meme_id` 仍由聊天服务按候选列表校验。
- `qa/service.py` 在存在 `complete_decision` 时优先调用工具；自然消息、原生 `@` 配图和拍一拍配图都走该路径。没有该能力的模型/测试桩继续回退原有 JSON 文本路径。
- 决策提示改成工具语义：适合接话时 `reply` 填正文，不适合时填空字符串；不再要求模型在普通 content 里输出 JSON。
- 真实探测：合成自然消息可正常接话，私人行程闲聊可返回 `reply=""` 沉默，`@` 消息可回复并选择表情；未再出现 `model_response_empty`。探测未向任何微信群发送消息。

## 验证

- 新增决策客户端与聊天服务测试：强制工具调用、空回复沉默、`null` 归一、额外字段、坏参数、错误工具名、截断、纯文本回退。
- 276 项离线测试通过（含既有 JSON 回退路径）。真实模型探测使用合成内容和本机配置，只验证响应形态，不构成真实群聊验收。
