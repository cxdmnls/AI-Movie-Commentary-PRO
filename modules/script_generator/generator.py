from __future__ import annotations


import importlib
import json
import logging
from pathlib import Path

conf = importlib.import_module("conf")
from .exporters import ScriptExporter

logger = logging.getLogger(__name__)


class ScriptGenerator:
    """基于 Qwen 的三轮解说脚本生成器。"""

    def __init__(self):
        """初始化 DashScope 客户端。"""
        self.dashscope = None
        try:
            self.dashscope = importlib.import_module("dashscope")
            setattr(self.dashscope, "api_key", conf.DASHSCOPE_API_KEY)
        except ModuleNotFoundError:
            logger.warning("未安装 dashscope，M4 将使用本地降级脚本生成。")

    def generate(
        self,
        subtitles: list[dict[str, object]],
        scenes: list[dict[str, object]],
        movie_info: dict[str, object],
    ) -> dict[str, object]:
        """执行场景筛选、解说生成与情感标注三轮流程。"""
        selected_scene_ids = self._select_scenes(scenes, movie_info)
        scene_index: dict[int, dict[str, object]] = {}
        for scene in scenes:
            scene_id_value = scene.get("scene_id")
            if isinstance(scene_id_value, int):
                scene_index[scene_id_value] = scene

        selected_scenes: list[dict[str, object]] = []
        for scene_id in selected_scene_ids:
            if scene_id in scene_index:
                selected_scenes.append(scene_index[scene_id])

        segments = self._generate_narration(selected_scenes, subtitles, movie_info)
        tagged_segments = self._tag_emotions(segments, movie_info)
        return {
            "total_duration_target": f"{int(conf.SCRIPT_TARGET_DURATION_MIN):02d}:00",
            "segments": tagged_segments,
        }

    def _select_scenes(self, scenes: list[dict[str, object]], movie_info: dict[str, object]) -> list[int]:
        """第一轮：筛选保留场景并返回 scene_id 列表。"""
        try:
            prompt = self._load_prompt("scene_selection.txt")
            message_text = prompt.format(
                scenes_json=json.dumps(scenes, ensure_ascii=False, indent=2),
                movie_info_json=json.dumps(movie_info, ensure_ascii=False, indent=2),
                target_duration=f"{int(conf.SCRIPT_TARGET_DURATION_MIN):02d}:00",
            )
            parsed = self._parse_json_response(self._call_llm([{"role": "user", "content": message_text}]))
        except (RuntimeError, FileNotFoundError, ValueError) as error:
            logger.warning("场景筛选调用失败，使用降级策略: %s", error)
            parsed = {}

        raw_items: list[object] = []
        if isinstance(parsed, dict):
            selected = parsed.get("selected_scene_ids")
            if isinstance(selected, list):
                raw_items = selected
        elif isinstance(parsed, list):
            raw_items = parsed

        selected_ids: list[int] = []
        for item in raw_items:
            if isinstance(item, int):
                selected_ids.append(item)
            elif isinstance(item, str) and item.isdigit():
                selected_ids.append(int(item))

        if selected_ids:
            return selected_ids

        fallback_ids: list[int] = []
        for scene in scenes:
            scene_id_value = scene.get("scene_id")
            if isinstance(scene_id_value, int):
                fallback_ids.append(scene_id_value)
        return fallback_ids

    def _generate_narration(
        self,
        selected_scenes: list[dict[str, object]],
        subtitles: list[dict[str, object]],
        movie_info: dict[str, object],
    ) -> list[dict[str, object]]:
        """第二轮：为选中场景生成结构化解说片段。"""
        try:
            prompt = self._load_prompt("narration_gen.txt")
            message_text = prompt.format(
                selected_scenes_json=json.dumps(selected_scenes, ensure_ascii=False, indent=2),
                subtitles_json=json.dumps(subtitles, ensure_ascii=False, indent=2),
                movie_info_json=json.dumps(movie_info, ensure_ascii=False, indent=2),
                target_words=conf.SCRIPT_TARGET_WORDS,
            )
            parsed = self._parse_json_response(self._call_llm([{"role": "user", "content": message_text}]))
        except (RuntimeError, FileNotFoundError, ValueError) as error:
            logger.warning("解说生成调用失败，使用降级策略: %s", error)
            return self._fallback_segments(selected_scenes, subtitles, movie_info)

        raw_segments: list[object] = []
        if isinstance(parsed, dict):
            segments_value = parsed.get("segments")
            if isinstance(segments_value, list):
                raw_segments = segments_value
        elif isinstance(parsed, list):
            raw_segments = parsed

        normalized: list[dict[str, object]] = []
        for index, raw_segment in enumerate(raw_segments, start=1):
            if not isinstance(raw_segment, dict):
                continue

            scene_ids = self._normalize_scene_ids(raw_segment.get("scene_ids"))
            clip = self._normalize_video_clip(raw_segment.get("video_clip"))
            narration_text = raw_segment.get("narration_text")
            emotion = raw_segment.get("emotion")
            is_climax = raw_segment.get("is_climax")
            bgm_required = raw_segment.get("bgm_required")
            segment_id_value = raw_segment.get("segment_id")

            segment_id = index
            if isinstance(segment_id_value, int):
                segment_id = segment_id_value
            elif isinstance(segment_id_value, str) and segment_id_value.isdigit():
                segment_id = int(segment_id_value)

            normalized.append(
                {
                    "segment_id": segment_id,
                    "scene_ids": scene_ids,
                    "video_clip": clip,
                    "narration_text": str(narration_text or "").strip(),
                    "emotion": str(emotion or "平静"),
                    "is_climax": bool(is_climax),
                    "bgm_required": bool(bgm_required),
                }
            )

        if normalized:
            return normalized
        return self._fallback_segments(selected_scenes, subtitles, movie_info)

    def _tag_emotions(
        self,
        segments: list[dict[str, object]],
        movie_info: dict[str, object],
    ) -> list[dict[str, object]]:
        """第三轮：为片段补充或修正情感标签。"""
        try:
            prompt = self._load_prompt("emotion_tagging.txt")
            message_text = prompt.format(
                segments_json=json.dumps(segments, ensure_ascii=False, indent=2),
                emotion_types=json.dumps(conf.EMOTION_TYPES, ensure_ascii=False),
                movie_info_json=json.dumps(movie_info, ensure_ascii=False, indent=2),
            )
            parsed = self._parse_json_response(self._call_llm([{"role": "user", "content": message_text}]))
        except (RuntimeError, FileNotFoundError, ValueError) as error:
            logger.warning("情感标注调用失败，保持原始情感标签: %s", error)
            return segments

        emotion_map: dict[str, str] = {}
        if isinstance(parsed, dict):
            raw_map = parsed.get("emotion_map")
            if isinstance(raw_map, dict):
                for key, value in raw_map.items():
                    if isinstance(key, str) and isinstance(value, str):
                        emotion_map[key] = value
        elif isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                segment_id = item.get("segment_id")
                emotion = item.get("emotion")
                if isinstance(segment_id, (int, str)) and isinstance(emotion, str):
                    emotion_map[str(segment_id)] = emotion

        for segment in segments:
            segment_id_value = segment.get("segment_id")
            key = str(segment_id_value)
            if key in emotion_map and emotion_map[key]:
                segment["emotion"] = emotion_map[key]
        return segments

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """统一调用 Qwen 接口并返回文本响应。"""
        if self.dashscope is None:
            raise RuntimeError("dashscope 不可用")

        response = self.dashscope.Generation.call(
            model=conf.QWEN_MODEL,
            api_key=conf.DASHSCOPE_API_KEY,
            messages=messages,
            max_tokens=conf.QWEN_MAX_TOKENS,
            temperature=conf.QWEN_TEMPERATURE,
            result_format="message",
        )

        if isinstance(response, dict):
            status_code = response.get("status_code")
            if isinstance(status_code, int) and status_code != 200:
                raise RuntimeError(f"Qwen 调用失败: status_code={status_code}")

            output = response.get("output")
            if isinstance(output, dict):
                choices = output.get("choices")
                if isinstance(choices, list) and choices:
                    first = choices[0]
                    if isinstance(first, dict):
                        message = first.get("message")
                        if isinstance(message, dict):
                            content = message.get("content")
                            if isinstance(content, str):
                                return content
                text = output.get("text")
                if isinstance(text, str):
                    return text

        response_output = getattr(response, "output", None)
        if response_output is not None:
            response_text = getattr(response_output, "text", None)
            if isinstance(response_text, str):
                return response_text
            choices = getattr(response_output, "choices", None)
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str):
                            return content

        raise RuntimeError("Qwen 返回格式无法解析")

    def _load_prompt(self, prompt_name: str) -> str:
        """从 prompts 目录读取指定模板。"""
        prompt_file = Path(conf.PROMPTS_DIR) / prompt_name
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt 模板不存在: {prompt_file}")
        return prompt_file.read_text(encoding="utf-8")

    def save(self, script: dict[str, object], output_path: str) -> None:
        """将脚本结果保存为 JSON 文件。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(script, file, ensure_ascii=False, indent=2)

    def _parse_json_response(self, raw_text: str) -> dict[str, object] | list[object]:
        """从 LLM 文本中提取并解析 JSON。"""
        content = raw_text.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) >= 3:
                content = "\n".join(lines[1:-1]).strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            logger.warning("JSON 解析失败: %s", error)
            return {}

        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return parsed
        return {}

    def _fallback_segments(
        self,
        selected_scenes: list[dict[str, object]],
        subtitles: list[dict[str, object]],
        movie_info: dict[str, object],
    ) -> list[dict[str, object]]:
        """无 LLM 时生成可执行的最小脚本。"""
        title = str(movie_info.get("title") or "这部电影")
        synopsis = str(movie_info.get("synopsis") or "故事围绕主角的关键抉择展开。")

        chosen = selected_scenes[:8]
        if not chosen:
            return []

        subtitle_text = ""
        for item in subtitles[:6]:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    subtitle_text += text.strip() + " "
        subtitle_hint = subtitle_text.strip() or "画面推进节奏稳定，情绪逐步抬升。"

        emotions = list(conf.EMOTION_TYPES)
        result: list[dict[str, object]] = []
        for index, scene in enumerate(chosen, start=1):
            scene_id_value = scene.get("scene_id")
            start = self._to_float(scene.get("start"))
            end = self._to_float(scene.get("end"))
            if end <= start:
                continue

            emotion = emotions[(index - 1) % len(emotions)] if emotions else "平静"
            narration_text = (
                f"{title}第{index}段：{synopsis[:60]}。"
                f"当前片段聚焦人物行动与结果，{subtitle_hint[:50]}。"
            )

            scene_id = index
            if isinstance(scene_id_value, int):
                scene_id = scene_id_value

            result.append(
                {
                    "segment_id": index,
                    "scene_ids": [scene_id],
                    "video_clip": {"start": start, "end": end},
                    "narration_text": narration_text,
                    "emotion": emotion,
                    "is_climax": index == len(chosen),
                    "bgm_required": True,
                }
            )
        return result

    def _normalize_scene_ids(self, raw_scene_ids: object) -> list[int]:
        """将场景编号标准化为整数列表。"""
        if not isinstance(raw_scene_ids, list):
            return []

        result: list[int] = []
        for item in raw_scene_ids:
            if isinstance(item, int):
                result.append(item)
            elif isinstance(item, str) and item.isdigit():
                result.append(int(item))
        return result

    def _normalize_video_clip(self, raw_clip: object) -> dict[str, float]:
        """将片段裁剪区间标准化为起止秒数。"""
        if not isinstance(raw_clip, dict):
            return {"start": 0.0, "end": 0.0}

        start_value = raw_clip.get("start")
        end_value = raw_clip.get("end")
        start = self._to_float(start_value)
        end = self._to_float(end_value)
        return {"start": start, "end": end}

    def _to_float(self, value: object) -> float:
        """将输入值转换为浮点数。"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def export_readable_script(
        self, movie_info: dict[str, object], output_path: str
    ) -> str:
        """导出可读格式的剧本（Markdown）。"""
        exporter = ScriptExporter()
        return exporter.export_markdown(movie_info, output_path)
