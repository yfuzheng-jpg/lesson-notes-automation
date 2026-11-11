"""Local diagnostics without credentials, course text, audio, or screenshots."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
from functools import lru_cache
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

REPORT_VERSION = 1


@lru_cache(maxsize=1)
def app_version():
    try:
        tree = ast.parse((Path(__file__).parent / 'lesson_notes_automation.py').read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets):
                return ast.literal_eval(node.value)
    except (OSError, SyntaxError, ValueError):
        pass
    return 'unknown'


def write_json(path, data):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        return True
    except OSError:
        return False


def last_failure_path():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    return base / 'LessonNotesAutomation/last_failure_diagnostics.json'


def record_failure(session_dir, stage, error):
    # Exception bodies can echo request content or credentials. Store only
    # structured error metadata; the normal UI retains its existing message.
    text = str(error)
    match = re.search(r'HTTP\s+(\d{3})', text)
    event = {'report_version': REPORT_VERSION, 'app_version': app_version(),
             'time': datetime.now().isoformat(timespec='seconds'), 'stage': stage,
             'error_type': type(error).__name__, 'http_status': int(match[1]) if match else None}
    if session_dir:
        write_json(Path(session_dir) / 'failure_diagnostics.json', event)
    write_json(last_failure_path(), event)
    return event


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def build_report(session_dir=None):
    folder = Path(__file__).resolve().parent
    packages = {}
    for name in ['faster-whisper', 'ctranslate2', 'av', 'numpy', 'opencc-python-reimplemented', 'requests', 'reportlab', 'python-docx']:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = '未安装'
    config = _read_json(Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'LessonNotesAutomation/settings.json')
    language = config.get('transcription_language', 'zh')
    report = {'report_version': REPORT_VERSION, 'app_version': app_version(),
              'created_at': datetime.now().isoformat(timespec='seconds'),
              'python': platform.python_version(), 'python_bits': 64 if sys.maxsize > 2**32 else 32,
              'windows': platform.release(), 'packages': packages,
              'configured_language': language if language in ('zh', 'en', 'auto') else 'invalid',
              'task': 'transcribe', 'simplified_chinese': True,
              'latex_compiler_available': bool(shutil.which('xelatex') or shutil.which('pdflatex')),
              'privacy': '不含密钥、令牌、课程正文、截图、音频或原视频路径。'}
    failure = _read_json(Path(session_dir) / 'failure_diagnostics.json') if session_dir else _read_json(last_failure_path())
    report['last_failure'] = {k: failure.get(k) for k in ('app_version', 'time', 'stage', 'error_type', 'http_status')}
    if session_dir:
        speech = _read_json(Path(session_dir) / 'transcription_diagnostics.json')
        report['speech'] = {k: speech.get(k) for k in (
            'app_version', 'language', 'task', 'model_multilingual', 'status', 'chunks', 'retries', 'han_characters', 'latin_characters')}
    return report


def export_report(path, session_dir=None):
    if not write_json(path, build_report(session_dir)):
        raise OSError('无法保存故障报告，请选择可写入的文件夹。')
    return Path(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='导出课堂笔记故障信息，不包含课程材料或密钥。')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / '故障诊断报告.json')
    args = parser.parse_args()
    export_report(args.output)
    print('故障诊断报告已保存。请将“故障诊断报告.json”反馈给维护者。')
