import json
import sys
import tempfile
import threading
import unittest
import wave
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lesson_notes_automation as app
from transcription import (TRANSCRIPTION_LANGUAGES, create_simplifier,
                           normalize_transcription_language, transcription_options)
from video_import import VideoImporter


MIXED = '繁體中文與简体混合，課堂筆記、學習與復習；OpenAI API、DNA、123、x+y=2。'
SIMPLIFIED = '繁体中文与简体混合，课堂笔记、学习与复习；OpenAI API、DNA、123、x+y=2。'


class TranscriptionLanguageTests(unittest.TestCase):
    def test_defaults_and_explicit_modes(self):
        self.assertEqual(app.Settings().transcription_language, 'zh')
        for value in [None, '', 'zh', '中文（简体，默认）']:
            with self.subTest(value=value):
                options = transcription_options(value)
                self.assertEqual(options['language'], 'zh')
                self.assertEqual(options['task'], 'transcribe')
                self.assertIn('简体中文', options['initial_prompt'])
        for value in ['en', '英文', 'auto', '自动识别']:
            options = transcription_options(value)
            self.assertEqual(options['language'], 'en' if value in ['en', '英文'] else None)
            self.assertEqual(options['task'], 'transcribe')
            self.assertNotIn('initial_prompt', options)
        with self.assertRaises(ValueError):
            normalize_transcription_language('invalid')

    def test_simplification_preserves_terms_formulas_and_timestamps(self):
        simplify = create_simplifier()
        self.assertEqual(simplify(MIXED), SIMPLIFIED)
        self.assertEqual(simplify(SIMPLIFIED), SIMPLIFIED)
        english = '[00:00:01 - 00:00:30] OpenAI API: E = mc^2, 15%, DNA.'
        self.assertEqual(simplify(english), english)

    def test_video_chunks_do_not_inherit_first_language_and_write_simplified(self):
        # Real WAV decoding/resampling spans three Whisper calls; the fake model
        # deliberately reports English to reproduce the old first-chunk lock.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'speech.wav'
            with wave.open(str(source), 'wb') as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16000)
                target.writeframes(b'\xe8\x03' * (16000 * 65))
            for language in ['zh', 'en', 'auto']:
                with self.subTest(language=language):
                    calls = []
                    class Model:
                        def transcribe(self, audio, **options):
                            calls.append(options)
                            return iter([SimpleNamespace(start=0, end=1000, text=MIXED)]), SimpleNamespace(language='en')
                    session = folder / language
                    session.mkdir()
                    importer = VideoImporter(source, app.Settings(transcription_language=language), session,
                                             lambda _: None, threading.Event(), threading.Event(),
                                             model_factory=lambda *args, **kwargs: Model())
                    importer.metadata = {'timeline_origin_seconds': 0, 'duration_seconds': 65}
                    importer.transcribe_audio()
                    self.assertEqual(len(calls), 3)
                    self.assertTrue(all(call['language'] == (None if language == 'auto' else language)
                                        and call['task'] == 'transcribe' for call in calls))
                    transcript = (session / 'transcript.txt').read_text(encoding='utf-8')
                    self.assertEqual(transcript.count(SIMPLIFIED), 3)
                    self.assertNotIn('繁體', transcript)
                    for stamp in ['[00:00:00 - 00:00:30]', '[00:00:30 - 00:01:00]', '[00:01:00 - 00:01:05]']:
                        self.assertIn(stamp, transcript)

    def test_live_recording_uses_same_policy_and_persists_simplified(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            for language in ['zh', 'en', 'auto']:
                with self.subTest(language=language):
                    calls = []
                    class Model:
                        def transcribe(self, audio, **options):
                            calls.append(options)
                            return iter([SimpleNamespace(text=MIXED)]), SimpleNamespace(language='en')
                    controller = app.SessionController(app.Settings(folder=folder, transcription_language=language),
                                                       '', lambda _: None, lambda *_: None, lambda *_: None)
                    controller.session_dir.mkdir(parents=True)
                    controller.audio_queue.put(np.ones(16000, dtype=np.float32))
                    controller.audio_queue.put(np.ones(16000, dtype=np.float32))
                    controller.audio_queue.put(None)
                    with patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=lambda *a, **k: Model())}):
                        controller._transcribe_loop()
                    self.assertIsNone(controller.transcript_error)
                    self.assertEqual(len(calls), 2)
                    self.assertTrue(all(call['language'] == (None if language == 'auto' else language)
                                        and call['task'] == 'transcribe' for call in calls))
                    transcript = controller.transcript_path.read_text(encoding='utf-8')
                    self.assertEqual(transcript.count(SIMPLIFIED), 2)
                    self.assertTrue(all(SIMPLIFIED in part for part in controller.transcript_parts))

    def test_old_settings_default_and_ui_language_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.json'
            previous = asdict(app.Settings(folder=folder, model='test', use_clash=False))
            del previous['transcription_language']
            config.write_text(json.dumps(previous), encoding='utf-8')
            with patch.object(app, 'CONFIG_FILE', config):
                self.assertEqual(app.LessonNotesApp._load_settings().transcription_language, 'zh')
                root = app.Tk()
                root.withdraw()
                try:
                    with patch.dict(sys.modules, {'keyring': SimpleNamespace(get_password=lambda *a: '')}):
                        ui = app.LessonNotesApp(root)
                    self.assertEqual(ui.transcription_language_var.get(), '中文（简体，默认）')
                    ui.key_var.set('test-key')
                    for label, language in TRANSCRIPTION_LANGUAGES.items():
                        ui.transcription_language_var.set(label)
                        settings = ui._collect_settings()
                        self.assertEqual(settings.transcription_language, language)
                        config.write_text(json.dumps(asdict(settings)), encoding='utf-8')
                        self.assertEqual(app.LessonNotesApp._load_settings().transcription_language, language)
                finally:
                    root.destroy()


if __name__ == '__main__':
    unittest.main()
