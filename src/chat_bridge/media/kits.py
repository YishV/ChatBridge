"""Bounded Kits HTTP adapter. No request is retried automatically."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import CoverError

_BASE = 'https://arpeggi.io/api/kits/v1'
_JSON_LIMIT = 1024 * 1024
_UPLOAD_LIMIT = 10 * 1024 * 1024
_DOWNLOAD_LIMIT = 20 * 1024 * 1024
_KINDS = {
    'separation': ('vocal-separations', 'separate'),
    'separate': ('vocal-separations', 'separate'),
    'conversion': ('voice-conversions', 'infer'),
    'infer': ('voice-conversions', 'infer'),
}
_URL_FIELDS = ('outputFileUrl', 'lossyOutputFileUrl', 'recombinedAudioFileUrl',
               'vocalAudioFileUrl', 'backingAudioFileUrl')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CoverError('kits_redirect_refused')


def _opener():
    return build_opener(ProxyHandler({}), _NoRedirect())


def _positive_int(value):
    return type(value) is int and value > 0


def _voice(value):
    if (not isinstance(value, dict) or not _positive_int(value.get('id'))
            or not isinstance(value.get('title'), str)
            or type(value.get('isUsable', False)) is not bool
            or 'tags' not in value
            or (value['tags'] is not None and (not isinstance(value['tags'], list)
                or not all(isinstance(tag, str) for tag in value['tags'])))):
        raise CoverError('kits_invalid_response')
    return {key: value[key] for key in ('id', 'title')} | {
        'tags': value['tags'] if value['tags'] is not None else [],
        'isUsable': value.get('isUsable', False)}


def _job(value, kind, job_id=None):
    if (not isinstance(value, dict) or not _positive_int(value.get('id'))
            or (job_id is not None and value['id'] != job_id)
            or value.get('type') != kind
            or value.get('status') not in ('running', 'success', 'error', 'cancelled')):
        raise CoverError('kits_invalid_response')
    clean = {key: value[key] for key in ('id', 'status', 'type')}
    if value.get('voiceModelId') is not None:
        model_id = value['voiceModelId']
        if isinstance(model_id, str) and model_id.isascii() and model_id.isdecimal():
            try:
                model_id = int(model_id)
            except ValueError:
                raise CoverError('kits_invalid_response') from None
        if not _positive_int(model_id):
            raise CoverError('kits_invalid_response')
        clean['voiceModelId'] = model_id
    for key in _URL_FIELDS:
        if key in value and value[key] is not None:
            if not isinstance(value[key], str) or not value[key]:
                raise CoverError('kits_invalid_response')
            clean[key] = value[key]
    if 'stemFileUrls' in value and value['stemFileUrls'] is not None:
        stems = value['stemFileUrls']
        if not isinstance(stems, list):
            raise CoverError('kits_invalid_response')
        clean['stemFileUrls'] = []
        for stem in stems:
            if (not isinstance(stem, dict) or not isinstance(stem.get('instrument'), str)
                    or not isinstance(stem.get('url'), str) or not stem['url']):
                raise CoverError('kits_invalid_response')
            clean['stemFileUrls'].append({'instrument': stem['instrument'], 'url': stem['url']})
    return clean


class KitsClient:
    """Use separate injectable API and download transports (each exposes open)."""

    def __init__(self, api_key: str, download_hosts: frozenset[str], opener=None,
                 *, download_opener=None):
        if not isinstance(api_key, str) or not api_key.strip() or '\r' in api_key or '\n' in api_key:
            raise CoverError('kits_invalid_input')
        if not isinstance(download_hosts, frozenset) or not all(
                isinstance(host, str) and host and '*' not in host for host in download_hosts):
            raise CoverError('kits_invalid_input')
        self._api_key = api_key
        self._download_hosts = frozenset(host.lower() for host in download_hosts)
        self._api_opener = opener if opener is not None else _opener()
        self._download_opener = download_opener if download_opener is not None else _opener()

    def __repr__(self):
        return 'KitsClient()'

    def _json(self, path, data=None, content_type=None):
        request = Request(_BASE + path, data=data, method='POST' if data is not None else 'GET')
        request.add_header('Authorization', 'Bearer ' + self._api_key)
        request.add_header('Accept', 'application/json')
        if content_type:
            request.add_header('Content-Type', content_type)
        try:
            with self._api_opener.open(request, timeout=30) as result:
                status = result.getcode()
                if type(status) is not int or not 200 <= status < 300:
                    if type(status) is int and 400 <= status <= 599:
                        raise CoverError(f'kits_http_{status}')
                    raise CoverError('kits_invalid_response')
                raw = result.read(_JSON_LIMIT + 1)
                if len(raw) > _JSON_LIMIT:
                    raise CoverError('kits_response_too_large')
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise CoverError('kits_invalid_response') from None
        except HTTPError as error:
            code = error.code
            if type(code) is int and 400 <= code <= 599:
                raise CoverError(f'kits_http_{code}') from None
            raise CoverError('kits_submission_unknown' if data is not None else 'kits_invalid_response') from None
        except CoverError as error:
            if data is not None and not error.code.startswith('kits_http_'):
                raise CoverError('kits_submission_unknown') from None
            raise
        except Exception:
            raise CoverError('kits_submission_unknown' if data is not None else 'kits_network_error') from None

    def list_voices(self, page=1) -> dict:
        if not _positive_int(page):
            raise CoverError('kits_invalid_input')
        value = self._json(f'/voice-models?order=asc&page={page}&perPage=50&myModels=false&instruments=false')
        if not isinstance(value, dict) or not isinstance(value.get('data'), list):
            raise CoverError('kits_invalid_response')
        meta = value.get('meta')
        if (not isinstance(meta, dict) or not _positive_int(meta.get('currentPage'))
                or meta['currentPage'] != page or not _positive_int(meta.get('lastPage'))
                or meta['lastPage'] < page):
            raise CoverError('kits_invalid_response')
        return {'data': [_voice(item) for item in value['data']], 'page': page, 'lastPage': meta['lastPage']}

    def get_voice(self, voice_id: int) -> dict:
        if not _positive_int(voice_id):
            raise CoverError('kits_invalid_input')
        voice = _voice(self._json(f'/voice-models/{voice_id}'))
        if voice['id'] != voice_id:
            raise CoverError('kits_invalid_response')
        return voice

    def _submit(self, audio, kind, file_field, fields):
        try:
            with Path(audio).open('rb') as source:
                content = source.read(_UPLOAD_LIMIT + 1)
        except (OSError, TypeError, ValueError):
            raise CoverError('kits_audio_unreadable') from None
        if len(content) > _UPLOAD_LIMIT:
            raise CoverError('kits_upload_too_large')
        if not content:
            raise CoverError('kits_invalid_input')
        boundary = 'kits-' + uuid.uuid4().hex
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="sample.wav"\r\n'
                      'Content-Type: audio/wav\r\n\r\n').encode() + content + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        endpoint, expected = _KINDS[kind]
        value = self._json('/' + endpoint, b''.join(parts), 'multipart/form-data; boundary=' + boundary)
        try:
            result = _job(value, expected)
            if kind == 'conversion' and 'voiceModelId' in result and result['voiceModelId'] != fields['voiceModelId']:
                raise CoverError('kits_invalid_response')
            return result
        except CoverError:
            raise CoverError('kits_submission_unknown') from None

    def submit_separation(self, audio: Path) -> dict:
        return self._submit(audio, 'separation', 'inputFile', {})

    def submit_conversion(self, audio: Path, voice_id: int, pitch_shift: int = 0) -> dict:
        if (not _positive_int(voice_id) or type(pitch_shift) is not int
                or not -24 <= pitch_shift <= 24):
            raise CoverError('kits_invalid_input')
        return self._submit(audio, 'conversion', 'soundFile', {'voiceModelId': voice_id, 'pitchShift': pitch_shift})

    def get_job(self, kind: str, job_id: int) -> dict:
        if not isinstance(kind, str) or kind not in _KINDS or not _positive_int(job_id):
            raise CoverError('kits_invalid_input')
        endpoint, expected = _KINDS[kind]
        return _job(self._json(f'/{endpoint}/{job_id}'), expected, job_id)

    def _download_url(self, url):
        try:
            if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
                raise ValueError
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != 'https' or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)
                    or '#' in url or host not in self._download_hosts
                    or host == 'localhost' or host.endswith('.localhost') or '\\' in parsed.netloc):
                raise ValueError
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise ValueError
        except (ValueError, TypeError):
            raise CoverError('kits_download_url_invalid') from None

    def download(self, url: str, destination: Path) -> None:
        self._download_url(url)
        try:
            destination = Path(destination)
        except (TypeError, ValueError):
            raise CoverError('kits_invalid_input') from None
        if destination.exists() or destination.is_symlink():
            raise CoverError('kits_destination_exists')
        temporary = None
        try:
            request = Request(url, method='GET')
            with self._download_opener.open(request, timeout=30) as result:
                if result.getcode() != 200:
                    raise CoverError('kits_download_failed')
                length = result.headers.get('Content-Length')
                if length is not None:
                    length = int(length)
                    if length < 0:
                        raise CoverError('kits_download_failed')
                    if length > _DOWNLOAD_LIMIT:
                        raise CoverError('kits_download_too_large')
                with tempfile.NamedTemporaryFile(mode='wb', dir=destination.parent, suffix='.part', delete=False) as target:
                    temporary = Path(target.name)
                    total = 0
                    while True:
                        block = result.read(min(64 * 1024, _DOWNLOAD_LIMIT + 1 - total))
                        if not block:
                            break
                        total += len(block)
                        if total > _DOWNLOAD_LIMIT:
                            raise CoverError('kits_download_too_large')
                        target.write(block)
                    if total == 0 or (length is not None and total != length):
                        raise CoverError('kits_download_failed')
            # Atomic hard-link creation fails if destination already exists.
            os.link(temporary, destination)
        except FileExistsError:
            raise CoverError('kits_destination_exists') from None
        except CoverError as error:
            if error.code == 'kits_redirect_refused':
                raise CoverError('kits_download_failed') from None
            raise
        except Exception:
            raise CoverError('kits_download_failed') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
