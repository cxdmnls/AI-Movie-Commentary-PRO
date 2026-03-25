"""M1 — 字幕提取模块（faster-whisper）"""
from .extractor import SubtitleExtractor
from .utils import load_srt_as_subtitles

__all__ = ["SubtitleExtractor", "load_srt_as_subtitles"]
