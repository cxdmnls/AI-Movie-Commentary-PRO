"""TTS 合成主流程实现。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import conf

from .utils import adjust_audio_speed, estimate_speech_duration, get_audio_duration

logger = logging.getLogger(__name__)


class TTSSynthesizer:
    """基于 CosyVoice2 的语音合成器。"""

    def __init__(self):
        """初始化并加载 CosyVoice2 模型。"""
        self.model: Any | None = None
        self._model_error: Exception | None = None

        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

        local_cosyvoice_repo = Path(r"E:\Develop\third_party\CosyVoice")
        if local_cosyvoice_repo.exists() and str(local_cosyvoice_repo) not in sys.path:
            sys.path.insert(0, str(local_cosyvoice_repo))

        local_matcha_repo = local_cosyvoice_repo / "third_party" / "Matcha-TTS"
        if local_matcha_repo.exists() and str(local_matcha_repo) not in sys.path:
            sys.path.insert(0, str(local_matcha_repo))

        try:
            from cosyvoice.cli.cosyvoice import CosyVoice, CosyVoice2
            import cosyvoice.cli.frontend as cosy_frontend
            import cosyvoice.utils.file_utils as cosy_file_utils
        except ImportError as exc:
            self._model_error = exc
            logger.warning(
                "未检测到 CosyVoice 依赖，请先按 requirements 注释安装官方仓库版本。"
            )
            return

        try:
            import librosa
            import numpy as np
            import soundfile as sf
            import torch

            def _safe_load_wav(wav: str, target_sr: int, min_sr: int = 16000):
                speech, sample_rate = sf.read(wav, dtype="float32", always_2d=False)
                if isinstance(speech, np.ndarray) and speech.ndim > 1:
                    speech = speech.mean(axis=1)
                if not isinstance(speech, np.ndarray):
                    speech = np.asarray(speech, dtype=np.float32)
                if sample_rate != target_sr:
                    assert sample_rate >= min_sr, (
                        f"wav sample rate {sample_rate} must be greater than {min_sr}"
                    )
                    speech = librosa.resample(speech, orig_sr=sample_rate, target_sr=target_sr)
                tensor = torch.tensor(speech, dtype=torch.float32).unsqueeze(0)
                return tensor

            cosy_file_utils.load_wav = _safe_load_wav
            cosy_frontend.load_wav = _safe_load_wav
        except Exception as exc:
            logger.warning("替换 CosyVoice load_wav 失败，继续使用默认实现: %s", exc)

        model_path = Path(conf.COSYVOICE_MODEL_PATH)
        if not model_path.exists():
            logger.warning("CosyVoice 模型路径不存在: %s", model_path)

        logger.info("加载 CosyVoice 模型: path=%s, device=%s", model_path, conf.TTS_DEVICE)
        try:
            if (model_path / "cosyvoice2.yaml").exists():
                self.model = CosyVoice2(str(model_path))
            else:
                self.model = CosyVoice(str(model_path))
        except (RuntimeError, OSError, ValueError) as exc:
            self._model_error = exc
            logger.error("CosyVoice 模型加载失败: %s", exc)

    def synthesize(self, text: str, voice_ref: str, output_path: str, speed: float | None = None) -> str:
        """合成单段语音并返回输出文件路径。"""
        if not text.strip():
            raise ValueError("text 不能为空")
        voice_ref_path = Path(voice_ref)
        if not voice_ref_path.exists():
            raise FileNotFoundError(f"参考音频不存在: {voice_ref}")
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        target_speed = speed if speed is not None else conf.TTS_DEFAULT_SPEED

        if self.model is None:
            logger.warning("CosyVoice 不可用，生成降级占位音频: %s", output_file)
            expected = estimate_speech_duration(text, conf.WORDS_PER_MINUTE)
            duration = max(1.0, expected / max(0.5, target_speed))
            return self._generate_placeholder_audio(str(output_file), duration)

        logger.info("开始 TTS 合成: output=%s, speed=%.3f", output_file, target_speed)
        try:
            if hasattr(self.model, "inference_cross_lingual"):
                generation = self.model.inference_cross_lingual(
                    text,
                    str(voice_ref_path),
                    stream=False,
                    speed=target_speed,
                )
            else:
                generation = self.model.inference_zero_shot(
                    text,
                    "",
                    str(voice_ref_path),
                    stream=False,
                    speed=target_speed,
                )
        except TypeError:
            if hasattr(self.model, "inference_cross_lingual"):
                generation = self.model.inference_cross_lingual(text, str(voice_ref_path))
            else:
                generation = self.model.inference_zero_shot(text, "", str(voice_ref_path))
        except (RuntimeError, ValueError) as exc:
            logger.error("CosyVoice 推理失败: %s", exc)
            raise RuntimeError(f"TTS 推理失败: {exc}") from exc

        if not isinstance(generation, (str, dict, list, tuple)) and hasattr(generation, "__iter__"):
            generation = list(generation)

        raw_audio_path = self._save_generated_audio(generation, output_file)
        if abs(target_speed - 1.0) > 1e-4:
            adjusted_path = output_file.with_name(f"{output_file.stem}_speed{output_file.suffix or '.wav'}")
            adjust_audio_speed(str(raw_audio_path), str(adjusted_path), target_speed)
            try:
                raw_audio_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("删除中间音频失败: %s", exc)
            return str(adjusted_path)
        return str(raw_audio_path)

    def _generate_placeholder_audio(self, output_path: str, duration: float) -> str:
        """在无 TTS 模型时生成可用占位音频。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        safe_duration = max(1.0, float(duration))

        command = [
            conf.FFMPEG_BIN,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={safe_duration:.3f}",
            "-ar",
            str(conf.TTS_SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_file),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"生成占位音频失败: {result.stderr.strip()}")
        return str(output_file)

    def synthesize_segments(self, segments: list[dict], voice_ref: str, output_dir: str) -> list[dict]:
        """批量合成所有 segment 的语音并返回更新后的 segments。"""
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        updated_segments: list[dict] = []

        for index, segment in enumerate(segments):
            segment_data = dict(segment)
            text = self._extract_segment_text(segment_data)
            if not text:
                logger.warning("segment[%d] 缺少可合成文本，跳过。", index)
                updated_segments.append(segment_data)
                continue

            expected_duration = self._extract_segment_duration(segment_data)
            speed = conf.TTS_DEFAULT_SPEED
            if expected_duration > 0:
                estimated_duration = estimate_speech_duration(text, conf.WORDS_PER_MINUTE)
                if estimated_duration > 0:
                    speed = max(0.5, min(2.0, estimated_duration / expected_duration))

            output_path = output_root / f"segment_{index:04d}.wav"
            tts_path = self.synthesize(text=text, voice_ref=voice_ref, output_path=str(output_path), speed=speed)
            segment_data["tts_audio_path"] = tts_path

            if expected_duration > 0:
                segment_data["tts_duration_valid"] = self._validate_duration(
                    tts_path,
                    expected_duration,
                    tolerance=0.2,
                )
            updated_segments.append(segment_data)
        return updated_segments

    def _validate_duration(self, audio_path: str, expected_duration: float, tolerance: float = 0.2) -> bool:
        """校验音频时长是否与预期时长匹配。"""
        if expected_duration <= 0:
            return True
        actual_duration = get_audio_duration(audio_path)
        deviation_ratio = abs(actual_duration - expected_duration) / expected_duration
        is_valid = deviation_ratio <= tolerance
        if not is_valid:
            logger.warning(
                "TTS 时长不匹配: audio=%s, expected=%.3f, actual=%.3f, tolerance=%.2f",
                audio_path,
                expected_duration,
                actual_duration,
                tolerance,
            )
        return is_valid

    @staticmethod
    def _extract_segment_text(segment: dict) -> str:
        """提取 segment 中的可合成文本。"""
        for key in ("narration_text", "narration", "text", "script"):
            value = segment.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _extract_segment_duration(segment: dict) -> float:
        """提取 segment 时长信息。"""
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

    @staticmethod
    def _save_generated_audio(generation: Any, output_path: Path) -> Path:
        """将 CosyVoice 生成结果保存为音频文件。"""
        if generation is None:
            raise RuntimeError("CosyVoice 未返回任何音频结果")

        path_candidate = TTSSynthesizer._find_audio_path(generation)
        if path_candidate is not None:
            source_file = Path(path_candidate)
            if not source_file.exists():
                raise RuntimeError(f"推理返回音频路径不存在: {source_file}")
            output_path.write_bytes(source_file.read_bytes())
            return output_path

        waveform = TTSSynthesizer._find_waveform(generation)
        if waveform is None:
            raise RuntimeError("无法从 CosyVoice 推理结果解析音频数据")

        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("保存波形需要 torch 依赖") from exc

        tensor = waveform
        if not isinstance(tensor, torch.Tensor):
            tensor = torch.tensor(tensor, dtype=torch.float32)
        if tensor.dim() == 1:
            tensor = tensor.unsqueeze(0)

        tensor = tensor.cpu()

        try:
            import soundfile as sf

            np_audio = tensor.squeeze(0).numpy()
            sf.write(str(output_path), np_audio, conf.TTS_SAMPLE_RATE)
            return output_path
        except Exception:
            pass

        try:
            import torchaudio

            torchaudio.save(str(output_path), tensor, conf.TTS_SAMPLE_RATE)
        except Exception as exc:
            raise RuntimeError(f"保存波形失败: {exc}") from exc
        return output_path

    @staticmethod
    def _find_audio_path(payload: Any) -> str | None:
        """在推理返回对象中查找已有音频文件路径。"""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("audio_path", "wav_path", "path"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            for value in payload.values():
                nested = TTSSynthesizer._find_audio_path(value)
                if nested is not None:
                    return nested
        if isinstance(payload, (list, tuple)):
            for item in payload:
                nested = TTSSynthesizer._find_audio_path(item)
                if nested is not None:
                    return nested
        return None

    @staticmethod
    def _find_waveform(payload: Any) -> Any | None:
        """在推理返回对象中查找音频波形。"""
        if payload is None:
            return None
        if hasattr(payload, "shape") and hasattr(payload, "dtype"):
            return payload
        if isinstance(payload, dict):
            for key in ("audio", "wav", "waveform", "tts_speech"):
                if key in payload:
                    nested = TTSSynthesizer._find_waveform(payload[key])
                    if nested is not None:
                        return nested
            for value in payload.values():
                nested = TTSSynthesizer._find_waveform(value)
                if nested is not None:
                    return nested
        if isinstance(payload, (list, tuple)):
            for item in payload:
                nested = TTSSynthesizer._find_waveform(item)
                if nested is not None:
                    return nested
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("用法: python -m modules.tts_synthesizer.synthesizer <文本> <参考音频路径> <输出文件路径>")
        sys.exit(1)
    
    text = sys.argv[1]
    voice_ref = sys.argv[2]
    output_path = sys.argv[3]
    
    synthesizer = TTSSynthesizer()
    result = synthesizer.synthesize(text, voice_ref, output_path)
    
    print(f"TTS 合成完成")
    print(f"输出文件: {result}")