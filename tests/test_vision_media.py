import hashlib
import io
import struct
import unittest
from dataclasses import FrozenInstanceError

from PIL import Image

from chat_bridge.vision.media import VisionError, decode_image, normalize_image


def picture(fmt='PNG', size=(100, 60), **options):
    output = io.BytesIO()
    Image.new('RGB', size, (20, 80, 170)).save(output, fmt, **options)
    return output.getvalue()


def v2_image(raw, seed=12345, account_id='synthetic_account', xor_size=17):
    from Crypto.Cipher import AES
    aes_size = min(67, len(raw) - xor_size)
    head = raw[:aes_size]
    padding = 16 - len(head) % 16
    key = hashlib.md5(f'{seed}{account_id}'.encode()).hexdigest()[:16].encode('ascii')
    cipher = AES.new(key, AES.MODE_ECB).encrypt(head + bytes([padding]) * padding)
    middle = raw[aes_size:len(raw) - xor_size] if xor_size else raw[aes_size:]
    tail = bytes(value ^ (seed & 255) for value in raw[-xor_size:]) if xor_size else b''
    return struct.pack('<6sLLx', b'\x07\x08V2\x08\x07', aes_size, xor_size) + cipher + middle + tail


class VisionMediaTests(unittest.TestCase):
    def assert_error(self, callback, code=None):
        with self.assertRaises(VisionError) as caught:
            callback()
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn('synthetic_account', str(caught.exception))
        self.assertNotIn('12345', str(caught.exception))

    def test_normalizes_jpeg_and_hides_bytes(self):
        raw = picture('JPEG', (1700, 800))
        image = normalize_image(raw)
        self.assertEqual((image.width, image.height), (1280, 602))
        self.assertEqual(image.mime, 'image/jpeg')
        self.assertLessEqual(len(image.data), 768 * 1024)
        self.assertNotIn('data=', repr(image))
        self.assertEqual(image.source_md5, hashlib.md5(raw).hexdigest())
        self.assertNotIn(image.source_md5, repr(image))
        with self.assertRaises(FrozenInstanceError):
            image.width = 1
        with Image.open(io.BytesIO(image.data)) as decoded:
            decoded.load()
            self.assertEqual(decoded.mode, 'RGB')

    def test_exif_orientation_and_metadata_are_stripped(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = 'private synthetic metadata'
        image = normalize_image(picture('JPEG', (120, 80), exif=exif))
        self.assertEqual((image.width, image.height), (80, 120))
        with Image.open(io.BytesIO(image.data)) as decoded:
            self.assertEqual(len(decoded.getexif()), 0)
        self.assertNotIn(b'private synthetic metadata', image.data)

    def test_transparency_is_white(self):
        output = io.BytesIO()
        Image.new('RGBA', (10, 10), (0, 0, 0, 0)).save(output, 'PNG')
        result = normalize_image(output.getvalue())
        with Image.open(io.BytesIO(result.data)) as decoded:
            self.assertEqual(decoded.getpixel((0, 0)), (255, 255, 255))

    def test_supported_images_and_gif_first_frame(self):
        for fmt in ('JPEG', 'PNG', 'WEBP', 'GIF'):
            with self.subTest(fmt=fmt):
                self.assertEqual(normalize_image(picture(fmt)).mime, 'image/jpeg')
        self.assert_error(lambda: normalize_image(picture('BMP')))
        output = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(output, 'GIF', save_all=True,
            append_images=[Image.new('RGB', (10, 10), 'blue')])
        result = normalize_image(output.getvalue())
        self.assertTrue(result.sampled_frame)
        with Image.open(io.BytesIO(result.data)) as decoded:
            red, green, blue = decoded.getpixel((0, 0))
            self.assertGreater(red, 240)
            self.assertLess(blue, 10)
        self.assertFalse(normalize_image(picture('GIF')).sampled_frame)

    def test_rejects_animated_png_and_webp(self):
        for fmt in ('PNG', 'WEBP'):
            output = io.BytesIO()
            Image.new('RGB', (10, 10), 'red').save(output, fmt, save_all=True,
                append_images=[Image.new('RGB', (10, 10), 'blue')])
            with self.subTest(fmt=fmt):
                self.assert_error(lambda: normalize_image(output.getvalue()), 'animated_image')

    def test_rejects_invalid_truncated_and_overlarge_input(self):
        for raw in (b'', b'not an image', picture()[:50], picture('JPEG')[:100]):
            with self.subTest(length=len(raw)):
                self.assert_error(lambda: normalize_image(raw))
        self.assert_error(lambda: normalize_image(b'x' * (8 * 1024 * 1024 + 1)), 'image_too_large')
        self.assert_error(lambda: normalize_image(picture(size=(8193, 1))), 'image_dimensions')
        self.assert_error(lambda: normalize_image(picture(size=(4001, 4000))), 'image_dimensions')

    def test_adaptive_compression_obeys_output_limit(self):
        output = io.BytesIO()
        Image.effect_noise((1500, 1500), 100).convert('RGB').save(output, 'PNG')
        image = normalize_image(output.getvalue())
        self.assertLessEqual(max(image.width, image.height), 1280)
        self.assertLessEqual(len(image.data), 768 * 1024)

    def test_raw_and_legacy_xor_are_fully_validated(self):
        raw = picture()
        expected = normalize_image(raw)
        decoded = decode_image(raw, account_id='synthetic_account')
        self.assertEqual(decoded, expected)
        self.assertEqual(decoded.source_md5, hashlib.md5(raw).hexdigest())
        for key in (1, 99, 255):
            encrypted = bytes(value ^ key for value in raw)
            decoded = decode_image(encrypted, account_id='synthetic_account')
            self.assertEqual(decoded, expected)
            self.assertEqual(decoded.source_md5, hashlib.md5(raw).hexdigest())
        damaged = bytes(value ^ 99 for value in raw[:50])
        self.assert_error(lambda: decode_image(damaged, account_id='synthetic_account'))

    def test_v2_decodes_correct_candidate_and_zero_xor(self):
        raw = picture('JPEG')
        for xor_size in (0, 17):
            with self.subTest(xor_size=xor_size):
                result = decode_image(v2_image(raw, xor_size=xor_size),
                    account_id='synthetic_account', seeds=(777, 12345))
                self.assertEqual(result, normalize_image(raw))
                self.assertEqual(result.source_md5, hashlib.md5(raw).hexdigest())

    def test_v2_rejects_wrong_keys_and_malformed_containers(self):
        valid = v2_image(picture())
        for raw in (valid, valid[:14], valid[:20],
            struct.pack('<6sLLx', b'\x07\x08V2\x08\x07', 0xffffffff, 0) + b'x' * 16,
            struct.pack('<6sLLx', b'\x07\x08V2\x08\x07', 1, 0xffffffff) + b'x' * 16):
            with self.subTest(length=len(raw)):
                self.assert_error(lambda: decode_image(raw, account_id='synthetic_account', seeds=(999,)))
        corrupted = bytearray(valid)
        corrupted[30] ^= 91
        self.assert_error(lambda: decode_image(bytes(corrupted), account_id='synthetic_account', seeds=(12345,)))
        self.assert_error(lambda: decode_image(valid, account_id='wrong', seeds=(12345,)))

    def test_seeds_are_bounded(self):
        raw = v2_image(picture())
        self.assert_error(lambda: decode_image(raw, account_id='synthetic_account', seeds=tuple(range(33))))


if __name__ == '__main__':
    unittest.main()
