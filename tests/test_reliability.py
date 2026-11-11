import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics
import lesson_notes_automation as app
from speech_engine import SpeechEngine, validate_existing_transcript

ENGLISH = 'This is an English sentence that must never replace a Chinese lecture transcript. ' * 4
CHINESE = '今天学习電壓與电流，保留 OpenAI API 和数字 123。'


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.local = patch.object(diagnostics, 'last_failure_path', return_value=self.folder / 'last_failure.json')
        self.local.start()
        self.addCleanup(self.local.stop)

    def engine(self, responses, language='zh'):
        calls = []
        class Model:
            model = SimpleNamespace(is_multilingual=True)
            supported_languages = ['zh', 'en']
            def transcribe(self, samples, **kwargs):
                calls.append(kwargs)
                value = next(responses)
                return iter([SimpleNamespace(text=value, start=0, end=10)]), SimpleNamespace(language='en')
        return SpeechEngine(Model(), language, self.folder / 'speech.json'), calls

    def test_english_only_model_is_rejected_before_inference(self):
        model = SimpleNamespace(model=SimpleNamespace(is_multilingual=False), supported_languages=['en'])
        with self.assertRaisesRegex(RuntimeError, '只支持英文'):
            SpeechEngine(model, 'zh', self.folder / 'speech.json')
        self.assertEqual(json.loads((self.folder / 'speech.json').read_text(encoding='utf-8'))['status'], 'english_only_model')

    def test_wrong_language_retries_and_returns_simplified_chinese(self):
        engine, calls = self.engine(iter([ENGLISH, CHINESE]))
        segments = engine.transcribe(None)
        self.assertIn('电压与电流', segments[0].text)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c['language'] == 'zh' and c['task'] == 'transcribe' for c in calls))
        self.assertEqual(calls[1]['temperature'], 0.0)
        self.assertFalse(calls[1]['condition_on_previous_text'])

    def test_persistent_english_is_rejected_without_exposing_text_in_report(self):
        engine, calls = self.engine(iter([ENGLISH, ENGLISH]))
        with self.assertRaisesRegex(RuntimeError, '提前停止'):
            engine.transcribe(None)
        self.assertEqual(len(calls), 2)
        text = (self.folder / 'speech.json').read_text(encoding='utf-8')
        self.assertNotIn(ENGLISH[:40], text)
        self.assertEqual(json.loads(text)['status'], 'wrong_language')

    def test_short_english_terms_preserve_spaces_and_english_mode_is_allowed(self):
        engine, calls = self.engine(iter([' DNA API ', ENGLISH]), language='en')
        self.assertEqual(engine.transcribe(None)[0].text, ' DNA API ')
        self.assertEqual(engine.transcribe(None)[0].text, ENGLISH)
        self.assertEqual(len(calls), 2)
        validate_existing_transcript(ENGLISH, 'en')
        validate_existing_transcript(ENGLISH, 'auto')
        validate_existing_transcript('中文讲解 DNA API', 'zh')

    def test_old_english_transcript_is_blocked_before_ai(self):
        with self.assertRaisesRegex(ValueError, '不会重新转写'):
            validate_existing_transcript(ENGLISH, 'zh')
        controller = app.SessionController(app.Settings(folder=str(self.folder), material_mode='仅转写', use_clash=False),
                                           '', lambda _: None, lambda *_: None, lambda *_: None)
        controller.session_dir.mkdir()
        controller.transcript_path.write_text('视频转写原文\n\n' + ENGLISH, encoding='utf-8')
        with patch.object(app, 'OpenAICompatibleClient', side_effect=AssertionError('Must not call AI')):
            controller._generate_notes()
        state = json.loads(controller.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(state['state'], 'generation_failed')
        self.assertEqual(state['failure_stage'], '素材检查')
        self.assertIn('几乎全是英文', state['error'])

    def test_failure_report_does_not_include_credentials_or_course_text(self):
        secret = 'CANARY_PRIVATE_TOKEN_987654'
        diagnostics.record_failure(self.folder, 'AI 整理', RuntimeError('HTTP 401 ' + secret + ENGLISH))
        with patch.object(diagnostics, '_read_json', wraps=diagnostics._read_json) as read:
            report = diagnostics.build_report(self.folder)
        text = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret, text)
        self.assertNotIn(ENGLISH, text)
        self.assertEqual(report['last_failure']['http_status'], 401)
        self.assertEqual(report['last_failure']['stage'], 'AI 整理')

    def test_generation_failure_reports_ai_stage_and_retains_transcript(self):
        controller = app.SessionController(app.Settings(folder=str(self.folder), material_mode='仅转写', use_clash=False),
                                           '', lambda _: None, lambda *_: None, lambda *_: None)
        controller.session_dir.mkdir()
        controller.transcript_path.write_text('视频转写原文\n\n' + CHINESE, encoding='utf-8')
        with patch.object(app.OpenAICompatibleClient, 'create_notes', side_effect=RuntimeError('HTTP 503 unavailable')):
            controller._generate_notes()
        state = json.loads(controller.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(state['failure_stage'], 'AI 整理')
        self.assertIn('AI 整理失败', state['error'])
        self.assertIn(CHINESE, controller.transcript_path.read_text(encoding='utf-8'))

    def test_export_failure_is_distinguished_from_ai_failure(self):
        controller = app.SessionController(app.Settings(folder=str(self.folder), material_mode='仅转写', use_clash=False),
                                           '', lambda _: None, lambda *_: None, lambda *_: None)
        controller.session_dir.mkdir()
        controller.transcript_path.write_text('视频转写原文\n\n' + CHINESE, encoding='utf-8')
        with patch.object(app.OpenAICompatibleClient, 'create_notes', return_value='笔记'), \
                patch.object(app, 'save_notes', side_effect=RuntimeError('LaTeX unavailable')):
            controller._generate_notes()
        state = json.loads(controller.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(state['failure_stage'], '文档导出')
        self.assertIn('文档导出失败', state['error'])


if __name__ == '__main__':
    unittest.main()
