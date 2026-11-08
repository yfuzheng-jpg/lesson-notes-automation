"""Shared speech-language policy and Simplified Chinese transcript output."""
from __future__ import annotations

TRANSCRIPTION_LANGUAGES = {
    "中文（简体，默认）": "zh",
    "英文": "en",
    "自动识别": "auto",
}
DEFAULT_TRANSCRIPTION_LANGUAGE = "zh"


def normalize_transcription_language(value: str | None) -> str:
    value = (value or DEFAULT_TRANSCRIPTION_LANGUAGE).strip()
    language = TRANSCRIPTION_LANGUAGES.get(value, value)
    if language not in TRANSCRIPTION_LANGUAGES.values():
        raise ValueError("转写语言请选择中文、英文或自动识别。")
    return language


def transcription_options(language: str | None = None) -> dict:
    language = normalize_transcription_language(language)
    options = {
        "language": None if language == "auto" else language,
        "task": "transcribe",
        "vad_filter": True,
        "beam_size": 3,
    }
    if language == "zh":
        options["initial_prompt"] = "简体中文。课堂讲解的逐字转写。"
    return options


def create_simplifier():
    """Use a separate converter per transcription worker; never translate English."""
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError("缺少简繁转换依赖，请关闭程序后双击‘启动课堂笔记.bat’安装依赖。") from exc
    return OpenCC("t2s").convert
