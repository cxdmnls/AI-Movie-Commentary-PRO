"""TTS 合成模块工具函数。"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

import conf

logger = logging.getLogger(__name__)


def get_audio_duration(audio_path: str) -> float:
    """使用 ffprobe 获取音频时长（秒）。"""
    file_path = Path(audio_path)
    if not file_path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    command = [
        conf.FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffprobe 获取音频时长失败: %s", result.stderr.strip())
        raise RuntimeError(f"获取音频时长失败: {result.stderr.strip()}")

    output = result.stdout.strip()
    try:
        return float(output)
    except ValueError as exc:
        raise RuntimeError(f"ffprobe 输出无法解析为时长: {output}") from exc


def estimate_speech_duration(text: str, words_per_minute: int = 350) -> float:
    """根据字数估算语音时长（秒）。"""
    if words_per_minute <= 0:
        raise ValueError("words_per_minute 必须大于 0")

    clean_text = "".join(text.split())
    char_count = len(clean_text)
    if char_count == 0:
        return 0.0
    return char_count / words_per_minute * 60.0


def _build_atempo_filters(speed: float) -> str:
    """将任意倍速转换为 ffmpeg atempo 过滤器链。"""
    if speed <= 0:
        raise ValueError("speed 必须大于 0")

    factors: list[float] = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5

    factors.append(remaining)
    normalized = [max(0.5, min(2.0, value)) for value in factors]
    return ",".join(f"atempo={value:.5f}" for value in normalized)


def adjust_audio_speed(input_path: str, output_path: str, speed: float) -> str:
    """使用 ffmpeg 调整音频速度。"""
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"输入音频不存在: {input_path}")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if math.isclose(speed, 1.0, rel_tol=1e-4, abs_tol=1e-4):
        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-i",
            str(input_file),
            "-c:a",
            "pcm_s16le",
            str(output_file),
        ]
    else:
        atempo_filter = _build_atempo_filters(speed)
        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-i",
            str(input_file),
            "-filter:a",
            atempo_filter,
            "-c:a",
            "pcm_s16le",
            str(output_file),
        ]

    logger.info("调整音频速度: input=%s, speed=%.3f, output=%s", input_file, speed, output_file)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg 调整音频速度失败: %s", result.stderr.strip())
        raise RuntimeError(f"调整音频速度失败: {result.stderr.strip()}")
    return str(output_file)
