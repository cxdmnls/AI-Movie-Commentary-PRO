from __future__ import annotations


import importlib
import logging
import subprocess
from pathlib import Path

conf = importlib.import_module("conf")

logger = logging.getLogger(__name__)


def extract_audio(video_path: str, output_path: str) -> str:
    """使用 ffmpeg 从视频提取 16kHz 单声道 WAV 音频。"""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = [
        conf.FFMPEG_BIN,
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_file),
    ]
    logger.info("开始提取音频: %s -> %s", video_path, output_file)
    result = subprocess.run(command, capture_output=True, text=False, check=False)
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="ignore").strip()
        logger.error("ffmpeg 提取音频失败: %s", stderr_text)
        raise RuntimeError(f"音频提取失败: {stderr_text}")

    logger.info("音频提取完成: %s", output_file)
    return str(output_file)


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS.mmm 时间戳。"""
    safe_seconds = max(0.0, float(seconds))
    total_milliseconds = int(round(safe_seconds * 1000))
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"
