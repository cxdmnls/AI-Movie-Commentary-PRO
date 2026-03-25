from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path


def _build_keywords(raw_keywords: str, keywords_file: str) -> list[str]:
    keyword_list: list[str] = []

    if keywords_file:
        path = Path(keywords_file)
        if not path.is_file():
            raise FileNotFoundError(f"关键词文件不存在: {keywords_file}")
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and value not in keyword_list:
                keyword_list.append(value)

    if raw_keywords:
        for item in raw_keywords.split(","):
            value = item.strip()
            if value and value not in keyword_list:
                keyword_list.append(value)

    return keyword_list


def _load_subtitles(workspace: Path, movie_name: str, srt: str) -> list[dict]:
    if srt:
        srt_path = Path(srt)
        if not srt_path.is_file():
            raise FileNotFoundError(f"SRT 文件不存在: {srt}")
        return _parse_srt_file(srt_path)

    workspace_srt = workspace / f"{movie_name}.srt"
    if workspace_srt.is_file():
        return _parse_srt_file(workspace_srt)

    subtitles_json = workspace / "subtitles.json"
    if subtitles_json.is_file():
        data = json.loads(subtitles_json.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data

    raise FileNotFoundError("未找到字幕来源：请传 --srt 或在 workspace 下准备同名 .srt/subtitles.json")


def _parse_srt_file(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n").strip())
    subtitles: list[dict] = []

    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if re.fullmatch(r"\d+", lines[0]) else 0
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        left, right = [part.strip() for part in lines[idx].split("-->", 1)]
        start = _parse_srt_time(left)
        end = _parse_srt_time(right)
        text = " ".join(lines[idx + 1 :]).strip()
        if end > start and text:
            subtitles.append({"start": start, "end": end, "text": text})
    return subtitles


def _parse_srt_time(value: str) -> float:
    normalized = value.replace(",", ".").strip()
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?", normalized)
    if not match:
        return 0.0
    h = int(match.group(1))
    m = int(match.group(2))
    s = int(match.group(3))
    frac = (match.group(4) or "0").ljust(3, "0")[:3]
    return float(h * 3600 + m * 60 + s + int(frac) / 1000)


def main() -> int:
    parser = argparse.ArgumentParser(description="只执行关键场景模式 Step 3（M3 信息采集）")
    parser.add_argument("--workspace", required=True, help="workspace 目录，例如 workspace/西虹市首富")
    parser.add_argument("--name", default="", help="电影名称（默认使用 workspace 目录名）")
    parser.add_argument("--srt", default="", help="SRT 文件路径（优先）")
    parser.add_argument("--keywords", default="", help="关键词，逗号分隔")
    parser.add_argument("--keywords-file", default="", help="关键词文件（一行一个）")
    parser.add_argument("--skip-timestamp-match", action="store_true", help="跳过时间戳匹配")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace 不存在: {workspace}")

    movie_name = args.name.strip() or workspace.name
    keywords = _build_keywords(args.keywords, args.keywords_file)
    subtitles = _load_subtitles(workspace, movie_name, args.srt)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from modules.info_collector import InfoCollector

    collector = InfoCollector()
    movie_info = collector.collect(
        movie_name,
        keyscene_mode=True,
        subtitles=None if args.skip_timestamp_match else subtitles,
        workspace=str(workspace),
        keywords=keywords,
    )

    output_path = workspace / "movie_info.json"
    collector.save(movie_info, str(output_path))
    print(f"完成：{output_path}")
    print(f"关键场景数：{len(movie_info.get('key_scenes', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
