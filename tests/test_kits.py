import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from chat_bridge.media.kits import CoverError, KitsClient


class Response(io.BytesIO):
    def __init__(self, data=b'', status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def getcode(self):
        return self.status


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(value, **kwargs):
    return Response(json.dumps(value).encode(), **kwargs)


def job(kind='infer', **kwargs):
    return {'id': 7, 'type': kind, 'status': 'running', **kwargs}


class KitsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / 'private-name.wav'
        self.audio.write_bytes(b'RIFF-test-wave')

    def client(self, *responses, download=None):
        api = Transport(*responses)
        client = KitsClient('secret-key', frozenset({'cdn.example.com'}), api,
                            download_opener=download)
        return client, api

    def assert_code(self, code, action):
        with self.assertRaises(CoverError) as caught:
            action()
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn('secret', repr(caught.exception))

    def test_error_and_repr_are_safe(self):
        client, _ = self.client()
        self.assertNotIn('secret-key', repr(client))
        self.assertEqual(str(CoverError('kits_invalid_response')), 'kits_invalid_response')

    def test_list_voices_sanitizes_and_paginates(self):
        client, api = self.client(response({'data': [
            {'id': 1, 'title': 'Voice', 'isUsable': True, 'tags': ['pop'], 'demoURL': 'secret'},
            {'id': 2, 'title': 'Legacy', 'tags': []},
        ], 'meta': {'currentPage': 2, 'lastPage': 3}}))
        self.assertEqual(client.list_voices(2), {'data': [
            {'id': 1, 'title': 'Voice', 'isUsable': True, 'tags': ['pop']},
            {'id': 2, 'title': 'Legacy', 'isUsable': False, 'tags': []},
        ], 'page': 2, 'lastPage': 3})
        req, timeout = api.requests[0]
        self.assertEqual(req.full_url, 'https://arpeggi.io/api/kits/v1/voice-models?order=asc&page=2&perPage=50&myModels=false&instruments=false')
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret-key')
        self.assertEqual(timeout, 30)

    def test_rejects_bad_voice_fields_and_page(self):
        base = {'id': 1, 'title': 'V', 'isUsable': True, 'tags': []}
        for field, value in [('id', True), ('title', 4), ('isUsable', 'yes'), ('tags', [3])]:
            with self.subTest(field=field):
                client, _ = self.client(response({'data': [{**base, field: value}], 'meta': {'currentPage': 1, 'lastPage': 1}}))
                self.assert_code('kits_invalid_response', client.list_voices)
        for meta in [{'currentPage': 2, 'lastPage': 3}, {'currentPage': 1, 'lastPage': False}, {'currentPage': 1, 'lastPage': 0}]:
            client, _ = self.client(response({'data': [], 'meta': meta}))
            self.assert_code('kits_invalid_response', client.list_voices)
        client, api = self.client()
        for page in [True, 0, -1, '1']:
            self.assert_code('kits_invalid_input', lambda: client.list_voices(page))
        self.assertEqual(api.requests, [])

    def test_get_voice_validates_identity(self):
        client, _ = self.client(response({'id': 9, 'title': 'V', 'isUsable': True, 'tags': []}))
        self.assertEqual(client.get_voice(9)['id'], 9)
        client, _ = self.client(response({'id': 8, 'title': 'V', 'isUsable': True, 'tags': []}))
        self.assert_code('kits_invalid_response', lambda: client.get_voice(9))

    def test_multipart_has_official_fields_and_safe_filename(self):
        client, api = self.client(response(job('separate')), response(job()))
        self.assertEqual(client.submit_separation(self.audio)['type'], 'separate')
        client.submit_conversion(self.audio, 8, -2)
        separation, conversion = [item[0] for item in api.requests]
        self.assertTrue(separation.full_url.endswith('/vocal-separations'))
        self.assertIn(b'name="inputFile"; filename="sample.wav"', separation.data)
        self.assertIn(b'name="soundFile"; filename="sample.wav"', conversion.data)
        self.assertIn(b'name="voiceModelId"\r\n\r\n8\r\n', conversion.data)
        self.assertIn(b'name="pitchShift"\r\n\r\n-2\r\n', conversion.data)
        self.assertIn(b'Content-Type: audio/wav', conversion.data)
        self.assertNotIn(b'private-name', conversion.data)
        self.assertEqual(conversion.get_method(), 'POST')

    def test_upload_limit_and_bad_ids_do_not_make_requests(self):
        self.audio.write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        client, api = self.client()
        self.assert_code('kits_upload_too_large', lambda: client.submit_separation(self.audio))
        for value in [True, 0, -1, '7']:
            self.assert_code('kits_invalid_input', lambda: client.submit_conversion(self.audio, value))
        self.assert_code('kits_invalid_input', lambda: client.submit_conversion(self.audio, 7, True))
        self.assertEqual(api.requests, [])

    def test_post_network_error_is_unknown_and_never_retried(self):
        for error in [URLError('secret URL'), TimeoutError('secret'), HTTPError('https://secret', 500, 'secret', {}, None)]:
            client, api = self.client(error)
            expected = 'kits_http_500' if isinstance(error, HTTPError) else 'kits_submission_unknown'
            self.assert_code(expected, lambda: client.submit_separation(self.audio))
            self.assertEqual(len(api.requests), 1)

    def test_post_rejects_malformed_identity_type_status(self):
        for value in [job(id=True), job(type='separate'), job(status='unknown'), {'id': 7}, []]:
            client, api = self.client(response(value))
            self.assert_code('kits_submission_unknown', lambda: client.submit_conversion(self.audio, 1))
            self.assertEqual(len(api.requests), 1)

    def test_job_identity_status_and_allowed_fields(self):
        data = job(status='success', outputFileUrl='https://cdn.example.com/a?signature=secret', debug='secret')
        client, api = self.client(response(data))
        actual = client.get_job('conversion', 7)
        self.assertEqual(actual['outputFileUrl'], data['outputFileUrl'])
        self.assertNotIn('debug', actual)
        self.assertTrue(api.requests[0][0].full_url.endswith('/voice-conversions/7'))
        for change in [{'id': 8}, {'type': 'separate'}, {'status': 'done'}, {'outputFileUrl': 8}]:
            client, _ = self.client(response({**data, **change}))
            self.assert_code('kits_invalid_response', lambda: client.get_job('conversion', 7))
        client, _ = self.client(response(job('separate', status='success', vocalAudioFileUrl='https://cdn.example.com/v', stemFileUrls=[{'instrument': 'backing', 'url': 'https://cdn.example.com/b'}])))
        self.assertEqual(client.get_job('separation', 7)['stemFileUrls'][0]['instrument'], 'backing')

    def test_get_bad_input_and_http_error(self):
        client, api = self.client(HTTPError('https://secret', 401, 'secret-key', {}, None))
        self.assert_code('kits_http_401', client.list_voices)
        self.assertEqual(len(api.requests), 1)
        self.assert_code('kits_invalid_input', lambda: client.get_job('unknown', 7))
        self.assert_code('kits_invalid_input', lambda: client.get_job('conversion', True))

    def test_json_response_limit_and_invalid_json(self):
        for raw, code in [(b'x' * (1024 * 1024 + 1), 'kits_response_too_large'), (b'{', 'kits_invalid_response')]:
            client, _ = self.client(Response(raw))
            self.assert_code(code, client.list_voices)
        client, _ = self.client(Response(b'x' * (1024 * 1024 + 1)))
        self.assert_code('kits_submission_unknown', lambda: client.submit_separation(self.audio))

    def test_download_uses_separate_opener_without_bearer(self):
        download = Transport(Response(b'audio'))
        client, api = self.client(download=download)
        destination = Path(self.directory.name) / 'result.wav'
        client.download('https://cdn.example.com/a?signature=secret', destination)
        self.assertEqual(destination.read_bytes(), b'audio')
        self.assertEqual(api.requests, [])
        request, timeout = download.requests[0]
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(timeout, 30)

    def test_download_rejects_unsafe_urls(self):
        transport = Transport()
        client, _ = self.client(download=transport)
        for url in ['http://cdn.example.com/a', 'https://evil.example.com/a', 'https://cdn.example.com.evil/a',
                    'https://user:pass@cdn.example.com/a', 'https://cdn.example.com:444/a',
                    'https://cdn.example.com/a#f', 'https://127.0.0.1/a', 'https://[::1]/a']:
            with self.subTest(url=url):
                self.assert_code('kits_download_url_invalid', lambda: client.download(url, Path(self.directory.name) / 'x'))
        self.assertEqual(transport.requests, [])

    def test_download_local_ip_rejected_even_if_allowlisted(self):
        client = KitsClient('secret-key', frozenset({'127.0.0.1'}), Transport(), download_opener=Transport())
        self.assert_code('kits_download_url_invalid', lambda: client.download('https://127.0.0.1/a', self.audio))

    def test_download_refuses_redirects_and_cleans_partial(self):
        for result in [Response(b'x', 302), URLError('https://secret'), Response(b'x' * (20 * 1024 * 1024 + 1))]:
            destination = Path(self.directory.name) / 'out.wav'
            client, _ = self.client(download=Transport(result))
            code = 'kits_download_too_large' if isinstance(result, Response) and len(result.getvalue()) > 20 * 1024 * 1024 else 'kits_download_failed'
            self.assert_code(code, lambda: client.download('https://cdn.example.com/a', destination))
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(self.directory.name).glob('*.part')), [])

    def test_download_never_overwrites_destination(self):
        client, _ = self.client(download=Transport())
        self.assert_code('kits_destination_exists', lambda: client.download('https://cdn.example.com/a', self.audio))
        self.assertEqual(self.audio.read_bytes(), b'RIFF-test-wave')

    def test_default_openers_disable_proxy_and_redirect(self):
        from urllib.request import ProxyHandler
        client = KitsClient('secret-key', frozenset({'cdn.example.com'}))
        self.assertIsNot(client._api_opener, client._download_opener)
        for opener in [client._api_opener, client._download_opener]:
            self.assertFalse(any(isinstance(handler, ProxyHandler) and handler.proxies for handler in opener.handlers))
            for handler in opener.handlers:
                if hasattr(handler, 'redirect_request'):
                    self.assert_code('kits_redirect_refused', lambda: handler.redirect_request(None, None, 302, '', {}, 'https://secret'))

    def test_optional_voice_model_id_is_preserved_and_validated(self):
        client, _ = self.client(response(job(voiceModelId=8)))
        self.assertEqual(client.submit_conversion(self.audio, 8)['voiceModelId'], 8)
        for value in [7, True, 'wrong', 0]:
            client, _ = self.client(response(job(voiceModelId=value)))
            self.assert_code('kits_submission_unknown', lambda: client.submit_conversion(self.audio, 8))
        client, _ = self.client(response(job(voiceModelId=8)))
        self.assertEqual(client.get_job('conversion', 7)['voiceModelId'], 8)

    def test_download_failure_after_partial_read_removes_temporary(self):
        class BrokenResponse(Response):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('secret signed url')
                return super().read(3)
        client, _ = self.client(download=Transport(BrokenResponse(b'audio-bytes')))
        destination = Path(self.directory.name) / 'broken.wav'
        self.assert_code('kits_download_failed', lambda: client.download('https://cdn.example.com/a', destination))
        self.assertEqual(list(Path(self.directory.name).glob('*.part')), [])
        self.assertFalse(destination.exists())

    def test_limits_accept_exact_boundaries(self):
        self.audio.write_bytes(b'x' * (10 * 1024 * 1024))
        client, api = self.client(response(job('separate')))
        self.assertEqual(client.submit_separation(self.audio)['id'], 7)
        self.assertEqual(len(api.requests), 1)
        destination = Path(self.directory.name) / 'maximum.wav'
        client, _ = self.client(download=Transport(Response(b'x' * (20 * 1024 * 1024))))
        client.download('https://cdn.example.com/a', destination)
        self.assertEqual(destination.stat().st_size, 20 * 1024 * 1024)


    def test_download_rejects_truncated_or_invalid_lengths(self):
        for length in ['10', '-1', 'broken']:
            client, _ = self.client(download=Transport(Response(b'short', headers={'Content-Length': length})))
            destination = Path(self.directory.name) / 'truncated.wav'
            self.assert_code('kits_download_failed', lambda: client.download('https://cdn.example.com/a', destination))
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(self.directory.name).glob('*.part')), [])

    def test_download_rejects_empty_fragment(self):
        client, _ = self.client(download=Transport())
        self.assert_code('kits_download_url_invalid', lambda: client.download('https://cdn.example.com/a#', self.audio))

    def test_null_voice_tags_normalize_to_empty_list(self):
        voice = {'id': 8, 'title': 'V', 'isUsable': True, 'tags': None}
        client, _ = self.client(response(voice), response({'data': [voice], 'meta': {'currentPage': 1, 'lastPage': 1}}))
        self.assertEqual(client.get_voice(8)['tags'], [])
        self.assertEqual(client.list_voices()['data'][0]['tags'], [])

    def test_voice_model_string_and_null_normalization(self):
        for model_id in ['8', None]:
            client, _ = self.client(response(job(voiceModelId=model_id)), response(job(voiceModelId=model_id)))
            submitted = client.submit_conversion(self.audio, 8)
            polled = client.get_job('conversion', 7)
            for result in [submitted, polled]:
                if model_id is None:
                    self.assertNotIn('voiceModelId', result)
                else:
                    self.assertEqual(result['voiceModelId'], 8)
                    self.assertIs(type(result['voiceModelId']), int)
        for model_id in ['7', '', '0', '-8', '8.0', ' 8', 'eight', '８']:
            client, _ = self.client(response(job(voiceModelId=model_id)))
            self.assert_code('kits_submission_unknown', lambda: client.submit_conversion(self.audio, 8))

    def test_pitch_shift_range_rejected_before_post(self):
        for shift in [-25, 25, True, '0', 0.5]:
            client, transport = self.client()
            self.assert_code('kits_invalid_input', lambda: client.submit_conversion(self.audio, 8, shift))
            self.assertEqual(transport.requests, [])
        for shift in [-24, 24]:
            client, transport = self.client(response(job()))
            self.assertEqual(client.submit_conversion(self.audio, 8, shift)['id'], 7)
            self.assertEqual(len(transport.requests), 1)


if __name__ == '__main__':
    unittest.main()
