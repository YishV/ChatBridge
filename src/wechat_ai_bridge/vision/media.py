"""Bounded, memory-only decoding and metadata-free image normalization.

WeChat V2 container support is independently implemented from the documented
15-byte header, account-derived AES key, and XOR tail format. No media, keys,
account identifiers, or candidate seeds are logged or persisted here.
"""

from __future__ import annotations

import hashlib
import io
import struct
import warnings
from dataclasses import dataclass, field

from PIL import Image, ImageOps


MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_SIDE = 8192
MAX_OUTPUT_SIDE = 1280
MAX_OUTPUT_BYTES = 768 * 1024
MAX_SEEDS = 32
V2_MAGIC = b'\x07\x08V2\x08\x07'
_FORMATS = frozenset({'JPEG', 'PNG', 'WEBP', 'GIF'})
_ERROR_CODES = frozenset({
    'invalid_image', 'image_too_large', 'image_dimensions', 'animated_image',
    'unsupported_image', 'invalid_container', 'decode_failed', 'invalid_seeds',
    'decoder_unavailable',
})


class VisionError(ValueError):
    """An error whose public message contains only a fixed, safe code."""

    def __init__(self, code: str = 'invalid_image') -> None:
        self.code = code if code in _ERROR_CODES else 'invalid_image'
        super().__init__(self.code)


@dataclass(frozen=True)
class VisionImage:
    data: bytes = field(repr=False)
    width: int
    height: int
    mime: str = 'image/jpeg'
    sampled_frame: bool = False
    source_md5: str = field(default='', repr=False)


def _check_input(raw: bytes) -> None:
    if not isinstance(raw, bytes) or not raw:
        raise VisionError('invalid_image')
    if len(raw) > MAX_INPUT_BYTES:
        raise VisionError('image_too_large')


def _check_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if (width <= 0 or height <= 0 or max(width, height) > MAX_SIDE
            or width * height > MAX_PIXELS):
        raise VisionError('image_dimensions')


def normalize_image(raw: bytes) -> VisionImage:
    """Validate all image pixels, orient, flatten and encode a bounded JPEG."""
    _check_input(raw)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            # Verification catches container corruption; loading checks actual pixels.
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in _FORMATS:
                    raise VisionError('unsupported_image')
                _check_dimensions(source)
                # GIF's n_frames scans the entire animation. is_animated inspects
                # at most the next frame's header and restores position to zero.
                sampled_frame = bool(getattr(source, 'is_animated', False))
                if sampled_frame and source.format != 'GIF':
                    raise VisionError('animated_image')
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                try:
                    rgba = oriented.convert('RGBA')
                    try:
                        rgb = Image.new('RGB', rgba.size, 'white')
                        rgb.paste(rgba, mask=rgba.getchannel('A'))
                    finally:
                        rgba.close()
                finally:
                    oriented.close()
        try:
            rgb.thumbnail((MAX_OUTPUT_SIDE, MAX_OUTPUT_SIDE), Image.Resampling.LANCZOS)
            # A fresh RGB canvas deliberately excludes EXIF, ICC and other metadata.
            while True:
                for quality in (88, 78, 68, 55, 40):
                    output = io.BytesIO()
                    rgb.save(output, 'JPEG', quality=quality, optimize=True)
                    encoded = output.getvalue()
                    if len(encoded) <= MAX_OUTPUT_BYTES:
                        return VisionImage(encoded, rgb.width, rgb.height,
                                           sampled_frame=sampled_frame,
                                           source_md5=hashlib.md5(raw).hexdigest())
                width, height = rgb.size
                if max(width, height) <= 1:
                    raise VisionError('image_too_large')
                rgb.thumbnail((max(1, int(width * .8)), max(1, int(height * .8))),
                              Image.Resampling.LANCZOS)
        finally:
            rgb.close()
    except VisionError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise VisionError('image_dimensions') from None
    except (OSError, ValueError, SyntaxError, EOFError, OverflowError):
        raise VisionError('invalid_image') from None


def _has_magic(raw: bytes) -> bool:
    return (raw.startswith(b'\xff\xd8\xff')
            or raw.startswith(b'\x89PNG\r\n\x1a\n')
            or raw.startswith((b'GIF87a', b'GIF89a'))
            or (len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'))


def _decode_v2(raw: bytes, account_id: str, seeds: tuple[int, ...]) -> VisionImage:
    if len(raw) < 15:
        raise VisionError('invalid_container')
    _, aes_size, xor_size = struct.unpack('<6sLLx', raw[:15])
    encrypted_size = aes_size + 16 - aes_size % 16
    remainder_size = len(raw) - 15 - encrypted_size
    if aes_size == 0 or remainder_size < 0 or xor_size > remainder_size:
        raise VisionError('invalid_container')
    if (not isinstance(seeds, tuple) or len(seeds) > MAX_SEEDS
            or any(type(seed) is not int or not 0 <= seed <= 0xffffffffffffffff
                   for seed in seeds)):
        raise VisionError('invalid_seeds')
    if not isinstance(account_id, str) or not account_id or len(account_id) > 256:
        raise VisionError('decode_failed')
    try:
        from Crypto.Cipher import AES
    except ImportError:
        raise VisionError('decoder_unavailable') from None
    encrypted = raw[15:15 + encrypted_size]
    remainder = raw[15 + encrypted_size:]
    for seed in dict.fromkeys(seeds):
        key = hashlib.md5(f'{seed}{account_id}'.encode('utf-8')).hexdigest()[:16].encode('ascii')
        decrypted = AES.new(key, AES.MODE_ECB).decrypt(encrypted)
        padding = decrypted[-1]
        if (not 1 <= padding <= 16 or decrypted[-padding:] != bytes([padding]) * padding
                or len(decrypted) - padding != aes_size):
            continue
        # A zero-length XOR tail must not use a -0 slice (which selects everything).
        if xor_size:
            candidate = (decrypted[:-padding] + remainder[:-xor_size]
                         + bytes(value ^ (seed & 255) for value in remainder[-xor_size:]))
        else:
            candidate = decrypted[:-padding] + remainder
        if not _has_magic(candidate):
            continue
        try:
            return normalize_image(candidate)
        except VisionError:
            continue
    raise VisionError('decode_failed')


def decode_image(raw: bytes, *, account_id: str, seeds: tuple[int, ...] = ()) -> VisionImage:
    """Decode raw, legacy XOR, or V2 image bytes using only supplied candidates."""
    _check_input(raw)
    if raw.startswith(V2_MAGIC):
        return _decode_v2(raw, account_id, seeds)
    if _has_magic(raw):
        return normalize_image(raw)
    # Each supported first magic byte implies exactly one XOR candidate.
    for first in (0xff, 0x89, ord('G'), ord('R')):
        key = raw[0] ^ first
        prefix = bytes(value ^ key for value in raw[:12])
        if not _has_magic(prefix):
            continue
        candidate = bytes(value ^ key for value in raw)
        try:
            return normalize_image(candidate)
        except VisionError:
            continue
    raise VisionError('decode_failed')
