import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lesson_notes_automation as app
from video_batch import VideoJob
from test_batch_and_dedup import page, write_video


class MaterialsOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = app.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        settings = app.Settings(folder=str(self.folder/'notes'), provider='chatgpt_oauth', model='', use_clash=True)
        with patch.object(app.LessonNotesApp, '_load_settings', return_value=settings):
            self.ui = app.LessonNotesApp(self.root)
        self.ui.key_var.set('')
        self.ui.model_var.set('')

    def test_capture_settings_do_not_require_ai_or_export_configuration(self):
        self.ui.format_var.set(app.CUSTOM_FORMAT)
        self.ui.custom_format_var.set('')
        self.ui.material_mode_var.set('仅截图')
        with patch('siwc_auth.load_credentials', side_effect=AssertionError('must not read credentials')):
            result = self.ui._collect_settings(require_ai=False)
        self.assertEqual(result.material_mode, '仅截图')

    def test_existing_queue_restarts_screenshots_only_without_ai(self):
        source = self.folder/'lecture.mkv'
        write_video(source, [page()]*8 + [page(80)]*8)
        plan = self.folder/'queue.json'
        plan.write_text(json.dumps({'folder':str(self.folder/'notes'), 'jobs':[{'source':str(source),'selection':{}}]}),encoding='utf-8')
        self.ui.settings = app.Settings(folder=str(self.folder/'notes'), material_mode='转写＋截图', use_clash=False)
        with patch.object(app, 'OpenAICompatibleClient', side_effect=AssertionError('must not call AI')), \
             patch.object(app, 'ChatGPTOAuthClient', side_effect=AssertionError('must not call AI')):
            self.ui.start_screenshots_queue(plan)
            end = time.monotonic()+15
            def poll():
                if not self.ui.batch.running or time.monotonic()>end:
                    self.root.quit()
                else:
                    self.root.after(20,poll)
            self.root.after(20,poll)
            self.root.mainloop()
        batch = self.ui.batch
        self.assertFalse(batch.running)
        self.assertFalse(batch.generate)
        self.assertEqual(batch.settings.material_mode, '仅截图')
        self.assertEqual(batch.jobs[0].state, '素材已保存')
        self.assertTrue(list((batch.jobs[0].session_dir/'screenshots').glob('*.png')))
        self.assertIsNone(batch.jobs[0].output_path)

    def test_single_video_transcription_button_does_not_require_ai(self):
        source = self.folder/'video.mkv'
        source.write_bytes(b'test')
        saves=[]
        def save(require_ai=True):
            saves.append(require_ai)
            self.ui.settings = self.ui._collect_settings(require_ai=require_ai)
            return True
        with patch.object(app.filedialog,'askopenfilename',return_value=str(source)), \
             patch.object(self.ui,'_save_settings',side_effect=save), \
             patch('video_region.select_video_region',return_value=None) as preview:
            self.ui._import_video_file(material_only='仅转写')
        self.assertEqual(saves,[False])
        self.assertEqual(self.ui.settings.material_mode,'仅转写')
        self.assertFalse(preview.call_args.kwargs['screenshots'])

    def test_worker_callbacks_are_queued_without_tk_cross_thread_calls(self):
        import threading
        with patch.object(self.root,'after',side_effect=AssertionError('worker must not enter Tk')):
            worker=threading.Thread(target=lambda:self.ui._status_from_thread('queued'))
            worker.start();worker.join(2)
            self.assertFalse(worker.is_alive())
        self.ui._drain_ui_events()
        self.assertEqual(self.ui.status_var.get(),'queued')
