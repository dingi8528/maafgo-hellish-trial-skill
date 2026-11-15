from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/maafgo-hellish-trial/scripts'))
from prepare_bb_loop import prepare, resolve_ffmpeg


class LoopAssetsTests(unittest.TestCase):
    def test_input_cannot_be_overwritten(self):
        source = ROOT / 'skills/maafgo-hellish-trial/assets/live/bb-loop.webm'
        with self.assertRaisesRegex(ValueError, '同一文件'):
            prepare(source, source.parent, ready=True)

    def test_already_looped_media_cannot_be_reversed_again_in_ready_mode(self):
        source = ROOT / 'skills/maafgo-hellish-trial/assets/live/bb-loop.webm'
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, '不能同时正倒放'):
            prepare(source, directory, ready=True, ping_pong=True)

    def test_bundled_loop_rebuild_keeps_original_and_transparent_poster(self):
        try:
            executable = resolve_ffmpeg()
            from PIL import Image
        except (ValueError, ImportError):
            self.skipTest('FFmpeg and Pillow required for media validation')
        source = ROOT / 'skills/maafgo-hellish-trial/assets/live/bb-loop.webm'
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            result = prepare(source, directory, ffmpeg=executable, ready=True)
            self.assertEqual((Path(directory) / result['movie']).read_bytes(), original)
            with Image.open(Path(directory) / result['poster']) as image:
                self.assertEqual(image.size, (640, 360))
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getextrema()[-1], (0, 255))
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
