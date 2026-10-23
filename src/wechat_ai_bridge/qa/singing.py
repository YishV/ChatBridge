"""Model-facing helpers for the QQ song library.

Kept out of ChatService so prompt construction and singing-specific model
calls can evolve without making the chat service larger.
"""

from __future__ import annotations

import json

from wechat_ai_bridge.message import IncomingMessage


class SingingAssistant:
    def __init__(self, model, prompt_loader, platform: str = 'qq'):
        self.model = model
        self.prompt_loader = prompt_loader
        self.platform = platform

    def ack(self, message: IncomingMessage, song_title: str, *,
            random: bool = False) -> str | None:
        try:
            prompt = self.prompt_loader()
            if random:
                situation = (
                    f'用户没有指定歌名，系统从曲库随机选了《{song_title}》。'
                    '不要暗示这是用户点的歌，可以说随机挑了一首；'
                )
            else:
                situation = f'用户点了《{song_title}》。'
            text = self.model.complete([
                {'role': 'system', 'content': (
                    prompt + '\n你刚收到一次 QQ 点歌，歌曲已经进入后台翻唱队列。'
                    + situation +
                    '用一句自然、短的中文接住这次点歌，可以傲娇、吐槽或期待；'
                    '不要用“收到，开始翻唱”这种客服模板，不要声称已经唱完，'
                    '不要发链接、文件路径或技术细节。只输出这句话。'
                )},
                {'role': 'user', 'content': f'这次要翻唱：《{song_title}》。'},
            ])
        except Exception:
            return None
        if not isinstance(text, str):
            return None
        text = ' '.join(text.split()).strip()[:120]
        if not text or text.startswith('{') or text.startswith('['):
            return None
        return text

    def classify(self, message: IncomingMessage, recent_text: str) -> str | None:
        system = (
            '你是 QQ 聊天机器人的点歌意图分类器。只判断最新一条用户消息是否在要求机器人'
            '翻唱/点歌。只输出 JSON：'
            '{"action":"cover","song":"歌名或空字符串","confidence":0到1} 或 '
            '{"action":"none","confidence":0到1}。'
            '只有明确要求机器人唱、翻唱、点一首、随机来一首，才算 cover。'
            '讨论唱歌、评价歌曲、问机器人会不会唱、让其他人唱，都算 none。'
            '没说歌名时 song 为空字符串。'
        )
        user = f'最近对话：\n{recent_text}\n最新消息：{message.text}'
        if hasattr(self.model, 'complete_json'):
            raw = self.model.complete_json([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ])
        else:
            raw = self.model.complete([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ])
        try:
            data = json.loads(raw)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise
        if not isinstance(data, dict) or data.get('action') != 'cover':
            return None
        try:
            confidence = float(data.get('confidence', 0))
        except (TypeError, ValueError):
            return None
        if confidence < 0.6:
            return None
        song = data.get('song')
        if song is None or (isinstance(song, str) and song.strip().lower() in ('', 'null', 'none')):
            return ''
        if not isinstance(song, str):
            return None
        return song.strip()[:100]
