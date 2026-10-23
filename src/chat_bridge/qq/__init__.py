"""NapCat/OneBot 11 QQ platform adapter."""

from chat_bridge.qq.config import QQSettings
from chat_bridge.qq.events import QQEventError, map_event
from chat_bridge.qq.gateway import QQGateway
from chat_bridge.qq.onebot import OneBotClient, OneBotError
from chat_bridge.qq.sender import QQSender

__all__ = ['QQSettings', 'QQGateway', 'QQSender', 'OneBotClient', 'OneBotError',
           'QQEventError', 'map_event']