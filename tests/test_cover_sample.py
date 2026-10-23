import json
import io
import shutil
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from chat_bridge.media.cover import CoverConfig, CoverSample
from chat_bridge.media.kits import CoverError


def wav(path, seconds=3):
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(array('h', [4000, -4000] * (44100 * seconds)).tobytes())


class LocalAudio:
    def decode(self, source, target, **kwargs):
        shutil.copyfile(source, target)
        return {'duration': kwargs.get('duration', 3), 'peak': 0.2, 'rms': 0.1}


class FakeKits:
    def __init__(self):
        self.posts = []
        self.gets = []
        self.running = False
        self.unknown = False
        self.usable = True
        self.before_post = lambda kind: None

    def get_voice(self, voice_id):
        return {'id': voice_id, 'title': 'Fixture Female', 'isUsable': self.usable, 'tags': ['Female']}

    def submit_separation(self, audio):
        self.before_post('separation')
        self.posts.append('separation')
        if self.unknown:
            raise CoverError('kits_submission_unknown')
        return {'id': 1, 'type': 'separate', 'status': 'running'}

    def submit_conversion(self, audio, voice_id, pitch_shift=0):
        self.before_post('conversion')
        self.posts.append('conversion')
        return {'id': 2, 'type': 'infer', 'status': 'running'}

    def get_job(self, kind, job_id):
        self.gets.append((kind, job_id))
        if self.running:
            return {'id': job_id, 'type': 'separate' if kind == 'separation' else 'infer', 'status': 'running'}
        if kind == 'separation':
            return {'id': job_id, 'type': 'separate', 'status': 'success',
                    'vocalAudioFileUrl': 'https://cdn.example/v?private=signed',
                    'stemFileUrls': [{'instrument': 'backing', 'url': 'https://cdn.example/b?private=signed'}]}
        return {'id': job_id, 'type': 'infer', 'status': 'success',
                'outputFileUrl': 'https://cdn.example/c?private=signed'}

    def download(self, url, destination):
        wav(destination)


class CoverSampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'private-song.wav'
        wav(self.source)
        self.config = CoverConfig(api_key='fixture-secret', work_dir=self.root / 'runs', female_voice_ids=frozenset({123}))
        self.provider = FakeKits()
        self.sample = CoverSample(self.config, tools=LocalAudio(), client=self.provider, sleep=lambda _: None)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        return self.sample.prepare(self.source, song='Fixture Song', artist='Fixture Artist',
                                   start=0, duration=3, voice_id=123, session='trial')

    def manifest(self):
        return json.loads((self.config.work_dir / 'trial' / 'manifest.json').read_text('utf-8'))

    def test_prepare_is_offline_and_requires_explicit_valid_segment(self):
        result = self.prepare()
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(self.provider.posts, [])
        self.assertEqual(self.provider.gets, [])
        self.assertTrue((self.config.work_dir / 'trial' / 'clip.wav').exists())
        with self.assertRaisesRegex(CoverError, 'cover_session_exists'):
            self.prepare()
        for name in ('../escape', '.', 'a/b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(CoverError):
                self.sample.status(name)

    def test_success_has_write_ahead_ids_and_no_signed_urls_or_keys_on_disk(self):
        self.prepare()
        self.provider.before_post = lambda kind: self.assertEqual(self.manifest()[kind]['state'], 'submitting')
        result = self.sample.run('trial', max_polls=2)
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(Path(result['output']).is_file())
        self.assertEqual(self.provider.posts, ['separation', 'conversion'])
        manifest = self.manifest()
        self.assertEqual(manifest['conversion']['job_id'], 2)
        saved = json.dumps(manifest)
        self.assertNotIn('private=signed', saved)
        self.assertNotIn('fixture-secret', saved)
        self.assertNotIn(str(self.source), saved)
        self.sample.run('trial')
        self.assertEqual(self.provider.posts, ['separation', 'conversion'])

    def test_timeout_resume_queries_known_job_without_resubmission(self):
        self.prepare()
        self.provider.running = True
        with self.assertRaisesRegex(CoverError, 'cover_poll_timeout'):
            self.sample.run('trial', max_polls=2)
        self.assertEqual(len(self.provider.gets), 2)
        self.assertEqual(self.manifest()['separation']['job_id'], 1)
        self.provider.running = False
        self.sample.run('trial', max_polls=2)
        self.assertEqual(self.provider.posts.count('separation'), 1)

    def test_unknown_submission_never_reposts_on_resume(self):
        self.prepare()
        self.provider.unknown = True
        for _ in range(2):
            with self.assertRaises(CoverError):
                self.sample.run('trial', max_polls=1)
        self.assertEqual(self.provider.posts, ['separation'])
        self.assertEqual(self.manifest()['separation']['state'], 'submitting')

    def test_tampered_clip_and_unusable_voice_stop_before_post(self):
        self.prepare()
        self.provider.usable = False
        with self.assertRaisesRegex(CoverError, 'cover_voice_unusable'):
            self.sample.run('trial')
        self.provider.usable = True
        (self.config.work_dir / 'trial' / 'clip.wav').write_bytes(b'changed')
        with self.assertRaisesRegex(CoverError, 'cover_clip_changed'):
            self.sample.run('trial')
        self.assertEqual(self.provider.posts, [])

    def test_partial_or_wrong_duration_cloud_output_is_not_completed(self):
        self.prepare()
        self.provider.download = lambda url, path: wav(path, seconds=2)
        with self.assertRaisesRegex(CoverError, 'audio_duration_mismatch'):
            self.sample.run('trial')
        self.assertNotEqual(self.manifest()['state'], 'complete')

    def test_job_failure_never_resubmits(self):
        self.prepare()
        self.provider.get_job = lambda kind, job_id: {'id': job_id, 'status': 'error'}
        for _ in range(2):
            with self.assertRaisesRegex(CoverError, 'cover_job_failed'):
                self.sample.run('trial', max_polls=1)
        self.assertEqual(self.provider.posts, ['separation'])

    def test_config_repr_hides_key(self):
        self.assertNotIn('fixture-secret', repr(self.config))

    def test_male_or_unregistered_voice_never_creates_cloud_tasks(self):
        self.prepare()
        self.provider.get_voice = lambda voice_id: {'id': voice_id, 'title': 'Fixture Male', 'isUsable': True, 'tags': ['Male']}
        with self.assertRaisesRegex(CoverError, 'cover_voice_not_female'):
            self.sample.run('trial')
        self.sample.config = CoverConfig(api_key='fixture', work_dir=self.config.work_dir, female_voice_ids=frozenset())
        with self.assertRaisesRegex(CoverError, 'cover_voice_not_approved'):
            self.sample.run('trial')
        self.assertEqual(self.provider.posts, [])

    def test_preseparated_inputs_skip_cloud_separation(self):
        result = self.sample.prepare(None, vocal=self.source, backing=self.source,
            song='Fixture Song', artist='Fixture Artist', start=0, duration=3,
            voice_id=123, session='trial')
        self.assertEqual(result['separation']['state'], 'skipped')
        self.sample.run('trial')
        self.assertEqual(self.provider.posts, ['conversion'])

    def test_crash_recording_downloaded_artifact_can_resume_without_resubmitting(self):
        self.prepare()
        save = self.sample._save
        def fail_before_write(directory, manifest):
            if 'vocal' in manifest['artifacts']:
                raise OSError('fixture disk interruption')
            save(directory, manifest)
        with patch.object(self.sample, '_save', side_effect=fail_before_write), self.assertRaises(OSError):
            self.sample.run('trial')
        result = self.sample.run('trial')
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.provider.posts.count('separation'), 1)

    def test_interrupted_mix_publication_can_resume_without_cloud_generation(self):
        self.prepare()
        with patch('chat_bridge.media.audio._publish', side_effect=OSError('fixture publication interruption')):
            with self.assertRaises(OSError):
                self.sample.run('trial')
        self.assertEqual(self.provider.posts, ['separation', 'conversion'])
        self.assertFalse((self.config.work_dir / 'trial' / 'cover.wav').exists())
        result = self.sample.run('trial')
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.provider.posts, ['separation', 'conversion'])

    def test_cover_check_does_not_create_chat_store_or_call_chat_model(self):
        import chat_bridge.cli as cli
        with patch('sys.argv', ['chatbridge', 'cover', 'check']), \
             patch.object(cli, 'StateStore') as store, patch.object(cli, 'create_model') as model, \
             patch('chat_bridge.media.cover.CoverConfig.from_env', return_value=self.config), \
             patch('sys.stdout', new_callable=io.StringIO):
            cli.main()
        store.assert_not_called()
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
