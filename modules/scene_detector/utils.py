from __future__ import annotations


import importlib
import logging
import subprocess
from pathlib import Path

conf = importlib.import_module("conf")

logger = logging.getLogger(__name__)


def extract_thumbnail(video_path: str, timestamp: float, output_path: str, width: int) -> str:
    """在指定时间点提取视频缩略图。"""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    safe_timestamp = max(0.0, float(timestamp))

    command: list[str] = [
        conf.FFMPEG_BIN,
        "-y",
        "-ss",
        f"{safe_timestamp:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-1",
        str(output_file),
    ]
    logger.info("提取缩略图: t=%.3fs -> %s", safe_timestamp, output_file)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg 提取缩略图失败: %s", result.stderr.strip())
        raise RuntimeError(f"缩略图提取失败: {result.stderr.strip()}")
    return str(output_file)


def seconds_to_timecode(seconds: float) -> str:
    """将秒数转换为 HH:MM:SS.mmm 时间码。"""
    safe_seconds = max(0.0, float(seconds))
    total_milliseconds = int(round(safe_seconds * 1000))
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"
