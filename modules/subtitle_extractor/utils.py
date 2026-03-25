from __future__ import annotations


import importlib
import logging
import re
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


def load_srt_as_subtitles(srt_path: str) -> list[dict[str, float | str]]:
    """将 SRT 文件解析为统一字幕结构。"""
    srt_file = Path(srt_path)
    if not srt_file.exists():
        raise FileNotFoundError(f"SRT 文件不存在: {srt_path}")

    content = srt_file.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n").strip())

    subtitles: list[dict[str, float | str]] = []
    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue

        time_line_index = 1 if re.fullmatch(r"\d+", lines[0]) else 0
        if time_line_index >= len(lines):
            continue

        time_line = lines[time_line_index]
        if "-->" not in time_line:
            continue

        raw_start, raw_end = [part.strip() for part in time_line.split("-->", 1)]
        start = _parse_srt_time(raw_start)
        end = _parse_srt_time(raw_end)
        if end <= start:
            continue

        text_lines = lines[time_line_index + 1 :]
        text = " ".join(text_lines).strip()
        if not text:
            continue

        subtitles.append({"start": start, "end": end, "text": text})

    logger.info("SRT 解析完成: %s, 共 %d 条", srt_file, len(subtitles))
    return subtitles


def _parse_srt_time(value: str) -> float:
    """解析 SRT 时间戳（HH:MM:SS,mmm / HH:MM:SS.mmm）。"""
    normalized = value.strip().replace(",", ".")
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?", normalized)
    if not match:
        return 0.0

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4) or "0"
    milliseconds = int(fraction.ljust(3, "0")[:3])
    return float(hours * 3600 + minutes * 60 + seconds + milliseconds / 1000)
