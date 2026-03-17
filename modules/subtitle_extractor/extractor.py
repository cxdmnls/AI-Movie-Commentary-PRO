from __future__ import annotations


import importlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

conf = importlib.import_module("conf")
from .utils import extract_audio

def _load_prompt(prompt_name: str) -> str:
    """从 prompts 目录读取指定模板。"""
    prompt_file = Path(conf.PROMPTS_DIR) / prompt_name
    return prompt_file.read_text(encoding="utf-8")

WHISPER_INITIAL_PROMPT = _load_prompt("whisper_initial.txt")

logger = logging.getLogger(__name__)


class SubtitleExtractor:
    """基于 faster-whisper 的字幕提取器。"""

    def __init__(self, workspace_dir: str):
        """初始化工作目录并加载 Whisper 模型。"""
        self.workspace_dir: Path = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir: Path = self.workspace_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)

        model_source = conf.WHISPER_MODEL_PATH
        if not Path(model_source).exists():
            logger.warning("模型路径不存在，将回退到模型尺寸: %s", conf.WHISPER_MODEL_SIZE)
            model_source = conf.WHISPER_MODEL_SIZE

        self.model: object | None = None
        if os.environ.get("PIPELINE_LOCAL_FALLBACK", "") == "1":
            logger.warning("启用 PIPELINE_LOCAL_FALLBACK，跳过 Whisper 模型加载。")
            return

        try:
            logger.info(
                "加载 Whisper 模型: source=%s, device=%s, compute_type=%s",
                model_source,
                conf.WHISPER_DEVICE,
                conf.WHISPER_COMPUTE_TYPE,
            )
            whisper_module = importlib.import_module("faster_whisper")
            whisper_model_cls = getattr(whisper_module, "WhisperModel")
            self.model = whisper_model_cls(
                model_source,
                device=conf.WHISPER_DEVICE,
                compute_type=conf.WHISPER_COMPUTE_TYPE,
            )
        except (ModuleNotFoundError, RuntimeError, OSError, ValueError) as error:
            logger.warning("Whisper 初始化失败，使用降级字幕策略: %s", error)

    def extract(self, video_path: str) -> list[dict[str, float | str]]:
        """执行音频提取与转录，返回字幕列表。"""
        video_file = Path(video_path)
        if not video_file.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        audio_path = self.audio_dir / f"{video_file.stem}.wav"
        _ = extract_audio(str(video_file), str(audio_path))

        if self.model is None:
            return self._fallback_subtitles(video_file)

        logger.info("开始转录音频: %s", audio_path)
        transcribe_callable = getattr(self.model, "transcribe")
        try:
            segments, _info = transcribe_callable(
                str(audio_path),
                language=conf.WHISPER_LANGUAGE,
                beam_size=conf.WHISPER_BEAM_SIZE,
                initial_prompt=WHISPER_INITIAL_PROMPT,
                vad_filter=conf.WHISPER_VAD_FILTER,
            )
        except (RuntimeError, ValueError) as error:
            logger.warning("Whisper 转录失败，回退到降级字幕策略: %s", error)
            return self._fallback_subtitles(video_file)

        subtitles: list[dict[str, float | str]] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            subtitles.append(
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text,
                }
            )

        logger.info("转录完成，共生成 %d 条字幕", len(subtitles))
        return subtitles

    def _fallback_subtitles(self, video_file: Path) -> list[dict[str, float | str]]:
        """在 Whisper 不可用时，按时间片生成占位字幕。"""
        duration = self._get_video_duration(str(video_file))
        chunk = 20.0
        subtitles: list[dict[str, float | str]] = []
        start = 0.0
        index = 1
        while start < duration:
            end = min(start + chunk, duration)
            subtitles.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": f"第{index}段对白（自动降级字幕）",
                }
            )
            start = end
            index += 1
        logger.info("降级字幕生成完成，共 %d 条", len(subtitles))
        return subtitles

    def _get_video_duration(self, video_path: str) -> float:
        """通过 ffprobe 获取视频时长。"""
        ffprobe_cmd = conf.FFPROBE_BIN if shutil.which(conf.FFPROBE_BIN) else None
        if ffprobe_cmd:
            command = [
                ffprobe_cmd,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            result = subprocess.run(command, capture_output=True, text=False, check=False)
            if result.returncode == 0:
                output = result.stdout.decode("utf-8", errors="ignore").strip()
                if output:
                    return float(output)

        fallback_command = [
            conf.FFMPEG_BIN,
            "-i",
            video_path,
        ]
        fallback_result = subprocess.run(fallback_command, capture_output=True, text=False, check=False)
        stderr_text = fallback_result.stderr.decode("utf-8", errors="ignore")
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text)
        if not match:
            raise RuntimeError("无法获取视频时长（ffprobe/ffmpeg 均失败）")
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    def save(self, subtitles: list[dict[str, float | str]], output_path: str) -> None:
        """将字幕列表保存为 JSON 文件。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(subtitles, file, ensure_ascii=False, indent=2)
        logger.info("字幕已保存: %s", output_file)
