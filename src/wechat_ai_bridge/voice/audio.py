"""Play synthesized speech into VB-Cable and route WeChat's microphone."""

from contextlib import contextmanager
from pathlib import Path

from wechat_ai_bridge.voice.errors import VoiceError


class CableAudio:
    OUTPUT_NAME = '虚拟扬声器 (VB-Audio Cable A)'
    CAPTURE_NAME = '虚拟麦克风 (VB-Audio Cable A)'
    RATE = 44100

    def play(self, path: Path) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
        except ImportError:
            raise VoiceError('voice_audio_missing') from None
        path = Path(path)
        if not path.is_file():
            raise VoiceError('voice_audio_missing')
        try:
            data, rate = sf.read(path, dtype='float32')
        except Exception as exc:
            raise VoiceError('voice_audio_invalid') from exc
        if data.size == 0 or rate <= 0:
            raise VoiceError('voice_audio_invalid')
        if data.ndim > 1:
            data = data.mean(axis=1)
        if rate != self.RATE:
            count = max(1, int(len(data) * self.RATE / rate))
            data = np.interp(np.linspace(0, len(data) - 1, count), np.arange(len(data)), data).astype('float32')
        device = self._output_device()
        try:
            sd.play(data, self.RATE, device=device, blocking=True)
        except Exception as exc:
            raise VoiceError('voice_audio_play_failed') from exc

    def _output_device(self) -> int:
        try:
            import sounddevice as sd
        except ImportError:
            raise VoiceError('voice_audio_missing') from None
        for index, device in enumerate(sd.query_devices()):
            if device['name'] == self.OUTPUT_NAME and device['max_output_channels'] > 0:
                return index
        raise VoiceError('voice_cable_missing')

    def _capture_id(self) -> str:
        try:
            from pycaw.utils import AudioUtilities
        except ImportError:
            raise VoiceError('voice_audio_missing') from None
        for device in AudioUtilities.GetAllDevices():
            if device.FriendlyName == self.CAPTURE_NAME and device.state.name == 'Active':
                return device.id
        raise VoiceError('voice_cable_missing')

    @contextmanager
    def microphone(self):
        try:
            from pycaw.constants import ERole
            from pycaw.utils import AudioUtilities
        except ImportError:
            raise VoiceError('voice_audio_missing') from None
        original = AudioUtilities.GetMicrophone().GetId()
        cable = self._capture_id()
        roles = [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
        try:
            AudioUtilities.SetDefaultDevice(cable, roles=roles)
        except Exception as exc:
            raise VoiceError('voice_microphone_route_failed') from exc
        try:
            yield
        finally:
            try:
                AudioUtilities.SetDefaultDevice(original, roles=roles)
            except Exception:
                # Restoring is best-effort; never hide a completed voice send.
                pass
