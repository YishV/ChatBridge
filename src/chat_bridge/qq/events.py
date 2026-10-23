"""Map OneBot 11 group events onto the transport-neutral message contract."""

from chat_bridge.message import IncomingMessage


class QQEventError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value) -> str:
    return value if isinstance(value, str) else ''


def _segments_text(message) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ''
    parts = []
    for segment in message:
        if isinstance(segment, dict) and segment.get('type') == 'text':
            data = segment.get('data')
            if isinstance(data, dict):
                parts.append(_text(data.get('text')))
    return ''.join(parts)


def _mentioned(message, self_id: str) -> bool:
    if not isinstance(message, list):
        return False
    for segment in message:
        if isinstance(segment, dict) and segment.get('type') == 'at':
            data = segment.get('data')
            if isinstance(data, dict) and data.get('qq') is not None and str(data.get('qq')) == str(self_id):
                return True
    return False


def map_event(event: dict, self_id: str | int | None) -> IncomingMessage | None:
    if not isinstance(event, dict):
        raise QQEventError('qq_event_invalid')
    own = str(self_id if self_id is not None else event.get('self_id', ''))
    post_type = event.get('post_type')
    if post_type in ('message', 'message_sent'):
        message_type = event.get('message_type')
        if message_type not in ('group', 'private'):
            return None
        user_id = event.get('user_id')
        if user_id is None:
            return None
        message_id = event.get('message_id')
        if message_id is None:
            return None
        message = event.get('message')
        text = _segments_text(message)
        mentioned = _mentioned(message, own)
        text = text.strip()
        if message_type == 'private':
            if not text:
                return None
            conversation_id = 'private:' + str(user_id)
            mentioned = True
        else:
            group_id = event.get('group_id')
            if group_id is None:
                return None
            if not text and not mentioned:
                return None
            conversation_id = str(group_id)
        sender = event.get('sender') if isinstance(event.get('sender'), dict) else {}
        nickname = sender.get('card') or sender.get('nickname')
        return IncomingMessage(
            message_id=str(message_id), group_id=conversation_id, sender_id=str(user_id),
            text=text or '（@了你）', mentioned_bot=mentioned,
            is_group=message_type == 'group',
            is_from_bot=str(user_id) == own, sender_name=nickname if isinstance(nickname, str) else None,
        )
    if post_type == 'notice' and event.get('notice_type') in ('notify', 'poke'):
        if event.get('sub_type') not in (None, 'poke') and event.get('notice_type') == 'notify':
            return None
        if str(event.get('target_id')) != own:
            return None
        group_id = event.get('group_id')
        user_id = event.get('user_id')
        if user_id is None:
            return None
        stamp = event.get('time', 0)
        if group_id is None:
            conversation_id = 'private:' + str(user_id)
            is_group = False
            message_id = f'poke:private:{user_id}:{stamp}'
        else:
            conversation_id = str(group_id)
            is_group = True
            message_id = f'poke:{group_id}:{user_id}:{stamp}'
        return IncomingMessage(
            message_id=message_id, group_id=conversation_id, sender_id=str(user_id),
            text='拍了拍你', mentioned_bot=False, interaction='pat',
            is_group=is_group,
        )
    return None
