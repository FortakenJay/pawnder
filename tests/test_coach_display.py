import io
import struct

from PIL import Image

from coach.display import HEIGHT, WIDTH, Screen, render, to_bmp, white_share


def test_bmp_is_1bit_800x480_with_black_at_index_0():
    bmp = to_bmp(render(Screen(message="Blunder: h3 allows Nc2+, forking king and rook.", weakness="knight forks")))
    (offset,) = struct.unpack_from("<I", bmp, 10)
    width, height = struct.unpack_from("<ii", bmp, 18)
    (bpp,) = struct.unpack_from("<H", bmp, 28)
    assert (width, height, bpp, offset) == (WIDTH, HEIGHT, 1, 62)  # positive height: rows bottom-up
    assert len(bmp) == offset + WIDTH // 8 * HEIGHT
    assert bmp[54:58] == b"\x00\x00\x00\x00" and bmp[58:61] == b"\xff\xff\xff"
    assert Image.open(io.BytesIO(bmp)).size == (WIDTH, HEIGHT)


def test_long_message_still_fits():
    render(Screen(message="word " * 200, opening="X" * 300, weakness="y" * 300))


def test_white_share():
    assert white_share(0, None) == 0.5
    assert white_share(400, None) > 0.75
    assert white_share(0, 3) == 1.0 and white_share(0, -2) == 0.0
