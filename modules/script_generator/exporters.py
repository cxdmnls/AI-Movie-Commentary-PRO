"""剧本导出器 - 将 movie_info 导出为可读格式（基于 generate_readable_script.py）"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ScriptExporter:
    """将电影信息导出为各种可读格式。"""

    def export_markdown(self, movie_info: dict[str, Any], output_path: str) -> str:
        """导出为 Markdown 格式的可读剧本。"""
        content = self._build_script(movie_info)
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        logger.info("剧本已导出: %s", output_path)
        return output_path

    def export_json(
        self, movie_info: dict[str, Any], output_path: str, indent: int = 2
    ) -> str:
        """导出为格式化的 JSON 文件。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with output_file.open("w", encoding="utf-8") as f:
            json.dump(movie_info, f, ensure_ascii=False, indent=indent)

        logger.info("JSON 已导出: %s", output_path)
        return output_path

    def _build_script(self, data: dict[str, Any]) -> str:
        """构建完整剧本内容"""
        lines = self._build_header(data)
        lines.append("## 分章节场景剧本")
        lines.append("")

        chapter_map = self._build_chapter_map(data)
        scenes = sorted(
            data.get("key_scenes", []),
            key=lambda item: item.get("scene_id", 0),
        )

        current_phase = None
        for scene in scenes:
            phase = scene.get("phase", "unknown")
            chapter = chapter_map.get(phase)

            if phase != current_phase:
                current_phase = phase
                if chapter:
                    lines.append(f"## 章节：{chapter.get('title', phase)}")
                    lines.append(f"- 章节功能：{chapter.get('core_goal', '')}")
                    lines.append(f"- 主要矛盾：{chapter.get('main_conflict', '')}")
                    lines.append(f"- 情绪变化：{chapter.get('emotional_shift', '')}")
                    lines.append(f"- 风险与代价：{chapter.get('stakes', '')}")
                    lines.append("")
                else:
                    lines.append(f"## 章节：{phase}")
                    lines.append("")

            lines.extend(self._format_scene(scene, chapter))

        usage_notes = data.get("usage_notes", [])
        if isinstance(usage_notes, list):
            notes = [str(item).strip() for item in usage_notes if str(item).strip()]
        else:
            notes = []
        if notes:
            lines.append("## 使用建议")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def _build_header(self, data: dict[str, Any]) -> list[str]:
        """构建剧本头部信息"""
        title = data.get("title", "")
        year = data.get("year", "")
        genre = " / ".join(data.get("genre", []))
        synopsis = data.get("synopsis", "")

        lines = [
            f"# 《{title}》用户可读剧本",
            "",
            f"- 年份：{year}",
            f"- 类型：{genre}",
            "",
            "## 故事简介",
            synopsis,
            "",
            "## 主要人物",
        ]

        for character in data.get("characters", []):
            if isinstance(character, dict):
                name = character.get("name", "")
                role = character.get("role", "")
                motivation = character.get("motivation", "")
                lines.append(f"- **{name}**（{role}）：{motivation}")
            elif isinstance(character, str):
                lines.append(f"- {character}")

        lines.append("")
        return lines

    def _build_chapter_map(self, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """构建章节映射表"""
        chapter_map = {}
        for chapter in data.get("chapter_breakdown", []):
            if isinstance(chapter, dict):
                phase = chapter.get("phase")
                if phase:
                    chapter_map[phase] = chapter
        return chapter_map

    def _format_scene(
        self, scene: dict[str, Any], chapter: dict[str, Any] | None
    ) -> list[str]:
        """格式化单个场景"""
        scene_id = scene.get("scene_id", "?")
        summary = scene.get("summary", "")
        location = scene.get("location", "")
        emotion = scene.get("suggested_emotion", "")
        scene_goal = scene.get("scene_goal", "")
        importance = scene.get("importance", "")
        importance_breakdown = scene.get("importance_breakdown", {})
        score_reason = scene.get("score_reason", "")
        conflict = scene.get("conflict", "")
        turning_point = scene.get("turning_point", "")
        visual_tone = scene.get("visual_tone", "")
        dialogue_focus = scene.get("dialogue_focus", "")
        action_line = scene.get("action_line", "")
        sample_dialogue = scene.get("sample_dialogue", [])
        characters = "、".join(scene.get("characters_present", []))

        lines = [
            f"### 场景 {scene_id}：{summary}",
            f"- 场景地点：{location}",
            f"- 出场人物：{characters}",
            f"- 情绪基调：{emotion}",
        ]

        if importance != "":
            dim_names = {
                "plot_advancement": "情节推动力",
                "emotional_impact": "情感冲击",
                "turning_point": "转折重要性",
                "character_development": "人物塑造"
            }
            breakdown_strs = []
            if isinstance(importance_breakdown, dict):
                for dim_key, dim_name in dim_names.items():
                    dim_val = importance_breakdown.get(dim_key, "")
                    if dim_val != "":
                        breakdown_strs.append(f"{dim_name}:{dim_val}")
            if breakdown_strs:
                lines.append(f"- 场景重要度：{importance}/10 ({'/'.join(breakdown_strs)})")
            else:
                lines.append(f"- 场景重要度：{importance}/10")
        if score_reason:
            lines.append(f"- 评分依据：{score_reason}")

        if chapter:
            lines.append(f"- 所属章节：{chapter.get('title', chapter.get('phase', ''))}")

        if scene_goal:
            lines.append(f"- 本场目标：{scene_goal}")
        if conflict:
            lines.append(f"- 核心冲突：{conflict}")
        if turning_point:
            lines.append(f"- 转折点：{turning_point}")
        if visual_tone:
            lines.append(f"- 画面风格：{visual_tone}")
        if dialogue_focus:
            lines.append(f"- 对白焦点：{dialogue_focus}")

        lines.append("")

        if action_line:
            lines.append("**示例动作线**")
            lines.append(action_line)
            lines.append("")

        if isinstance(sample_dialogue, list):
            dialogue_lines = [str(item).strip() for item in sample_dialogue if str(item).strip()]
        else:
            dialogue_lines = []

        if dialogue_lines:
            lines.append("**示例对白（可二次改写）**")
            for item in dialogue_lines:
                lines.append(item)
            lines.append("")

        return lines
