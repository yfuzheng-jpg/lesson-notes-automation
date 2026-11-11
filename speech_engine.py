"""Validated transcription shared by video imports and live recording."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from types import SimpleNamespace

from diagnostics import app_version, write_json
from transcription import create_simplifier, normalize_transcription_language, transcription_options


def character_counts(text):
    return len(re.findall(r'[\u4e00-\u9fff]', text)), len(re.findall(r'[A-Za-z]', text))


def looks_like_wrong_language(text):
    han, latin = character_counts(text)
    return latin >= 100 and han < 3


def validate_existing_transcript(text, language):
    if normalize_transcription_language(language) == 'zh' and looks_like_wrong_language(text):
        raise ValueError('当前选用中文，但已有转写稿几乎全是英文，已停止 AI 生成，避免继续使用错误材料。'
                         '“生成／重试”不会重新转写；请从原视频重新导入。若原课程确为英文，请改选英文。')


class SpeechEngine:
    def __init__(self, model, language, report_path, status=lambda _: None, gate=lambda: None):
        self.model = model
        self.language = normalize_transcription_language(language)
        self.options = transcription_options(self.language)
        self.options.update(temperature=0.0, condition_on_previous_text=False)
        self.simplify = create_simplifier()
        self.status = status
        self.gate = gate
        self.report_path = Path(report_path)
        self.english_run = 0
        multilingual = getattr(getattr(model, 'model', None), 'is_multilingual', None)
        supported = getattr(model, 'supported_languages', None)
        self.report = {'report_version': 1, 'app_version': app_version(),
                       'started_at': datetime.now().isoformat(timespec='seconds'),
                       'language': self.language, 'task': 'transcribe',
                       'model_multilingual': multilingual if isinstance(multilingual, bool) else None,
                       'chunks': 0, 'retries': 0, 'han_characters': 0, 'latin_characters': 0,
                       'status': 'ready'}
        self._save()
        if self.language == 'zh' and (multilingual is False or isinstance(supported, (list, tuple)) and 'zh' not in supported):
            self._fail('english_only_model', '当前语音模型只支持英文，无法按中文转写。请使用多语言 Whisper base 模型，'
                       '并通过“导出故障信息”反馈模型检查结果。')
        label = {'zh': '中文（统一简体）', 'en': '英文', 'auto': '自动识别'}[self.language]
        self.status(f'语音转写设置：{label}；任务：原文转写，不翻译。')

    def _save(self):
        write_json(self.report_path, self.report)

    def _fail(self, code, message):
        self.report['status'] = code
        self._save()
        raise RuntimeError(message)

    def _decode(self, samples, options):
        self.gate()
        segments, info = self.model.transcribe(samples, **options)
        result = []
        for segment in segments:
            self.gate()
            result.append(SimpleNamespace(text=self.simplify(segment.text),
                                          start=getattr(segment, 'start', 0), end=getattr(segment, 'end', 0)))
        effective = getattr(info, 'language', None)
        self.report['effective_language'] = effective if effective in ('zh', 'en') else 'other_or_unknown'
        return result

    def transcribe(self, samples):
        try:
            result = self._decode(samples, self.options)
            text = ''.join(s.text for s in result)
            han, latin = character_counts(text)
            next_run = 0 if han >= 3 else self.english_run + latin
            if self.language == 'zh' and (looks_like_wrong_language(text) or next_run >= 200):
                self.report['retries'] += 1
                self.status('中文模式下检测到连续英文输出，正在按中文重新识别当前片段…')
                self._save()
                retry = dict(self.options, beam_size=5, best_of=1)
                result = self._decode(samples, retry)
                text = ''.join(s.text for s in result)
                han, latin = character_counts(text)
                next_run = 0 if han >= 3 else self.english_run + latin
                if looks_like_wrong_language(text) or next_run >= 200:
                    self._fail('wrong_language', '中文转写连续得到英文，重试后仍异常，已提前停止，未把当前异常片段写入转写稿。'
                               '请确认正在运行新版，并导出故障信息；不要继续用英文稿生成笔记。')
            self.english_run = next_run
            self.report['chunks'] += 1
            self.report['han_characters'] += han
            self.report['latin_characters'] += latin
            self.report['status'] = 'transcribing'
            self._save()
            return result
        except Exception:
            if self.report['status'] not in ('wrong_language', 'english_only_model'):
                self.report['status'] = 'transcription_failed'
                self._save()
            raise
