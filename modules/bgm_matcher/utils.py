"""BGM 匹配模块工具函数。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import conf

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac"}


def list_audio_files(directory: str) -> list[str]:
    """列出目录下所有音频文件（mp3/wav/flac）。"""
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return []
    files = [
        str(path)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES
    ]
    files.sort()
    return files


def trim_audio(
    input_path: str,
    duration: float,
    output_path: str,
    fade_in: float = 2.0,
    fade_out: float = 2.0,
) -> str:
    """使用 ffmpeg 裁切音频并添加淡入淡出。"""
    if duration <= 0:
        raise ValueError("duration 必须大于 0")

    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"输入 BGM 不存在: {input_path}")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    safe_fade_in = min(max(fade_in, 0.0), duration / 2)
    safe_fade_out = min(max(fade_out, 0.0), duration / 2)
    fade_out_start = max(duration - safe_fade_out, 0.0)
    afade = (
        f"afade=t=in:st=0:d={safe_fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={safe_fade_out:.3f}"
    )

    command = [
        conf.FFMPEG_BIN,
        "-y",
        "-i",
        str(input_file),
        "-t",
        f"{duration:.3f}",
        "-af",
        afade,
        "-c:a",
        "pcm_s16le",
        str(output_file),
    ]
    logger.info("裁切 BGM: input=%s, duration=%.3f, output=%s", input_file, duration, output_file)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("裁切 BGM 失败: %s", result.stderr.strip())
        raise RuntimeError(f"裁切 BGM 失败: {result.stderr.strip()}")
    return str(output_file)


def adjust_volume(input_path: str, output_path: str, volume_db: float) -> str:
    """使用 ffmpeg 调整音量（dB）。"""
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"输入音频不存在: {input_path}")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    command = [
        conf.FFMPEG_BIN,
        "-y",
        "-i",
        str(input_file),
        "-af",
        f"volume={volume_db}dB",
        "-c:a",
        "pcm_s16le",
        str(output_file),
    ]
    logger.info("调整 BGM 音量: input=%s, volume_db=%s, output=%s", input_file, volume_db, output_file)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("调整 BGM 音量失败: %s", result.stderr.strip())
        raise RuntimeError(f"调整 BGM 音量失败: {result.stderr.strip()}")
    return str(output_file)
