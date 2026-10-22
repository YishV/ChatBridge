"""Turn a parsed QQ request into a queued singing job."""

from pathlib import Path

from wechat_ai_bridge.singing.intent import looks_like_singing, parse_request
from wechat_ai_bridge.singing.library import SongLibrary
from wechat_ai_bridge.singing.queue import QueueBusy, SingingQueue


class SingingCoordinator:
    def __init__(self, settings, library: SongLibrary, queue: SingingQueue, sender,
                 ack=None, classifier=None):
        self.settings = settings
        self.library = library
        self.queue = queue
        self.sender = sender
        self.ack = ack
        self.classifier = classifier

    def _classify(self, message):
        if self.classifier is None:
            return parse_request(
                message.text, is_group=message.is_group, mentioned=message.mentioned_bot
            )
        try:
            song = self.classifier(message)
        except Exception:
            return parse_request(
                message.text, is_group=message.is_group, mentioned=message.mentioned_bot
            )
        if song is None:
            return None
        from wechat_ai_bridge.singing.intent import SingingRequest
        return SingingRequest(song or None)

    def handle(self, message) -> bool:
        if not self.settings.enabled:
            return False
        if getattr(message, 'is_from_bot', False):
            return False
        if self.classifier is not None and not message.text.lstrip().startswith('/') \
                and looks_like_singing(message.text):
            request = self._classify(message)
        else:
            request = parse_request(
                message.text, is_group=message.is_group, mentioned=message.mentioned_bot
            )
        if request is None:
            return False
        songs = self.library.scan()
        if not songs:
            self.sender.send_group_text(message.group_id, '曲库还是空的，先把歌曲放进 data/music。')
            return True
        if request.random:
            song = self.library.random(
                exclude={Path(path) for path in self.queue.recent_paths(message.group_id)}
            )
            if song is None:
                self.sender.send_group_text(message.group_id, '曲库里暂时没有可翻唱的歌。')
                return True
        else:
            matches = self.library.match(request.query or '')
            if not matches:
                self.sender.send_group_text(
                    message.group_id, f'没找到《{request.query}》。'
                )
                return True
            if len(matches) > 1 and matches[0].title == matches[1].title:
                names = '、'.join(song.title for song in matches)
                self.sender.send_group_text(
                    message.group_id, f'找到多首《{request.query}》：{names}，请补充歌手或路径。'
                )
                return True
            song = matches[0]
        try:
            self.queue.enqueue(message.group_id, message.is_group, message.sender_id,
                               request.query or song.title, str(song.path))
        except QueueBusy:
            self.sender.send_group_text(message.group_id, '你已有一首在翻唱，等这首完成再来。')
            return True
        ack_text = None
        if self.ack is not None:
            try:
                ack_text = self.ack(message, song.title, random=request.query is None)
            except Exception:
                ack_text = None
        self.sender.send_group_text(
            message.group_id,
            ack_text or f'收到，开始翻唱《{song.title}》。完成后发给你。',
        )
        return True
