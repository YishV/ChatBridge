"""NapCat/OneBot 11 QQ platform adapter."""

from wechat_ai_bridge.qq.config import QQSettings
from wechat_ai_bridge.qq.events import QQEventError, map_event
from wechat_ai_bridge.qq.gateway import QQGateway
from wechat_ai_bridge.qq.onebot import OneBotClient, OneBotError
from wechat_ai_bridge.qq.sender import QQSender

__all__ = ['QQSettings', 'QQGateway', 'QQSender', 'OneBotClient', 'OneBotError',
           'QQEventError', 'map_event']
