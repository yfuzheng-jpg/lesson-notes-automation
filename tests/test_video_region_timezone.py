import json
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_schedule as schedule
import lesson_notes_automation as app
from video_import import VideoImporter, VideoImportCancelled
from video_region import pixel_region, read_preview, validate_region, select_video_region


def create_video(path, change_inside=False):
    import av
    import numpy as np
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1', rate=4)
        stream.width, stream.height, stream.pix_fmt = 640, 360, 'bgr0'
        for index in range(24):
            image = np.zeros((360, 640, 3), dtype=np.uint8)
            image[:, :320] = (0, 0, 255) if change_inside and index >= 12 else (255, 0, 0)
            image[:, 320:] = (255, 255, 255) if index // 4 % 2 else (0, 255, 0)
            frame = av.VideoFrame.from_ndarray(image, format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class TimezoneTests(unittest.TestCase):
    def test_offsets_and_date_conversion(self):
        instant = datetime(2026, 12, 31, 22, tzinfo=timezone.utc)
        for value, expected in [('GMT+8', '2027-01-01 06:00'),
                                ('GMT-5', '2026-12-31 17:00'),
                                ('GMT+05:45', '2027-01-01 03:45')]:
            converted = schedule.schedule_time(value, instant)
            self.assertEqual(converted.strftime('%Y-%m-%d %H:%M'), expected)
            self.assertEqual(converted.astimezone(timezone.utc), instant)
        self.assertEqual(schedule.schedule_time('本机时间', instant), instant.astimezone())

    def test_normalize_and_validation(self):
        self.assertEqual(schedule.normalize_timezone(' utc-3:30 '), 'GMT-03:30')
        self.assertEqual(schedule.normalize_timezone('GMT-0'), 'GMT+00:00')
        self.assertEqual(schedule.normalize_timezone('GMT+23:59'), 'GMT+23:59')
        for invalid in ['GMT+24', 'GMT-24:00', 'GMT+08:60', 'GMT+8.5', '', 'Asia/Shanghai']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                schedule.normalize_timezone(invalid)

    def test_fixed_zone_cross_midnight_and_http_retry_after(self):
        now = schedule.schedule_time('GMT-05:00', datetime(2026, 11, 7, 8, tzinfo=timezone.utc))
        self.assertEqual(schedule.next_allowed(now, '23:00', '03:00').isoformat(), '2026-11-07T23:00:00-05:00')
        advised = schedule.next_retry(now, '01:00', 'Mon, 09 Nov 2026 12:00:00 GMT')
        self.assertEqual(advised.isoformat(), '2026-11-09T07:00:00-05:00')
        self.assertEqual(schedule.next_allowed(now, '01:00', '07:00', advised).isoformat(),
                         '2026-11-10T01:00:00-05:00')

    def test_gate_uses_selected_zone_and_label(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = app.Settings(folder=folder, ai_schedule_enabled=True, ai_schedule_timezone='GMT-05:00')
            messages = []
            controller = app.SessionController(settings, '', messages.append, lambda *_: None,
                                               lambda *_: None, Path(folder))
            # 08:00 UTC = 03:00 GMT-5, inside the default 01:00-07:00 window.
            with patch.object(app, 'datetime') as clock:
                clock.now.return_value = datetime(2026, 11, 7, 8, tzinfo=timezone.utc)
                self.assertFalse(controller._generation_request_gate())
                clock.now.return_value = datetime(2026, 11, 7, 15, tzinfo=timezone.utc)
                with patch.object(controller.generation_cancel_event, 'wait', return_value=True):
                    with self.assertRaises(app.GenerationCancelled):
                        controller._generation_request_gate()
            self.assertIn('GMT-05:00', messages[-1])
            manifest = json.loads(controller.manifest_path.read_text(encoding='utf-8'))
            self.assertEqual(manifest['ai_schedule']['timezone'], 'GMT-05:00')

    def test_saved_delay_survives_timezone_change_and_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = datetime(2026, 11, 8, 2, tzinfo=timezone(timedelta(hours=8)))
            path = Path(folder) / 'session_state.json'
            path.write_text(json.dumps({'generation_not_before': saved.isoformat()}), encoding='utf-8')
            controller = app.SessionController(app.Settings(folder=folder, ai_schedule_timezone='GMT-05:00'),
                                               '', lambda *_: None, lambda *_: None, lambda *_: None, Path(folder))
            self.assertEqual(controller.generation_not_before, saved)
            now = schedule.schedule_time('GMT-05:00', saved - timedelta(hours=1))
            allowed = schedule.next_allowed(now, '01:00', '23:00', controller.generation_not_before)
            self.assertEqual(allowed, saved)
            path.write_text(json.dumps({'generation_not_before': '2026-11-08T02:00:00'}), encoding='utf-8')
            legacy = app.SessionController(app.Settings(folder=folder), '', lambda *_: None,
                                           lambda *_: None, lambda *_: None, Path(folder))
            self.assertEqual(legacy.generation_not_before, datetime(2026, 11, 8, 2).astimezone())

    def test_ui_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.json'
            settings = app.Settings(folder=folder, model='test', use_clash=False)
            config.write_text(json.dumps(asdict(settings)), encoding='utf-8')
            root = app.Tk()
            root.withdraw()
            try:
                with patch.object(app, 'CONFIG_FILE', config):
                    ui = app.LessonNotesApp(root)
                    ui.key_var.set('test-key')
                    ui.ai_schedule_timezone_var.set('GMT+5:45')
                    collected = ui._collect_settings()
                    self.assertEqual(collected.ai_schedule_timezone, 'GMT+05:45')
                    config.write_text(json.dumps(asdict(collected)), encoding='utf-8')
                    self.assertEqual(ui._load_settings().ai_schedule_timezone, 'GMT+05:45')
                    ui.ai_schedule_timezone_var.set('GMT+25')
                    with self.assertRaises(ValueError):
                        ui._collect_settings()
                    # Older settings have no timezone field and retain local-time behavior.
                    config.write_text('{}', encoding='utf-8')
                    self.assertEqual(ui._load_settings().ai_schedule_timezone, '本机时间')
            finally:
                root.destroy()


class VideoRegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.video = self.folder / 'test.mkv'
        create_video(self.video)

    def importer(self, region=None, mode='定时截图', duplicate=False, source=None):
        session = self.folder / ('session-' + str(len(list(self.folder.iterdir()))))
        session.mkdir()
        settings = app.Settings(material_mode='仅截图', capture_mode=mode,
                                capture_interval_seconds=1, skip_duplicate_screenshots=duplicate)
        return VideoImporter(source or self.video, settings, session, lambda _: None,
                             threading.Event(), threading.Event(), capture_region=region,
                             frame_size=[640, 360])

    def test_reverse_scaled_clamped_drag(self):
        self.assertEqual(pixel_region((300, 150), (10, 20), (320, 180), (640, 360)), [20, 40, 580, 260])
        self.assertEqual(pixel_region((-5, -8), (900, 900), (320, 180), (640, 360)), [0, 0, 640, 360])

    def test_reject_invalid_crop(self):
        for roi in [[-1, 0, 20, 20], [0, 0, 641, 360], [0, 0, 1, 20], [0, 0, 20.5, 20]]:
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                validate_region(roi, (640, 360))

    def test_crop_pixels_dimensions_metadata_and_interval(self):
        from PIL import Image
        importer = self.importer([20, 30, 200, 100])
        metadata = importer.run()
        self.assertEqual(metadata['capture_region'], [20, 30, 200, 100])
        self.assertTrue(metadata['screenshots_complete'])
        self.assertEqual(len(importer.frames), 6)
        for frame in importer.frames:
            with Image.open(importer.session_dir / 'screenshots' / frame['file']) as image:
                self.assertEqual(image.size, (200, 100))
                self.assertEqual(image.getpixel((100, 50)), (255, 0, 0))
        self.assertEqual(len(json.loads((importer.session_dir / 'video_frames.json').read_text())), 6)

    def test_outside_motion_does_not_trigger_changes_or_duplicates(self):
        for mode in ['画面变化时', '定时截图']:
            with self.subTest(mode=mode):
                importer = self.importer([0, 0, 320, 360], mode=mode, duplicate=True)
                importer.run()
                self.assertEqual(len(importer.frames), 1)

    def test_inside_changes_are_captured(self):
        changed = self.folder / 'changed.mkv'
        create_video(changed, change_inside=True)
        importer = self.importer([0, 0, 320, 360], mode='画面变化时', source=changed)
        importer.run()
        self.assertEqual(len(importer.frames), 2)
        self.assertGreaterEqual(importer.frames[1]['seconds'], 3)

    def test_full_frame_and_preview_seek(self):
        from PIL import Image
        importer = self.importer()
        importer.run()
        with Image.open(importer.session_dir / 'screenshots' / importer.frames[0]['file']) as image:
            self.assertEqual(image.size, (640, 360))
        first, duration = read_preview(self.video)
        later, _ = read_preview(self.video, 1.5)
        self.assertEqual(first.size, (640, 360))
        self.assertGreater(duration, 5)
        self.assertNotEqual(first.getpixel((500, 100)), later.getpixel((500, 100)))

    def test_invalid_roi_and_cancel_fail_without_full_frame_fallback(self):
        importer = self.importer([500, 0, 300, 100])
        with self.assertRaises(ValueError):
            importer.run()
        self.assertFalse(list(importer.session_dir.glob('screenshots/*.png')))
        importer = self.importer([0, 0, 320, 360])
        importer.stop.set()
        with self.assertRaises(VideoImportCancelled):
            importer.run()

    def test_controller_import_passes_crop_to_worker_and_persists(self):
        done = threading.Event()
        issues = []
        settings = app.Settings(folder=str(self.folder), material_mode='仅截图', capture_mode='定时截图')
        def ready(issue, _path):
            issues.append(issue)
            done.set()
        controller = app.SessionController(settings, '', lambda _: None, ready, lambda *_: None)
        controller.import_video(self.video, {'capture_region': [20, 30, 200, 100], 'frame_size': [640, 360]})
        self.assertTrue(done.wait(15))
        self.assertEqual(issues, [None])
        manifest = json.loads(controller.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(manifest['state'], 'ready_to_generate')
        self.assertEqual(manifest['video_source']['capture_region'], [20, 30, 200, 100])

    def test_preview_dialog_drag_confirm_full_frame_and_cancel(self):
        from tkinter import Canvas, Toplevel, ttk
        root = app.Tk()
        root.geometry('400x100')
        errors = []
        expected_region = []
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def interact(action):
            try:
                dialog = next(child for child in root.winfo_children() if isinstance(child, Toplevel))
                widgets = list(descendants(dialog))
                canvas = next(child for child in widgets if isinstance(child, Canvas))
                if action == 'crop':
                    expected_region[:] = pixel_region((20, 30), (220, 130),
                                                      (int(canvas.cget('width')), int(canvas.cget('height'))),
                                                      (640, 360))
                    canvas.event_generate('<ButtonPress-1>', x=20, y=30)
                    canvas.event_generate('<B1-Motion>', x=220, y=130)
                    canvas.event_generate('<ButtonRelease-1>', x=220, y=130)
                    button = next(child for child in widgets if isinstance(child, ttk.Button)
                                  and child.cget('text') == '使用框选区域并导入')
                    self.assertFalse(button.instate(['disabled']))
                    button.invoke()
                elif action == 'full':
                    next(child for child in widgets if isinstance(child, ttk.Button)
                         and child.cget('text') == '使用完整画面并导入').invoke()
                else:
                    dialog.destroy()
            except Exception as exc:
                errors.append(exc)
                for child in root.winfo_children():
                    if isinstance(child, Toplevel):
                        child.destroy()
        try:
            root.update()
            for action in ['crop', 'full', 'cancel']:
                root.after(250, lambda action=action: interact(action))
                result = select_video_region(root, self.video)
                if errors:
                    raise errors[0]
                if action == 'crop':
                    self.assertEqual(result['capture_region'], expected_region)
                elif action == 'full':
                    self.assertIsNone(result['capture_region'])
                else:
                    self.assertIsNone(result)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
