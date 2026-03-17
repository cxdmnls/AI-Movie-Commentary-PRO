"""视频编辑模块工具函数。"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import conf

logger = logging.getLogger(__name__)


def get_video_duration(video_path: str) -> float:
    """使用 ffprobe 获取视频时长。"""
    file_path = Path(video_path)
    if not file_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

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
        logger.error("ffprobe 获取视频时长失败: %s", result.stderr.strip())
        raise RuntimeError(f"获取视频时长失败: {result.stderr.strip()}")

    output = result.stdout.strip()
    try:
        return float(output)
    except ValueError as exc:
        raise RuntimeError(f"ffprobe 输出无法解析为时长: {output}") from exc


def get_video_info(video_path: str) -> dict[str, Any]:
    """获取视频信息（分辨率、fps、编码等）。"""
    file_path = Path(video_path)
    if not file_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    command = [
        conf.FFPROBE_BIN,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffprobe 获取视频信息失败: %s", result.stderr.strip())
        raise RuntimeError(f"获取视频信息失败: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe 输出 JSON 解析失败") from exc

    video_stream: dict[str, Any] | None = None
    audio_stream: dict[str, Any] | None = None
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        if codec_type == "audio" and audio_stream is None:
            audio_stream = stream

    return {
        "format": data.get("format", {}),
        "video": {
            "codec": (video_stream or {}).get("codec_name"),
            "width": (video_stream or {}).get("width"),
            "height": (video_stream or {}).get("height"),
            "fps": (video_stream or {}).get("r_frame_rate"),
            "pix_fmt": (video_stream or {}).get("pix_fmt"),
        },
        "audio": {
            "codec": (audio_stream or {}).get("codec_name"),
            "sample_rate": (audio_stream or {}).get("sample_rate"),
            "channels": (audio_stream or {}).get("channels"),
        },
    }
