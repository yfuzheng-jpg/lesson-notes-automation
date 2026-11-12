import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lesson_notes_automation as app
from screenshot_filter import (difference_percent, is_distinct, screenshot_fingerprint,
                               validate_difference_percent)
from video_batch import VideoBatch, VideoJob, select_video_batch
from video_import import VideoImporter


def page(width=0):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (320, 180), "white")
    if width:
        ImageDraw.Draw(image).rectangle((0, 0, width - 1, 179), fill="black")
    return image


def write_video(path, frames):
    import av
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=4)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "bgr0"
        for image in frames:
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class DifferenceTests(unittest.TestCase):
    def test_area_percent_noise_and_mouse_are_ignored(self):
        import numpy as np
        from PIL import Image, ImageDraw
        reference = screenshot_fingerprint(page())
        pixels = np.full((180, 320, 3), 250, dtype=np.uint8)
        noise = Image.fromarray(pixels)
        ImageDraw.Draw(noise).rectangle((40, 40, 48, 48), fill="black")
        self.assertLess(difference_percent(screenshot_fingerprint(noise), reference), 1)
        self.assertFalse(is_distinct(screenshot_fingerprint(page(32)), reference, 15))
        self.assertTrue(is_distinct(screenshot_fingerprint(page(64)), reference, 15))
        self.assertTrue(is_distinct(reference, reference, 0))
        self.assertTrue(is_distinct(reference, None, 100))

    def test_rgb_detects_equal_luminance_color_changes(self):
        from PIL import Image
        red = Image.new("RGB", (320, 180), (255, 0, 0))
        green = Image.new("RGB", (320, 180), (0, 130, 0))
        self.assertEqual(red.convert("L").getpixel((0, 0)), green.convert("L").getpixel((0, 0)))
        self.assertEqual(difference_percent(screenshot_fingerprint(red), screenshot_fingerprint(green)), 100)

    def test_threshold_validation_and_boundary(self):
        import numpy as np
        reference = np.zeros((10, 10, 3), dtype=np.int16)
        changed = reference.copy()
        changed.reshape(-1, 3)[:15] = 255
        self.assertEqual(difference_percent(changed, reference), 15)
        self.assertTrue(is_distinct(changed, reference, 15))
        for value in ["nan", "inf", -1, 101, "", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_difference_percent(value)
        self.assertEqual(validate_difference_percent("15.5"), 15.5)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "slides.mkv"
        write_video(self.source, [page()] * 8 + [page(25)] * 8 + [page(60)] * 8)

    def extract(self, mode="画面变化时", threshold=15, skip=True, source=None):
        folder = self.folder / f"session-{len(list(self.folder.iterdir()))}"
        folder.mkdir()
        settings = app.Settings(material_mode="仅截图", capture_mode=mode,
            capture_interval_seconds=1, skip_duplicate_screenshots=skip,
            screenshot_difference_percent=threshold)
        importer = VideoImporter(source or self.source, settings, folder, lambda _: None,
                                 threading.Event(), threading.Event())
        importer.run()
        return importer

    def test_fifteen_percent_compares_to_last_kept_and_accumulates(self):
        for mode in ["画面变化时", "定时截图"]:
            with self.subTest(mode=mode):
                importer = self.extract(mode)
                # 7.8% edit is skipped; total 18.8% against the saved page is kept.
                self.assertEqual(len(importer.frames), 2)
                self.assertGreaterEqual(importer.frames[1]["seconds"], 4)
                self.assertGreaterEqual(importer.frames[1]["difference_percent"], 15)
                self.assertEqual(importer.metadata["screenshot_difference_percent"], 15)
                self.assertEqual(len(self.extract(mode, threshold=5).frames), 3)
                self.assertEqual(len(self.extract(mode, threshold=25).frames), 1)

    def test_interval_filter_can_be_disabled(self):
        self.assertEqual(len(self.extract("定时截图", skip=False).frames), 6)
        self.assertEqual(len(self.extract("定时截图", threshold=0).frames), 6)

    def test_eof_preserves_distinct_last_page(self):
        source = self.folder / "short-final-page.mkv"
        write_video(source, [page()] * 14 + [page(70)] * 2)
        importer = self.extract(source=source)
        self.assertEqual(len(importer.frames), 2)
        self.assertEqual(importer.frames[-1]["seconds"], 3.5)

    def test_screen_recording_uses_the_same_filter(self):
        import numpy as np
        from types import SimpleNamespace
        images = [page(), page(25), page(60), page(60)]
        controller = app.SessionController(app.Settings(folder=str(self.folder), material_mode="仅截图",
            capture_mode="定时截图", capture_interval_seconds=1, capture_region_only=False),
            "", lambda _: None, lambda *_: None, lambda *_: None)
        controller.images_dir.mkdir(parents=True)
        controller.session_start = 0
        saved = []
        class Screen:
            monitors = [{"width": 320, "height": 180}]
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def grab(self, _):
                image = images.pop(0)
                if not images:
                    controller.stop_event.set()
                return SimpleNamespace(size=image.size, rgb=np.asarray(image).tobytes())
        with patch("mss.MSS", Screen), patch.object(app.time, "sleep"), \
             patch.object(app.time, "time", side_effect=[1, 2, 3, 4]), \
             patch.object(controller, "_save_screenshot", side_effect=lambda image, _: saved.append(image.copy())):
            controller._screen_loop()
        self.assertIsNone(controller.capture_error)
        self.assertEqual(len(saved), 2)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.sources = [self.folder / "第一讲.mkv", self.folder / "第二讲.mkv"]
        for source in self.sources:
            write_video(source, [page()] * 8 + [page(70)] * 8)
        self.callbacks = queue.Queue()
        self.done = []
        self.settings = app.Settings(folder=str(self.folder / "notes"), material_mode="仅截图",
            provider="deepseek", model="local-test", use_clash=False, output_format="Markdown (.md)")

    def batch(self, sources=None, **options):
        batch = VideoBatch([VideoJob(source) for source in (sources or self.sources)], self.settings,
            "synthetic-test-key", app.SessionController, self.callbacks.put, lambda _: None,
            self.done.append, **options)
        batch.start()
        return batch

    def pump(self, batch):
        until = time.monotonic() + 15
        while batch.running and time.monotonic() < until:
            try:
                self.callbacks.get(timeout=0.1)()
            except queue.Empty:
                pass
        self.assertFalse(batch.running, "batch did not finish")
        self.assertEqual(self.done, [batch])

    def test_two_real_videos_generate_separate_notes_and_report(self):
        calls = []
        def notes(_client, images, transcript, title, *args):
            calls.append((title, len(images)))
            return f"# {title}\n\n本地测试笔记"
        with patch.object(app.OpenAICompatibleClient, "create_notes", notes):
            batch = self.batch()
            self.pump(batch)
        self.assertEqual(calls, [("第一讲", 2), ("第二讲", 2)])
        self.assertEqual([job.state for job in batch.jobs], ["笔记已生成"] * 2)
        self.assertNotEqual(batch.jobs[0].session_dir, batch.jobs[1].session_dir)
        for job in batch.jobs:
            self.assertTrue(job.output_path.is_file())
            self.assertIn(job.source.stem, job.output_path.read_text(encoding="utf-8"))
            state = json.loads((job.session_dir / "session_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["screenshot_difference_percent"], 15)
        report = batch.report_path.read_text(encoding="utf-8")
        self.assertNotIn("synthetic-test-key", report)
        self.assertEqual(len(json.loads(report)["jobs"]), 2)

    def test_bad_video_does_not_block_next_and_import_only_uses_no_ai(self):
        bad = self.folder / "broken.mkv"
        bad.write_text("not a video", encoding="utf-8")
        with patch.object(app.SessionController, "generate_notes", side_effect=AssertionError("must not call AI")):
            batch = self.batch([bad, self.sources[0]], generate=False)
            self.pump(batch)
        self.assertEqual([job.state for job in batch.jobs], ["导入失败", "素材已保存"])
        self.assertTrue(batch.jobs[1].session_dir.is_dir())

    def test_generation_failure_continues_and_preserves_materials(self):
        calls = []
        def notes(_client, images, transcript, title, *args):
            calls.append(title)
            if len(calls) == 1:
                raise RuntimeError("synthetic API failure")
            return "# 第二讲笔记"
        with patch.object(app.OpenAICompatibleClient, "create_notes", notes):
            batch = self.batch()
            self.pump(batch)
        self.assertEqual([job.state for job in batch.jobs], ["生成失败", "笔记已生成"])
        self.assertEqual(len(list((batch.jobs[0].session_dir / "screenshots").glob("*.png"))), 2)

    def test_cancel_before_start_and_between_jobs_never_starts_next(self):
        batch = self.batch(generate=False)
        batch.cancel()
        self.pump(batch)
        self.assertTrue(all(job.state.startswith("未处理") for job in batch.jobs))

    def test_cancel_active_import_keeps_materials_and_skips_remaining(self):
        batch = self.batch(generate=False)
        self.callbacks.get(timeout=1)()  # Start the first real decoder.
        batch.cancel()
        self.pump(batch)
        self.assertEqual(batch.jobs[0].state, "已停止")
        self.assertTrue(batch.jobs[0].session_dir.is_dir())
        self.assertTrue(batch.jobs[1].state.startswith("未处理"))
        self.assertIsNone(batch.jobs[1].session_dir)

    def test_cancel_active_generation_never_submits_next_video(self):
        entered = threading.Event()
        calls = []
        def notes(client, *args):
            calls.append(1)
            entered.set()
            if not client.cancel_event.wait(5):
                raise AssertionError("cancel did not reach the active generator")
            raise app.GenerationCancelled("local cancellation")
        with patch.object(app.OpenAICompatibleClient, "create_notes", notes):
            batch = self.batch()
            until = time.monotonic() + 5
            while not entered.is_set() and time.monotonic() < until:
                try:
                    self.callbacks.get(timeout=0.1)()
                except queue.Empty:
                    pass
            self.assertTrue(entered.is_set())
            batch.cancel()
            self.pump(batch)
        self.assertEqual(calls, [1])
        self.assertEqual(batch.jobs[0].state, "已停止")
        self.assertTrue(batch.jobs[1].state.startswith("未处理"))

    def test_same_video_names_get_unique_folders(self):
        other = self.folder / "other"
        other.mkdir()
        duplicate = other / self.sources[0].name
        duplicate.write_bytes(self.sources[0].read_bytes())
        batch = self.batch([self.sources[0], duplicate], generate=False)
        self.pump(batch)
        self.assertEqual([job.state for job in batch.jobs], ["素材已保存"] * 2)
        self.assertNotEqual(batch.jobs[0].session_dir, batch.jobs[1].session_dir)

    def test_review_can_skip_one_job_and_continue(self):
        reviewed = []
        def review(folder):
            reviewed.append(folder)
            return len(reviewed) > 1
        with patch.object(app.OpenAICompatibleClient, "create_notes", return_value="# 本地笔记"):
            batch = self.batch(review=review)
            self.pump(batch)
        self.assertEqual([job.state for job in batch.jobs], ["素材已保存", "笔记已生成"])


class InterfaceTests(unittest.TestCase):
    def test_main_window_drives_real_batch_and_restores_buttons(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "lecture.mkv"
            write_video(source, [page()] * 8 + [page(70)] * 8)
            settings = app.Settings(folder=str(Path(folder) / "notes"), material_mode="仅截图",
                provider="deepseek", model="local-test", use_clash=False, output_format="Markdown (.md)")
            root = app.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(args)
            try:
                with patch.object(app.LessonNotesApp, "_load_settings", return_value=settings), \
                     patch.object(app.LessonNotesApp, "_save_settings", return_value=True), \
                     patch.object(app.filedialog, "askopenfilenames", return_value=[str(source)]), \
                     patch("video_batch.select_video_batch", return_value={"jobs": [VideoJob(source)], "generate": True, "review": False}), \
                     patch.object(app.OpenAICompatibleClient, "create_notes", return_value="# 本地端到端测试"), \
                     patch.object(app.messagebox, "showinfo"), patch.object(app.messagebox, "showerror") as showerror:
                    ui = app.LessonNotesApp(root)
                    ui._import_video_batch()
                    self.assertTrue(ui.batch_video_button.instate(["disabled"]))
                    until = time.monotonic() + 10
                    def poll():
                        if not ui.batch.running or time.monotonic() >= until:
                            root.quit()
                        else:
                            root.after(10, poll)
                    root.after(10, poll)
                    root.mainloop()
                    root.update()
                    self.assertFalse(ui.batch.running)
                    self.assertEqual(ui.batch.jobs[0].state, "笔记已生成")
                    self.assertFalse(ui.start_button.instate(["disabled"]))
                    self.assertFalse(ui.batch_video_button.instate(["disabled"]))
                    self.assertTrue(ui.batch_cancel_button.instate(["disabled"]))
                    self.assertFalse(ui.generate_button.instate(["disabled"]))
                    self.assertEqual(ui.name_var.get(), "lecture")
                    self.assertEqual(errors, [])
                    showerror.assert_not_called()
            finally:
                root.destroy()

    def test_settings_default_migration_roundtrip_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "settings.json"
            config.write_text(json.dumps({"folder": folder, "use_clash": False}), encoding="utf-8")
            root = app.Tk()
            root.withdraw()
            try:
                with patch.object(app, "CONFIG_FILE", config):
                    ui = app.LessonNotesApp(root)
                    self.assertEqual(ui.screenshot_difference_var.get(), "15")
                    # Importing materials alone does not require an AI account/key.
                    ui.key_var.set("")
                    ui.model_var.set("")
                    settings = ui._collect_settings(require_ai=False)
                    self.assertEqual(settings.screenshot_difference_percent, 15)
                    ui.screenshot_difference_var.set("23.5")
                    settings = ui._collect_settings(require_ai=False)
                    config.write_text(json.dumps(asdict(settings)), encoding="utf-8")
                    self.assertEqual(ui._load_settings().screenshot_difference_percent, 23.5)
                    with patch.object(app, "CONFIG_DIR", Path(folder)), patch("keyring.set_password") as save_key:
                        self.assertTrue(ui._save_settings(require_ai=False))
                        save_key.assert_not_called()
                    ui.screenshot_difference_var.set("nan")
                    with self.assertRaises(ValueError):
                        ui._collect_settings(require_ai=False)
            finally:
                root.destroy()

    def test_queue_dialog_reorders_removes_and_collects_import_only(self):
        from tkinter import Toplevel, ttk
        root = app.Tk()
        root.withdraw()
        errors = []
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def interact():
            dialog = next(child for child in root.winfo_children() if isinstance(child, Toplevel))
            try:
                widgets = list(descendants(dialog))
                tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
                buttons = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Button)}
                tree.selection_set("1")
                buttons["上移"].invoke()
                tree.selection_set("1")
                buttons["移除所选"].invoke()
                next(w for w in widgets if isinstance(w, ttk.Checkbutton)
                     and w.cget("text").startswith("导入后自动生成")).invoke()
                buttons["开始批量处理"].invoke()
            except Exception as exc:
                errors.append(exc)
                dialog.destroy()
        try:
            root.after(100, interact)
            result = select_video_batch(root, ["a.mkv", "b.mkv", "c.mkv"])
            if errors:
                raise errors[0]
            self.assertEqual([job.source.name for job in result["jobs"]], ["b.mkv", "c.mkv"])
            self.assertFalse(result["generate"])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
