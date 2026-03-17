"""BGM 匹配主流程实现。"""

from __future__ import annotations

import logging
import random
from pathlib import Path

import conf

from .utils import adjust_volume, list_audio_files, trim_audio

logger = logging.getLogger(__name__)


class BGMMatcher:
    """根据情感标签匹配并处理 BGM。"""

    def __init__(self):
        """初始化并扫描 BGM 曲库目录结构。"""
        self.library_root = Path(conf.BGM_LIBRARY_DIR)
        self.emotions = list(conf.EMOTION_TYPES)
        self.library: dict[str, list[str]] = {}

        for emotion in self.emotions:
            emotion_dir = self.library_root / emotion
            files = list_audio_files(str(emotion_dir))
            self.library[emotion] = files
            logger.info("扫描 BGM 目录: emotion=%s, files=%d", emotion, len(files))

    def is_library_empty(self) -> bool:
        """检查 BGM 曲库是否为空。"""
        return all(len(files) == 0 for files in self.library.values())

    def match(self, emotion: str) -> str | None:
        """根据情感标签随机匹配一首 BGM。"""
        candidates = self.library.get(emotion, [])
        if not candidates:
            logger.warning("未找到情感 %s 对应的 BGM", emotion)
            return None
        selected = random.choice(candidates)
        logger.info("匹配 BGM: emotion=%s, bgm=%s", emotion, selected)
        return selected

    def match_segments(self, segments: list[dict], output_dir: str) -> list[dict]:
        """为所有需要 BGM 的 segment 匹配并处理 BGM。"""
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        if self.is_library_empty() and conf.BGM_SKIP_IF_EMPTY:
            logger.warning("BGM 曲库为空，按配置跳过 BGM 匹配。")
            return [dict(segment) for segment in segments]

        results: list[dict] = []
        for index, segment in enumerate(segments):
            segment_data = dict(segment)

            needs_bgm = bool(segment_data.get("bgm_required", True))
            if not needs_bgm:
                segment_data["bgm_clip_path"] = None
                results.append(segment_data)
                continue

            duration = self._extract_duration(segment_data)
            if duration <= 0:
                logger.warning("segment[%d] 时长无效，跳过 BGM。", index)
                segment_data["bgm_clip_path"] = None
                results.append(segment_data)
                continue

            emotion = str(segment_data.get("emotion") or "").strip()
            bgm_source = self.match(emotion)
            if bgm_source is None:
                segment_data["bgm_clip_path"] = None
                results.append(segment_data)
                continue

            clipped_file = output_root / f"segment_{index:04d}_bgm_clip.wav"
            mixed_file = output_root / f"segment_{index:04d}_bgm.wav"
            trim_audio(
                input_path=bgm_source,
                duration=duration,
                output_path=str(clipped_file),
                fade_in=conf.BGM_FADE_DURATION_SEC,
                fade_out=conf.BGM_FADE_DURATION_SEC,
            )
            adjust_volume(str(clipped_file), str(mixed_file), conf.BGM_VOLUME_DB)

            try:
                clipped_file.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("删除 BGM 中间文件失败: %s", exc)

            segment_data["bgm_clip_path"] = str(mixed_file)
            results.append(segment_data)
        return results

    @staticmethod
    def _extract_duration(segment: dict) -> float:
        """提取 segment 的持续时长。"""
        duration = segment.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            return float(duration)

        video_clip = segment.get("video_clip")
        if isinstance(video_clip, dict):
            clip_start = video_clip.get("start")
            clip_end = video_clip.get("end")
            if isinstance(clip_start, (int, float)) and isinstance(clip_end, (int, float)) and clip_end > clip_start:
                return float(clip_end - clip_start)

        start = segment.get("start")
        end = segment.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            return float(end - start)
        return 0.0
