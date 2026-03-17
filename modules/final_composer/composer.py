"""最终视频合成主流程实现。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import conf

from .utils import create_concat_file, mix_audio_tracks

logger = logging.getLogger(__name__)


class FinalComposer:
    """负责 segment 合成与最终拼接。"""

    def __init__(self):
        """初始化最终合成器。"""

    def compose_segment(self, segment: dict, output_path: str) -> str:
        """合成单个 segment（视频 + TTS + 可选 BGM）。"""
        video_clip_path = segment.get("video_clip_path")
        if not isinstance(video_clip_path, str) or not video_clip_path:
            raise ValueError("segment 缺少 video_clip_path")
        if not Path(video_clip_path).exists():
            raise FileNotFoundError(f"视频片段不存在: {video_clip_path}")

        tts_audio_path = segment.get("tts_audio_path")
        if not isinstance(tts_audio_path, str) or not tts_audio_path:
            raise ValueError("segment 缺少 tts_audio_path")
        if not Path(tts_audio_path).exists():
            raise FileNotFoundError(f"TTS 音频不存在: {tts_audio_path}")

        tracks: list[dict] = [{"path": tts_audio_path, "volume": 1.0}]
        bgm_clip_path = segment.get("bgm_clip_path")
        if isinstance(bgm_clip_path, str) and bgm_clip_path and Path(bgm_clip_path).exists():
            tracks.append({"path": bgm_clip_path, "volume": 1.0})

        return self._merge_audio_video(video_clip_path, tracks, output_path)

    def compose_all(self, segments: list[dict], output_path: str) -> str:
        """合成全部 segment 并拼接为最终视频。"""
        if not segments:
            raise ValueError("segments 不能为空")

        final_output = Path(output_path)
        final_output.parent.mkdir(parents=True, exist_ok=True)
        segment_dir = final_output.parent / "_segment_outputs"
        segment_dir.mkdir(parents=True, exist_ok=True)

        composed_files: list[str] = []
        for index, segment in enumerate(segments):
            if bool(segment.get("skip", False)):
                logger.info("跳过 segment[%d]（用户审核标记）", index)
                continue

            segment_output = segment_dir / f"segment_{index:04d}.mp4"
            composed_files.append(self.compose_segment(segment, str(segment_output)))

        if not composed_files:
            raise RuntimeError("没有可拼接的片段")

        concat_list_file = segment_dir / "concat_list.txt"
        create_concat_file(composed_files, str(concat_list_file))

        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file),
            "-vf",
            f"scale={conf.OUTPUT_RESOLUTION},fps={conf.OUTPUT_FPS}",
            "-c:v",
            conf.OUTPUT_VIDEO_CODEC,
            "-preset",
            conf.OUTPUT_PRESET,
            "-crf",
            str(conf.OUTPUT_CRF),
            "-c:a",
            conf.OUTPUT_AUDIO_CODEC,
            "-b:a",
            conf.OUTPUT_AUDIO_BITRATE,
            str(final_output),
        ]
        logger.info("拼接最终视频: segments=%d, output=%s", len(composed_files), final_output)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error("最终拼接失败: %s", result.stderr.strip())
            raise RuntimeError(f"最终拼接失败: {result.stderr.strip()}")
        return str(final_output)

    def _merge_audio_video(self, video_path: str, audio_paths: list[dict], output_path: str) -> str:
        """混合音轨并与视频合并。"""
        video_file = Path(video_path)
        if not video_file.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        mixed_audio = output_file.with_name(f"{output_file.stem}_mix.m4a")
        mix_audio_tracks(audio_paths, str(mixed_audio))

        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-i",
            str(video_file),
            "-i",
            str(mixed_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            f"scale={conf.OUTPUT_RESOLUTION},fps={conf.OUTPUT_FPS}",
            "-c:v",
            conf.OUTPUT_VIDEO_CODEC,
            "-preset",
            conf.OUTPUT_PRESET,
            "-crf",
            str(conf.OUTPUT_CRF),
            "-c:a",
            conf.OUTPUT_AUDIO_CODEC,
            "-b:a",
            conf.OUTPUT_AUDIO_BITRATE,
            "-shortest",
            str(output_file),
        ]

        logger.info("合并音视频: video=%s, output=%s", video_file, output_file)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error("合并音视频失败: %s", result.stderr.strip())
            raise RuntimeError(f"合并音视频失败: {result.stderr.strip()}")

        try:
            mixed_audio.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除中间混音文件失败: %s", exc)
        return str(output_file)
