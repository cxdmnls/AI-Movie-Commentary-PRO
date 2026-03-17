"""用户审核交互模块。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

logger = logging.getLogger(__name__)


class Reviewer:
    """负责剧本与素材审核的人机交互。"""

    def __init__(self, workspace_dir: str):
        """初始化审核器。"""
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.console = Console()

    def review_script(self, script: dict) -> dict:
        """审核点 1：展示剧本并允许用户确认或修改。"""
        if not isinstance(script, dict):
            raise ValueError("script 必须是 dict")

        segments = script.get("segments", [])
        if not isinstance(segments, list):
            raise ValueError("script['segments'] 必须是 list")

        while True:
            self.console.print(Panel("[bold cyan]剧本审核（审核点 1）[/bold cyan]"))
            self._display_segment_table(segments)
            action = self._prompt_user(
                "请选择操作",
                ["确认", "编辑某个 segment", "删除某个 segment", "重新生成"],
            )

            if action == "确认":
                script["segments"] = segments
                logger.info("用户确认剧本，segment 数量: %d", len(segments))
                return script

            if action == "编辑某个 segment":
                index = self._prompt_segment_index(segments)
                if index is None:
                    continue
                edited = dict(segments[index])

                old_text = self._segment_text(edited)
                new_text = Prompt.ask(
                    "输入新的解说词（直接回车表示不改）",
                    default=old_text,
                    show_default=False,
                ).strip()
                if new_text:
                    edited["narration_text"] = new_text
                    if "narration" in edited:
                        edited["narration"] = new_text
                    if "text" in edited:
                        edited["text"] = new_text

                old_emotion = str(edited.get("emotion") or "")
                new_emotion = Prompt.ask(
                    "输入新的情感标签（直接回车表示不改）",
                    default=old_emotion,
                    show_default=False,
                ).strip()
                if new_emotion:
                    edited["emotion"] = new_emotion

                segments[index] = edited
                logger.info("用户已编辑 segment[%d]", index)
                continue

            if action == "删除某个 segment":
                index = self._prompt_segment_index(segments)
                if index is None:
                    continue
                deleted = segments.pop(index)
                logger.info("用户删除 segment[%d]: %s", index, self._segment_text(deleted)[:20])
                continue

            if action == "重新生成":
                logger.info("用户要求重新生成剧本")
                return {"regenerate": True, "segments": segments}

    def review_materials(self, segments: list[dict]) -> list[dict]:
        """审核点 2：展示素材并允许用户取舍。"""
        reviewed = [dict(item) for item in segments]

        while True:
            self.console.print(Panel("[bold green]素材审核（审核点 2）[/bold green]"))
            self._display_segment_table(reviewed)
            action = self._prompt_user(
                "请选择操作",
                ["确认全部", "跳过某个 segment", "移除某个 segment 的 BGM"],
            )

            if action == "确认全部":
                logger.info("用户确认素材，segment 数量: %d", len(reviewed))
                return reviewed

            if action == "跳过某个 segment":
                index = self._prompt_segment_index(reviewed)
                if index is None:
                    continue
                reviewed[index]["skip"] = True
                logger.info("用户标记跳过 segment[%d]", index)
                continue

            if action == "移除某个 segment 的 BGM":
                index = self._prompt_segment_index(reviewed)
                if index is None:
                    continue
                reviewed[index]["bgm_clip_path"] = None
                reviewed[index]["bgm_required"] = False
                logger.info("用户移除 segment[%d] 的 BGM", index)

    def _prompt_user(self, message: str, choices: list[str]) -> str:
        """统一用户输入交互。"""
        choice_map = {str(index + 1): value for index, value in enumerate(choices)}
        option_text = "\n".join([f"{key}. {value}" for key, value in choice_map.items()])
        self.console.print(Panel(option_text, title=message))
        selected = Prompt.ask(
            "请输入选项编号",
            choices=list(choice_map.keys()),
            default="1",
        )
        return choice_map[selected]

    def _display_segment_table(self, segments: list[dict]):
        """使用 rich.table 展示 segment 列表。"""
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("序号", justify="right", width=6)
        table.add_column("时间范围", width=18)
        table.add_column("解说词摘要", width=30)
        table.add_column("情感", width=8)
        table.add_column("视频片段", width=22)
        table.add_column("TTS 音频", width=22)
        table.add_column("BGM", width=22)

        for index, segment in enumerate(segments):
            video_clip = segment.get("video_clip", {})
            if isinstance(video_clip, dict):
                start = float(video_clip.get("start", 0.0))
                end = float(video_clip.get("end", 0.0))
            else:
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", 0.0))
            text = self._segment_text(segment)
            summary = text if len(text) <= 24 else text[:24] + "..."
            emotion = str(segment.get("emotion") or "-")
            video_path = str(segment.get("video_clip_path") or "-")
            tts_path = str(segment.get("tts_audio_path") or "-")
            bgm_path = str(segment.get("bgm_clip_path") or "-")

            table.add_row(
                str(index),
                f"{start:.2f}s - {end:.2f}s",
                summary,
                emotion,
                self._short_path(video_path),
                self._short_path(tts_path),
                self._short_path(bgm_path),
            )

        self.console.print(table)

    def _prompt_segment_index(self, segments: list[dict]) -> int | None:
        """提示并返回合法的 segment 序号。"""
        if not segments:
            self.console.print("[yellow]当前没有可操作的 segment。[/yellow]")
            return None

        max_index = len(segments) - 1
        selected = Prompt.ask(
            f"请输入 segment 序号（0-{max_index}）",
            default="0",
        )
        try:
            index = int(selected)
        except ValueError:
            self.console.print("[red]输入无效，请输入数字。[/red]")
            return None

        if index < 0 or index > max_index:
            self.console.print("[red]序号超出范围。[/red]")
            return None
        return index

    @staticmethod
    def _segment_text(segment: dict[str, Any]) -> str:
        """读取 segment 文本字段。"""
        for key in ("narration_text", "narration", "text", "script"):
            value = segment.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _short_path(path_text: str) -> str:
        """缩短路径显示长度，避免表格过宽。"""
        if path_text in {"", "-", "None"}:
            return "-"
        path = Path(path_text)
        name = path.name
        if len(name) <= 20:
            return name
        return name[:20] + "..."
