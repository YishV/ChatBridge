"""Turn-level memory extraction, deliberately separate from chat replies."""

from __future__ import annotations

import json
import re

from wechat_ai_bridge.memory.store import MemoryStore


_SYSTEM = (
    '你是私聊长期记忆筛选器。只从“用户说的话”里提取未来仍然有用、稳定、'
    '且用户亲口说过的信息。不要记录临时情绪、玩笑、助手自己的说法，'
    '不要记录密码、住址、财务、健康等敏感隐私。'
    '只输出 JSON：\n'
    '{"action":"none"}\n'
    '或 {"action":"upsert","kind":"preference|identity|event|promise|relationship|note",'
    '"key":"可选的稳定键","content":"一句简短中文事实",'
    '"confidence":0到1,"importance":0到1}\n'
    '或 {"action":"delete","key":"要删除的稳定键或内容片段"}。\n'
    '已经有等价记忆时用 key 更新，不要重复新增。'
)


def _load(raw: str) -> dict | None:
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.strip('`').strip()
        if raw.lower().startswith('json'):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def extract_memory(model, platform: str, user_key: str, user_text: str,
                   assistant_text: str, existing: list) -> dict | None:
    """Return a validated action dict, or None when nothing should change."""
    existing_text = '\n'.join(f'- {item.key or ""}: {item.content}' for item in existing) or '（无）'
    messages = [
        {'role': 'system', 'content': _SYSTEM},
        {'role': 'user', 'content': (
            f'已有记忆：\n{existing_text}\n\n'
            f'用户这轮说：{user_text}\n助手回答：{assistant_text}'
        )},
    ]
    if hasattr(model, 'complete_json'):
        raw = model.complete_json(messages)
    else:
        raw = model.complete(messages)
    data = _load(raw)
    if not data:
        return None
    action = data.get('action')
    if action == 'none':
        return None
    if action == 'delete':
        key = data.get('key')
        if isinstance(key, str) and key.strip():
            return {'action': 'delete', 'key': key.strip()[:100]}
        return None
    if action != 'upsert':
        return None
    kind = data.get('kind')
    content = data.get('content')
    if kind not in ('preference', 'identity', 'event', 'promise', 'relationship', 'note'):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    content = ' '.join(content.split()).strip()[:500]
    key = data.get('key')
    key = key.strip()[:100] if isinstance(key, str) and key.strip() else None
    try:
        confidence = float(data.get('confidence', 0))
        importance = float(data.get('importance', 0.5))
    except (TypeError, ValueError):
        return None
    if not 0 <= confidence <= 1 or not 0 <= importance <= 1 or confidence < 0.7:
        return None
    return {
        'action': 'upsert', 'kind': kind, 'key': key, 'content': content,
        'confidence': confidence, 'importance': importance,
    } if not re.search(
        r'密码|验证码|身份证|银行卡|信用卡|手机号|家庭住址|工资|存款|病历|诊断',
        content,
    ) else None


def apply_memory_action(model, store: MemoryStore, platform: str, user_key: str,
                        user_text: str, assistant_text: str, message_id: str | None = None) -> None:
    """Extract and persist one turn; errors are intentionally swallowed by callers."""
    existing = store.list(platform, user_key, limit=20)
    action = extract_memory(model, platform, user_key, user_text, assistant_text, existing)
    if action is None:
        return
    if action['action'] == 'delete':
        store.delete(platform, user_key, action['key'])
        return
    store.add(
        platform, user_key, action['kind'], action['content'],
        key=action['key'], confidence=action['confidence'],
        importance=action['importance'], source_message_id=message_id,
    )


def summarize_turns(model, existing_summary: str | None, turns: list[tuple[str, str]]) -> str | None:
    """Compress older private-chat turns into a compact rolling summary."""
    transcript = '\n'.join(f'{role}: {text}' for role, text in turns)
    messages = [
        {'role': 'system', 'content': (
            '你是私聊对话摘要器。把新增对话压缩成一段简洁中文摘要，保留：'
            '用户的重要偏好、身份、事件、约定、未完成事项和关系变化；'
            '不要保留临时寒暄、重复内容、密码、住址、财务、健康等敏感信息。'
            '不要编造。只输出摘要正文，不要标题、列表或 Markdown。'
        )},
        {'role': 'user', 'content': (
            f'已有摘要：{existing_summary or "（无）"}\n\n新增对话：\n{transcript}'
        )},
    ]
    text = model.complete(messages)
    if not isinstance(text, str):
        return None
    text = ' '.join(text.split()).strip()[:1200]
    return text or None
