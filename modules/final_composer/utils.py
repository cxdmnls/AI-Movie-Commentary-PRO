"""最终合成模块工具函数。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import conf

logger = logging.getLogger(__name__)


def create_concat_file(file_list: list[str], output_path: str) -> str:
    """生成 ffmpeg concat demuxer 所需的文件列表。"""
    if not file_list:
        raise ValueError("file_list 不能为空")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for file_path in file_list:
        safe_path = str(Path(file_path).resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("生成 concat 列表文件: %s", output_file)
    return str(output_file)


def mix_audio_tracks(tracks: list[dict], output_path: str) -> str:
    """使用 ffmpeg 混合多条音轨。"""
    if not tracks:
        raise ValueError("tracks 不能为空")

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    command = [conf.FFMPEG_BIN, "-y"]
    filter_parts: list[str] = []
    input_labels: list[str] = []

    for index, track in enumerate(tracks):
        path = track.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"tracks[{index}] 缺少有效 path")
        if not Path(path).exists():
            raise FileNotFoundError(f"音轨文件不存在: {path}")

        volume = float(track.get("volume", 1.0))
        command.extend(["-i", path])
        filter_parts.append(f"[{index}:a]volume={volume:.4f}[a{index}]")
        input_labels.append(f"[a{index}]")

    filter_parts.append(
        "".join(input_labels)
        + f"amix=inputs={len(tracks)}:duration=longest:dropout_transition=2[mixout]"
    )

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[mixout]",
            "-c:a",
            conf.OUTPUT_AUDIO_CODEC,
            "-b:a",
            conf.OUTPUT_AUDIO_BITRATE,
            str(output_file),
        ]
    )

    logger.info("混合音轨: tracks=%d, output=%s", len(tracks), output_file)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("音轨混合失败: %s", result.stderr.strip())
        raise RuntimeError(f"音轨混合失败: {result.stderr.strip()}")
    return str(output_file)
