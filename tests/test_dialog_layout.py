import gc
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tkinter as tk
from tkinter import font, ttk
from PIL import Image
import video_batch
import video_region


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class DialogLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('400x100+0+0')
        self.root.tk.call('tk', 'scaling', 2.0)
        for name in ['TkDefaultFont', 'TkTextFont', 'TkHeadingFont']:
            font.nametofont(name, root=self.root).configure(size=18)
        ttk.Style(self.root).configure('.', font=('Microsoft YaHei UI', 18))
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        gc.collect()

    def inside(self, widget, dialog):
        self.assertTrue(widget.winfo_ismapped(), widget.winfo_name())
        self.assertGreaterEqual(widget.winfo_rootx(), dialog.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(), dialog.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())

    def run_modal(self, callback, factory):
        errors = []
        deadline = time.monotonic() + 3
        def inspect():
            dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
            if not dialog.winfo_viewable() and time.monotonic() < deadline:
                self.root.after(30, inspect)
                return
            try:
                dialog.update_idletasks()
                callback(dialog)
            except BaseException as exc:
                errors.append(exc)
                if dialog.winfo_exists():
                    dialog.destroy()
        self.root.after(100, inspect)
        result = factory()
        if errors:
            raise errors[0]
        return result

    def test_batch_footer_is_visible_without_resizing_at_small_sizes(self):
        for width, height in [(640, 400), (720, 440)]:
            with self.subTest(width=width, height=height), patch.object(video_region, '_dialog_work_area', return_value=(0, 0, width + 32, height + 60)):
                def inspect(dialog):
                    self.assertEqual((dialog.winfo_width(), dialog.winfo_height()), (width, height))
                    widgets = list(descendants(dialog))
                    buttons = {w.cget('text'): w for w in widgets if isinstance(w, ttk.Button)}
                    self.inside(buttons['开始批量处理'], dialog)
                    self.inside(buttons['取消'], dialog)
                    combo = next(w for w in widgets if w.winfo_name() == 'processing_mode')
                    self.inside(combo, dialog)
                    viewport = next(w for w in widgets if w.winfo_name() == 'dialog_viewport')
                    self.assertGreater(float(viewport.yview()[1]), 0)
                    self.assertLess(float(viewport.yview()[1]), 1)
                    viewport.yview_moveto(1)
                    dialog.update_idletasks()
                    self.inside(buttons['开始批量处理'], dialog)
                    buttons['开始批量处理'].invoke()
                result = self.run_modal(inspect, lambda: video_batch.select_video_batch(self.root, ['a.mkv', 'b.mkv']))
                self.assertTrue(result['generate'])
                self.assertEqual(result['material_mode'], '转写＋截图')

    def test_non_note_processing_modes_never_generate_or_review(self):
        cases = [('仅保存截图（不生成笔记）', '仅截图'),
                 ('仅转写音频（不生成笔记）', '仅转写'),
                 ('仅保存截图和转写（不生成笔记）', '转写＋截图')]
        for label, material in cases:
            with self.subTest(label=label):
                def inspect(dialog):
                    widgets = list(descendants(dialog))
                    review = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and w.cget('text').startswith('每个视频生成前'))
                    review.invoke()
                    next(w for w in widgets if w.winfo_name() == 'processing_mode').set(label)
                    self.assertTrue(review.instate(['disabled']))
                    next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '开始批量处理').invoke()
                result = self.run_modal(inspect, lambda: video_batch.select_video_batch(self.root, ['a.mkv']))
                self.assertFalse(result['generate'])
                self.assertFalse(result['review'])
                self.assertEqual(result['material_mode'], material)

    def test_legacy_generate_checkbox_and_default_material_are_compatible(self):
        def inspect(dialog):
            widgets = list(descendants(dialog))
            next(w for w in widgets if isinstance(w, ttk.Checkbutton) and w.cget('text').startswith('导入后自动生成')).invoke()
            next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '开始批量处理').invoke()
        result = self.run_modal(inspect, lambda: video_batch.select_video_batch(self.root, ['a.mkv']))
        self.assertFalse(result['generate'])
        for material in ['仅截图', '仅转写']:
            def accept(dialog):
                next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget('text') == '开始批量处理').invoke()
            result = self.run_modal(accept, lambda: video_batch.select_video_batch(self.root, ['a.mkv'], default_material_mode=material))
            self.assertFalse(result['generate'])
            self.assertEqual(result['material_mode'], material)

    def test_region_actions_remain_visible_and_time_range_is_preserved(self):
        for screenshots in [True, False]:
            with self.subTest(screenshots=screenshots), \
                    patch.object(video_region, '_dialog_work_area', return_value=(0, 0, 672, 460)), \
                    patch.object(video_region, 'read_preview', return_value=(Image.new('RGB', (640, 360)), 120)):
                def inspect(dialog):
                    widgets = list(descendants(dialog))
                    # Existing clients and GUI tests discover this canvas first.
                    preview = next(w for w in widgets if isinstance(w, tk.Canvas))
                    self.assertNotEqual(preview.winfo_name(), 'dialog_viewport')
                    action = '使用完整画面并导入' if screenshots else '导入此时间段'
                    buttons = {w.cget('text'): w for w in widgets if isinstance(w, ttk.Button)}
                    self.inside(buttons[action], dialog)
                    self.inside(buttons['取消'], dialog)
                    if screenshots:
                        self.inside(buttons['使用框选区域并导入'], dialog)
                        self.inside(buttons['重新框选'], dialog)
                    buttons[action].invoke()
                result = self.run_modal(inspect, lambda: video_region.select_video_region(self.root, Path('a.mkv'), screenshots=screenshots,
                    initial_selection={'start_seconds': 10, 'end_seconds': 30}))
                self.assertEqual(result['start_seconds'], 10)
                self.assertEqual(result['end_seconds'], 30)


if __name__ == '__main__':
    unittest.main()
