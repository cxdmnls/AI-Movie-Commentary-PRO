"""视频裁切主流程实现。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import conf

from .utils import get_video_duration

logger = logging.getLogger(__name__)


class VideoEditor:
    """基于 ffmpeg 的视频裁切器。"""

    def __init__(self):
        """初始化视频编辑器。"""

    def cut_segment(
        self,
        video_path: str,
        start: float,
        end: float,
        output_path: str,
        remove_audio: bool = False,
    ) -> str:
        """裁切单个视频片段并返回输出路径。"""
        if end <= start:
            raise ValueError("end 必须大于 start")

        source_file = Path(video_path)
        if not source_file.exists():
            raise FileNotFoundError(f"输入视频不存在: {video_path}")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        clip_duration = end - start

        temp_fast_clip = output_file.with_name(f"{output_file.stem}_fast{output_file.suffix}")
        fast_command = [
            conf.FFMPEG_BIN,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source_file),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(temp_fast_clip),
        ]

        logger.info("尝试快速裁切: input=%s, start=%.3f, end=%.3f", source_file, start, end)
        fast_result = subprocess.run(fast_command, capture_output=True, text=True, check=False)
        encode_source = source_file
        need_precise_cut = True
        if fast_result.returncode == 0 and temp_fast_clip.exists():
            encode_source = temp_fast_clip
            need_precise_cut = False
        else:
            logger.warning("快速裁切失败，回退到精确重编码: %s", fast_result.stderr.strip())

        vf_filter = f"scale={conf.OUTPUT_RESOLUTION},fps={conf.OUTPUT_FPS}"
        command = [conf.FFMPEG_BIN, "-y"]
        if need_precise_cut:
            command.extend(["-ss", f"{start:.3f}", "-to", f"{end:.3f}"])
        command.extend(["-i", str(encode_source), "-vf", vf_filter])

        if remove_audio or conf.ORIGINAL_AUDIO_VOLUME <= 0:
            command.append("-an")
        else:
            command.extend(["-af", f"volume={conf.ORIGINAL_AUDIO_VOLUME}"])
            command.extend(["-c:a", conf.OUTPUT_AUDIO_CODEC, "-b:a", conf.OUTPUT_AUDIO_BITRATE])

        command.extend(
            [
                "-c:v",
                conf.OUTPUT_VIDEO_CODEC,
                "-preset",
                conf.OUTPUT_PRESET,
                "-crf",
                str(conf.OUTPUT_CRF),
                "-t",
                f"{clip_duration:.3f}",
                str(output_file),
            ]
        )

        logger.info("开始输出视频片段: %s", output_file)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error("视频裁切失败: %s", result.stderr.strip())
            raise RuntimeError(f"视频裁切失败: {result.stderr.strip()}")

        try:
            temp_fast_clip.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除快速裁切中间文件失败: %s", exc)

        return str(output_file)

    def cut_segments(self, video_path: str, segments: list[dict], output_dir: str) -> list[dict]:
        """批量裁切所有 segment 并返回更新后的 segments。"""
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        results: list[dict] = []
        for index, segment in enumerate(segments):
            segment_data = dict(segment)
            video_clip = segment_data.get("video_clip")
            if isinstance(video_clip, dict):
                start = float(video_clip.get("start", 0.0))
                end = float(video_clip.get("end", 0.0))
            else:
                start = float(segment_data.get("start", 0.0))
                end = float(segment_data.get("end", 0.0))
            if end <= start:
                logger.warning("segment[%d] 时间范围无效，跳过裁切。", index)
                results.append(segment_data)
                continue

            output_path = output_root / f"segment_{index:04d}.mp4"
            clip_path = self.cut_segment(
                video_path=video_path,
                start=start,
                end=end,
                output_path=str(output_path),
                remove_audio=bool(segment_data.get("remove_original_audio", False)),
            )
            segment_data["video_clip_path"] = clip_path
            segment_data["video_clip_duration"] = get_video_duration(clip_path)
            results.append(segment_data)
        return results

    def add_transition(
        self,
        clip1_path: str,
        clip2_path: str,
        output_path: str,
        duration: float = 0.5,
    ) -> str:
        """在两个视频片段之间添加过渡效果。"""
        if duration <= 0:
            raise ValueError("duration 必须大于 0")

        first = Path(clip1_path)
        second = Path(clip2_path)
        if not first.exists():
            raise FileNotFoundError(f"第一个片段不存在: {clip1_path}")
        if not second.exists():
            raise FileNotFoundError(f"第二个片段不存在: {clip2_path}")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        clip1_duration = get_video_duration(str(first))
        offset = max(clip1_duration - duration, 0.0)
        filter_complex = (
            f"[0:v][1:v]xfade=transition=fade:duration={duration:.3f}:offset={offset:.3f}[v];"
            f"[0:a][1:a]acrossfade=d={duration:.3f}[a]"
        )

        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-i",
            str(first),
            "-i",
            str(second),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
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
            str(output_file),
        ]

        logger.info("添加过渡效果: %s + %s -> %s", first, second, output_file)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error("添加过渡失败: %s", result.stderr.strip())
            raise RuntimeError(f"添加过渡失败: {result.stderr.strip()}")
        return str(output_file)
