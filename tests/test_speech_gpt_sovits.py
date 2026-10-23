import io
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from chat_bridge.speech.errors import SpeechError
from chat_bridge.speech.gpt_sovits import GPTSoVITSClient, GPTSoVITSSettings


def wav_bytes(seconds=0.5, rate=24000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * int(rate * seconds))
    return output.getvalue()


class TtsServer:
    def __init__(self, payload=None, status=200):
        self.payload = payload if payload is not None else wav_bytes()
        self.status = status
        self.seen = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                import json
                owner.seen = json.loads(self.rfile.read(length))
                self.send_response(owner.status)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(owner.payload)))
                self.end_headers()
                self.wfile.write(owner.payload)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class GPTSoVITSTests(unittest.TestCase):
    def test_posts_bounded_request_and_writes_valid_wav(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / 'ref.wav'
            reference.write_bytes(wav_bytes(1.0))
            with TtsServer() as server:
                client = GPTSoVITSClient(GPTSoVITSSettings(
                    base_url=server.url, ref_audio_path=reference,
                    prompt_text='参考文本', prompt_lang='zh', text_lang='zh'))
                output = client.synthesize('你好', root / 'out.wav')
                self.assertEqual(server.seen['text'], '你好')
                self.assertEqual(server.seen['media_type'], 'wav')
                self.assertTrue(output.is_file())
                with wave.open(str(output), 'rb') as audio:
                    self.assertEqual(audio.getnchannels(), 1)

    def test_non_loopback_service_is_rejected(self):
        with self.assertRaises(ValueError):
            GPTSoVITSClient(GPTSoVITSSettings(base_url='http://10.0.0.2:9880'))

    def test_invalid_audio_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / 'ref.wav'
            reference.write_bytes(wav_bytes(1.0))
            with TtsServer(payload=b'not a wav') as server:
                client = GPTSoVITSClient(GPTSoVITSSettings(
                    base_url=server.url, ref_audio_path=reference))
                with self.assertRaises(SpeechError) as caught:
                    client.synthesize('hi', root / 'out.wav')
                self.assertEqual(caught.exception.code, 'gpt_sovits_invalid_audio')

    def test_missing_reference_is_reported(self):
        with TemporaryDirectory() as temp:
            client = GPTSoVITSClient(GPTSoVITSSettings(
                base_url='http://127.0.0.1:9', ref_audio_path=Path(temp) / 'missing.wav'))
            with self.assertRaises(SpeechError) as caught:
                client.synthesize('hi', Path(temp) / 'out.wav')
            self.assertEqual(caught.exception.code, 'gpt_sovits_reference_missing')


if __name__ == '__main__':
    unittest.main()
