"""Local speech synthesis providers.

GPT-SoVITS is intentionally an external service: its PyTorch stack targets a
different Python version and must not be installed into the bridge runtime.
"""

from chat_bridge.speech.gpt_sovits import GPTSoVITSSettings, GPTSoVITSClient
from chat_bridge.speech.errors import SpeechError

__all__ = ['SpeechError', 'GPTSoVITSSettings', 'GPTSoVITSClient']
