import json
import sys
import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lesson_notes_automation as app
from video_import import VideoImporter
from video_region import parse_video_time, validate_time_range, select_video_region


def create_av_video(path, compressed=False):
    import av
    import numpy as np
    with av.open(str(path), 'w') as container:
        video = container.add_stream('libx264' if compressed else 'ffv1', rate=4)
        video.width, video.height, video.pix_fmt = 640, 360, 'yuv420p' if compressed else 'bgr0'
        sample_rate = 48000 if compressed else 16000
        audio = container.add_stream('aac' if compressed else 'pcm_s16le', rate=sample_rate)
        audio.layout = 'mono'
        for index in range(24):
            pixels = np.zeros((360, 640, 3), dtype=np.uint8)
            pixels[:] = (255, 0, 0) if index < 12 else (0, 0, 255)
            for packet in video.encode(av.VideoFrame.from_ndarray(pixels, format='rgb24')):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        for index in range(6):
            # Distinct positive PCM levels prove which source seconds reach the model.
            level = 1000 * (index + 1) / (32768 if compressed else 1)
            data = np.full((1, sample_rate), level, dtype=np.float32 if compressed else np.int16)
            frame = av.AudioFrame.from_ndarray(data, format='fltp' if compressed else 's16', layout='mono')
            frame.sample_rate = sample_rate
            frame.pts = index * sample_rate
            frame.time_base = Fraction(1, sample_rate)
            for packet in audio.encode(frame):
                container.mux(packet)
        for packet in audio.encode():
            container.mux(packet)


class TimeRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'audio-video.mkv'
        create_av_video(self.source)
        self.model_inputs = []

    def importer(self, start=0, end=None, mode='转写＋截图'):
        session = self.folder / f'session-{len(list(self.folder.iterdir()))}'
        session.mkdir()
        inputs = self.model_inputs
        class Model:
            def transcribe(self, samples, **kwargs):
                inputs.append(samples.copy())
                return iter([SimpleNamespace(start=0, end=1000, text='测试片段')]), SimpleNamespace(language='zh')
        return VideoImporter(self.source,
                             app.Settings(material_mode=mode, capture_mode='定时截图',
                                          capture_interval_seconds=1, skip_duplicate_screenshots=False),
                             session, lambda _: None, threading.Event(), threading.Event(),
                             model_factory=lambda *args, **kwargs: Model(),
                             capture_region=[20, 30, 200, 100], frame_size=[640, 360],
                             start_seconds=start, end_seconds=end)

    def test_formats_and_invalid_ranges(self):
        for text, seconds in [('90', 90), ('01:30', 90), ('00:01:30.5', 90.5), ('1:00:00', 3600)]:
            self.assertEqual(parse_video_time(text), seconds)
        for text in ['-1', 'nan', 'inf', '1:60', '00:60:00', '', '1:2:3:4']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_video_time(text)
        for start, end in [(3, 2), (2, 2), (-1, 2), (6, None), (0, 7), (float('nan'), 4)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_time_range(start, end, 6)
        self.assertEqual(validate_time_range(1.5, None, 6), (1.5, None))

    def test_screenshots_and_audio_use_same_half_open_range(self):
        import numpy as np
        from PIL import Image
        importer = self.importer(1.25, 4.25)
        metadata = importer.run()
        self.assertEqual([frame['seconds'] for frame in importer.frames], [1.25, 2.25, 3.25])
        self.assertEqual(metadata['selected_duration_seconds'], 3)
        self.assertEqual(metadata['start_seconds'], 1.25)
        self.assertEqual(metadata['end_seconds'], 4.25)
        self.assertTrue(metadata['transcript_complete'])
        samples = np.concatenate(self.model_inputs)
        self.assertEqual(len(samples), 48000)
        # Start at second 1.25, stop at 4.25; seconds 0 and 5 never reach inference.
        self.assertAlmostEqual(float(samples[0]), 2000 / 32768)
        self.assertAlmostEqual(float(samples[-1]), 5000 / 32768)
        self.assertEqual(int(np.sum(samples == 2000 / 32768)), 12000)
        self.assertEqual(int(np.sum(samples == 5000 / 32768)), 4000)
        text = (importer.session_dir / 'transcript.txt').read_text(encoding='utf-8')
        self.assertIn('[00:00:01 - 00:00:04]', text)
        for frame in importer.frames:
            with Image.open(importer.session_dir / 'screenshots' / frame['file']) as image:
                self.assertEqual(image.size, (200, 100))

    def test_transcript_only_and_open_ended_range(self):
        import numpy as np
        importer = self.importer(4, None, mode='仅转写')
        importer.run()
        samples = np.concatenate(self.model_inputs)
        self.assertEqual(len(samples), 32000)
        self.assertEqual(importer.frames, [])
        self.assertAlmostEqual(float(samples[0]), 5000 / 32768)
        self.assertAlmostEqual(float(samples[-1]), 6000 / 32768)

    def test_mp4_keyframe_seek_and_aac_resampling_preserve_range(self):
        import numpy as np
        self.source = self.folder / 'compressed.mp4'
        create_av_video(self.source, compressed=True)
        importer = self.importer(1.25, 4.25)
        importer.run()
        self.assertEqual([frame['seconds'] for frame in importer.frames], [1.25, 2.25, 3.25])
        samples = np.concatenate(self.model_inputs)
        self.assertEqual(len(samples), 48000)
        self.assertAlmostEqual(float(samples[:1000].mean()), 2000 / 32768, delta=0.003)
        self.assertAlmostEqual(float(samples[-1000:].mean()), 5000 / 32768, delta=0.003)

    def test_end_excludes_frame_exactly_on_boundary(self):
        importer = self.importer(2, 3, mode='仅截图')
        importer.run()
        self.assertEqual([frame['seconds'] for frame in importer.frames], [2.0])

    def test_invalid_range_fails_before_extracting_materials(self):
        for start, end in [(4, 2), (0, 10), (6, None)]:
            importer = self.importer(start, end)
            with self.assertRaises(ValueError):
                importer.run()
            self.assertFalse(list(importer.session_dir.glob('screenshots/*.png')))
            self.assertFalse((importer.session_dir / 'transcript.txt').exists())

    def test_controller_forwards_and_persists_range(self):
        done = threading.Event()
        issues = []
        def ready(issue, _):
            issues.append(issue)
            done.set()
        controller = app.SessionController(app.Settings(folder=str(self.folder), material_mode='仅截图',
                                                        capture_mode='定时截图', capture_interval_seconds=1,
                                                        skip_duplicate_screenshots=False),
                                           '', lambda _: None, ready, lambda *_: None)
        controller.import_video(self.source, {'start_seconds': 2, 'end_seconds': 4})
        self.assertTrue(done.wait(15))
        self.assertEqual(issues, [None])
        metadata = json.loads(controller.manifest_path.read_text(encoding='utf-8'))['video_source']
        self.assertEqual((metadata['start_seconds'], metadata['end_seconds']), (2, 4))
        frames = json.loads((controller.session_dir / 'video_frames.json').read_text(encoding='utf-8'))
        self.assertEqual([frame['seconds'] for frame in frames], [2.0, 3.0])

    def test_dialog_collects_range_in_transcript_only_mode(self):
        from tkinter import Toplevel, ttk
        root = app.Tk()
        root.geometry('400x100')
        errors = []
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def interact():
            dialog = next(child for child in root.winfo_children() if isinstance(child, Toplevel))
            try:
                widgets = list(descendants(dialog))
                for name, value in [('range_start', '00:00:01.25'), ('range_end', '00:00:04.25')]:
                    entry = next(widget for widget in widgets if widget.winfo_name() == name)
                    entry.delete(0, 'end')
                    entry.insert(0, value)
                next(widget for widget in widgets if isinstance(widget, ttk.Button)
                     and widget.cget('text') == '导入此时间段').invoke()
            except Exception as exc:
                errors.append(exc)
                dialog.destroy()
        try:
            root.update()
            root.after(250, interact)
            result = select_video_region(root, self.source, screenshots=False)
            if errors:
                raise errors[0]
            self.assertEqual((result['start_seconds'], result['end_seconds']), (1.25, 4.25))
            self.assertIsNone(result['capture_region'])
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
