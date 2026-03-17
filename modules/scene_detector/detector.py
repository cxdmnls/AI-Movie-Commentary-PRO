from __future__ import annotations


import importlib
import json
import logging
import os
import subprocess
from pathlib import Path

conf = importlib.import_module("conf")
from .utils import extract_thumbnail

logger = logging.getLogger(__name__)


class SceneDetector:
    """基于 PySceneDetect 的场景检测器。"""

    def __init__(self, workspace_dir: str):
        """初始化工作目录及缩略图目录。"""
        self.workspace_dir: Path = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir: Path = self.workspace_dir / "scenes_thumbnails"
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

    def detect(self, video_path: str) -> list[dict[str, float | int | str]]:
        """执行场景检测并生成每个场景的中间帧缩略图。"""
        video_file = Path(video_path)
        if not video_file.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        logger.info(
            "开始场景检测: threshold=%s, min_scene_len=%s",
            conf.SCENE_THRESHOLD,
            conf.SCENE_MIN_DURATION_SEC,
        )

        scene_boundaries: list[tuple[object, object]] = []
        duration = self._get_video_duration(str(video_file))
        if os.environ.get("PIPELINE_LOCAL_FALLBACK", "") == "1":
            logger.warning("启用 PIPELINE_LOCAL_FALLBACK，使用固定场景切分。")
            scene_boundaries = self._fallback_boundaries(duration)
        else:
            try:
                scenedetect_module = importlib.import_module("scenedetect")
                open_video = getattr(scenedetect_module, "open_video")
                SceneManager = getattr(scenedetect_module, "SceneManager")
                ContentDetector = getattr(scenedetect_module, "ContentDetector")

                video = open_video(str(video_file))
                frame_rate = video.frame_rate
                min_scene_len_frames = int(conf.SCENE_MIN_DURATION_SEC * frame_rate)

                scene_manager = SceneManager()
                scene_manager.add_detector(
                    ContentDetector(
                        threshold=conf.SCENE_THRESHOLD,
                        min_scene_len=min_scene_len_frames,
                    )
                )
                scene_manager.detect_scenes(video)
                scene_boundaries = scene_manager.get_scene_list()
            except (ModuleNotFoundError, RuntimeError, AttributeError, ValueError) as error:
                logger.warning("PySceneDetect 不可用，使用固定分段策略: %s", error)
                scene_boundaries = self._fallback_boundaries(duration)

        scene_boundaries = self._limit_boundaries(scene_boundaries, max_count=40)

        if not scene_boundaries:
            logger.warning("未检测到场景切换，使用整段视频作为单场景")
            duration = duration or 7.3
            scene_boundaries = [(0.0, duration)]

        scenes: list[dict[str, float | int | str]] = []
        for index, boundary in enumerate(scene_boundaries, start=1):
            start_sec = self._to_seconds(boundary[0])
            end_sec = self._to_seconds(boundary[1])

            if end_sec <= start_sec:
                logger.warning("跳过无效场景: scene_id=%d start=%s end=%s", index, start_sec, end_sec)
                continue

            middle_ts = start_sec + (end_sec - start_sec) / 2.0
            thumb_name = f"{index:03d}.jpg"
            thumb_abs = self.thumbnail_dir / thumb_name
            extract_thumbnail(
                str(video_file),
                middle_ts,
                str(thumb_abs),
                width=conf.SCENE_THUMBNAIL_WIDTH,
            )

            scenes.append(
                {
                    "scene_id": index,
                    "start": start_sec,
                    "end": end_sec,
                    "thumbnail": f"scenes_thumbnails/{thumb_name}",
                }
            )

        logger.info("场景检测完成，共 %d 个场景", len(scenes))
        return scenes

    def _fallback_boundaries(self, duration: float) -> list[tuple[float, float]]:
        """按固定时长切分场景，作为降级方案。"""
        if duration <= 0:
            return [(0.0, 8.0)]

        chunk = 20.0
        boundaries: list[tuple[float, float]] = []
        start = 0.0
        while start < duration:
            end = min(start + chunk, duration)
            boundaries.append((start, end))
            start = end
        return boundaries

    def _limit_boundaries(self, boundaries: list[tuple[object, object]], max_count: int) -> list[tuple[object, object]]:
        """限制场景数量，避免后续流程过慢。"""
        if len(boundaries) <= max_count:
            return boundaries
        step = len(boundaries) / max_count
        selected: list[tuple[object, object]] = []
        cursor = 0.0
        while int(cursor) < len(boundaries) and len(selected) < max_count:
            selected.append(boundaries[int(cursor)])
            cursor += step
        return selected

    def save(self, scenes: list[dict[str, float | int | str]], output_path: str) -> None:
        """保存场景检测结果为 JSON 文件。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(scenes, file, ensure_ascii=False, indent=2)
        logger.info("场景结果已保存: %s", output_file)

    def _to_seconds(self, value: object) -> float:
        """将帧时间对象转换为秒数。"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as error:
                raise ValueError(f"无法解析秒值: {value}") from error

        method = getattr(value, "get_seconds", None)
        if callable(method):
            seconds = method()
            if isinstance(seconds, (int, float)):
                return float(seconds)
            if isinstance(seconds, str):
                try:
                    return float(seconds)
                except ValueError as error:
                    raise ValueError(f"无法解析秒值: {seconds}") from error

        raise TypeError(f"不支持的时间对象类型: {type(value)!r}")

    def _get_video_duration(self, video_path: str) -> float:
        """通过 ffprobe 读取视频时长（秒）。"""
        command = [
            conf.FFPROBE_BIN,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe 获取视频时长失败: {result.stderr.strip()}")

        output = result.stdout.strip()
        if not output:
            raise ValueError("ffprobe 未返回有效视频时长")
        return float(output)
