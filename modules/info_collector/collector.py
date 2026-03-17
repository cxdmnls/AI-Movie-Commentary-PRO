from __future__ import annotations


import importlib
import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

conf = importlib.import_module("conf")
from .utils import clean_html_text, safe_request, safe_request_json

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

logger = logging.getLogger(__name__)


def _load_prompt(prompt_name: str) -> str:
    """从 prompts 目录读取指定模板。"""
    prompt_file = Path(conf.PROMPTS_DIR) / prompt_name
    return prompt_file.read_text(encoding="utf-8")


class InfoCollector:
    """电影信息聚合采集器。"""

    def __init__(self):
        """初始化采集器。"""
        self.sources = list(conf.INFO_SEARCH_SOURCES)
        self.dashscope = None
        try:
            self.dashscope = importlib.import_module("dashscope")
            setattr(self.dashscope, "api_key", conf.DASHSCOPE_API_KEY)
        except ModuleNotFoundError:
            logger.warning("未安装 dashscope，M3 将使用 HTTP 方式调用 Qwen。")

    def collect(
        self,
        movie_name: str,
        year: int | None = None,
        keyscene_mode: bool = False,
        subtitles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """从多个来源采集电影信息并聚合返回。

        Args:
            movie_name: 电影名称
            year: 年份（可选）
            keyscene_mode: 是否启用关键场景模式（生成完整 key_scenes 和剧情结构）
            subtitles: 字幕列表（用于时间戳匹配，仅在 keyscene_mode=True 时使用）
        """
        merged: dict[str, Any] = {
            "title": movie_name,
            "year": year or 0,
            "genre": [],
            "synopsis": "",
            "characters": [],
            "plot_structure": {},
            "key_scenes": [],
            "emotional_arc": [],
        }

        for source in self.sources:
            try:
                if source == "douban":
                    data = self._search_douban(movie_name)
                elif source == "baidu_baike":
                    data = self._search_baidu_baike(movie_name)
                elif source == "tmdb":
                    data = self._search_tmdb(movie_name, year=year)
                elif source == "imdb_omdb":
                    data = self._search_imdb_omdb(movie_name)
                else:
                    logger.warning("忽略未知采集源: %s", source)
                    continue
                self._merge_dict(merged, data)
            except (ValueError, RuntimeError, requests.RequestException) as error:
                logger.warning("采集源 %s 执行失败: %s", source, error)

        if keyscene_mode:
            # 关键场景模式：使用多轮 LLM 增强 + 时间戳匹配
            logger.info("启用关键场景模式，生成完整剧情结构...")
            merged = self._enrich_with_llm_v2(movie_name, merged)

            # 如果提供了字幕，自动匹配时间戳
            if subtitles:
                logger.info("自动匹配时间戳...")
                merged = self._match_timestamps(merged, subtitles)
        else:
            # 标准模式：使用简化版 LLM 增强
            merged = self._enrich_with_llm(movie_name, merged)

        logger.info("信息采集完成: title=%s", merged.get("title"))
        return merged

    def _enrich_with_llm(self, movie_name: str, merged: dict[str, object]) -> dict[str, object]:
        """使用 Qwen 对剧情做结构化增强。"""
        if not str(conf.DASHSCOPE_API_KEY or "").strip():
            return merged

        try:
            payload = {
                "title": merged.get("title") or movie_name,
                "year": merged.get("year") or 0,
                "genre": merged.get("genre") or [],
                "synopsis": merged.get("synopsis") or "",
                "characters": merged.get("characters") or [],
            }
            prompt_template = _load_prompt("plot_enhancement.txt")
            prompt = prompt_template.format(
                target_chars_min=int(conf.M3_SYNOPSIS_TARGET_CHARS * 0.9),
                target_chars_max=int(conf.M3_SYNOPSIS_TARGET_CHARS * 1.2),
                movie_info_json=json.dumps(payload, ensure_ascii=False)
            )

            content = self._call_qwen(prompt, max_tokens=2400)
            parsed = self._parse_json_response(content)
            if not isinstance(parsed, dict):
                return merged

            synopsis = parsed.get("synopsis")
            if isinstance(synopsis, str) and len(synopsis.strip()) >= int(conf.M3_SYNOPSIS_TARGET_CHARS * 0.3):
                merged["synopsis"] = synopsis.strip()

            characters = parsed.get("characters")
            if isinstance(characters, list) and characters:
                normalized_characters = []
                for item in characters:
                    if isinstance(item, dict):
                        name = str(item.get("name", "")).strip()
                        role = str(item.get("role", "")).strip()
                        motivation = str(item.get("motivation", "")).strip()
                        if name:
                            normalized_characters.append(
                                {"name": name, "role": role, "motivation": motivation}
                            )
                    elif isinstance(item, str) and item.strip():
                        normalized_characters.append(item.strip())
                if normalized_characters:
                    merged["characters"] = normalized_characters

            plot_structure = parsed.get("plot_structure")
            if isinstance(plot_structure, dict) and plot_structure:
                merged["plot_structure"] = self._strip_provenance_fields(plot_structure)

            key_scenes = parsed.get("key_scenes")
            if isinstance(key_scenes, list) and key_scenes:
                merged["key_scenes"] = self._strip_provenance_fields(key_scenes)

            emotional_arc = parsed.get("emotional_arc")
            if isinstance(emotional_arc, list) and emotional_arc:
                merged["emotional_arc"] = self._strip_provenance_fields(emotional_arc)
        except (ValueError, RuntimeError, requests.RequestException, KeyError) as error:
            logger.warning("M3 LLM 剧情增强失败，保留原始采集结果: %s", error)

        merged = self._strip_provenance_fields(merged)
        return merged

    def _strip_provenance_fields(self, value: object) -> object:
        """递归清理模型来源标记字段。"""
        forbidden_keys = {
            "generated_by",
            "content_generated_by",
            "generation_method",
            "importance_source",
            "details_source",
        }
        if isinstance(value, dict):
            cleaned: dict[str, object] = {}
            for key, item in value.items():
                if key in forbidden_keys:
                    continue
                cleaned[str(key)] = self._strip_provenance_fields(item)
            return cleaned
        if isinstance(value, list):
            return [self._strip_provenance_fields(item) for item in value]
        return value

    def _call_qwen(self, prompt: str, max_tokens: int = 4096) -> str:
        """调用 Qwen，优先 SDK，缺失时走 OpenAI 兼容 HTTP 接口。"""
        if self.dashscope is not None:
            response = self.dashscope.Generation.call(
                model=conf.M3_QWEN_MODEL,
                api_key=conf.DASHSCOPE_API_KEY,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.6,
                result_format="message",
            )
            return self._extract_llm_content(response)

        endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        payload = {
            "model": conf.M3_QWEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {conf.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Qwen HTTP 返回格式异常")

                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    first = choices[0]
                    if isinstance(first, dict):
                        message = first.get("message")
                        if isinstance(message, dict):
                            content = message.get("content")
                            if isinstance(content, str):
                                return content
            except (requests.RequestException, ValueError, RuntimeError) as error:
                last_error = error
                logger.warning("Qwen HTTP 调用失败，重试中(%d/2): %s", attempt + 1, error)

        if last_error is not None:
            raise RuntimeError(f"Qwen HTTP 调用失败: {last_error}")

        raise RuntimeError("Qwen HTTP 返回内容缺失")

    def _extract_llm_content(self, response: object) -> str:
        """统一提取 DashScope 文本内容。"""
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
            logger.warning("M3 LLM JSON 解析失败: %s", error)
            return {}

        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return parsed
        return {}

    def _search_douban(self, movie_name: str) -> dict[str, object]:
        """通过豆瓣搜索页面抓取基础信息。"""
        try:
            suggest_url = "https://movie.douban.com/j/subject_suggest"
            suggest_data = safe_request_json(
                suggest_url,
                headers=conf.DOUBAN_HEADERS,
                params={"q": movie_name},
                timeout=15,
            )
            if not isinstance(suggest_data, list):
                return {}

            candidates = [item for item in suggest_data if isinstance(item, dict)]
            if not candidates:
                return {}

            subject = next(
                (
                    item
                    for item in candidates
                    if movie_name in str(item.get("title", ""))
                    or movie_name in str(item.get("sub_title", ""))
                ),
                candidates[0],
            )

            title = str(subject.get("title") or movie_name)
            year = self._extract_year(str(subject.get("year", "")))
            subject_url = str(subject.get("url") or "")
            if not subject_url:
                subject_id = str(subject.get("id") or "").strip()
                if subject_id:
                    subject_url = f"https://movie.douban.com/subject/{subject_id}/"
            if not subject_url:
                return {"title": title, "year": year}

            html = safe_request(subject_url, headers=conf.DOUBAN_HEADERS, timeout=15)
            if not html:
                return {"title": title, "year": year}

            soup = BeautifulSoup(html, "lxml")
            synopsis_node = soup.select_one('span[property="v:summary"]')
            synopsis = self._normalize_text(synopsis_node.get_text(" ", strip=True) if synopsis_node else "")

            genre_nodes = soup.select('span[property="v:genre"]')
            genres = [self._normalize_text(node.get_text(" ", strip=True)) for node in genre_nodes]
            genres = [item for item in genres if item]

            starring_nodes = soup.select('a[rel="v:starring"]')
            characters = [self._normalize_text(node.get_text(" ", strip=True)) for node in starring_nodes]
            characters = [item for item in characters if item]

            if not year:
                year_node = soup.select_one("span.year")
                year = self._extract_year(year_node.get_text(" ", strip=True) if year_node else "")

            return {
                "title": title,
                "year": year,
                "genre": genres,
                "characters": characters[:12],
                "synopsis": synopsis,
            }
        except (ValueError, RuntimeError, requests.RequestException) as error:
            logger.warning("豆瓣搜索失败: %s", error)
            return {}

    def _search_baidu_baike(self, movie_name: str) -> dict[str, object]:
        """通过百度百科页面抓取简介信息。"""
        try:
            url = f"https://baike.baidu.com/item/{quote(movie_name)}"
            html = safe_request(url, headers=conf.DOUBAN_HEADERS, timeout=15)
            if not html:
                return {}

            soup = BeautifulSoup(html, "lxml")
            summary_node = soup.select_one("div.lemma-summary")
            synopsis = ""
            if summary_node:
                synopsis = self._normalize_text(summary_node.get_text(" ", strip=True))
            if not synopsis:
                text = clean_html_text(html)
                synopsis = self._extract_synopsis(text, movie_name)
            return {
                "title": movie_name,
                "synopsis": synopsis,
            }
        except (ValueError, RuntimeError, requests.RequestException) as error:
            logger.warning("百度百科搜索失败: %s", error)
            return {}

    def _search_tmdb(self, movie_name: str, year: int | None = None) -> dict[str, object]:
        """使用 TMDb API 查询电影信息。"""
        try:
            if not conf.TMDB_API_KEY:
                logger.info("未配置 TMDB_API_KEY，跳过 TMDb 采集")
                return {}

            query_url = f"{conf.TMDB_BASE_URL}/search/movie"
            params: dict[str, str | int] = {
                "api_key": conf.TMDB_API_KEY,
                "query": movie_name,
                "language": "zh-CN",
            }
            if year:
                params["year"] = year

            data = safe_request_json(query_url, params=params, timeout=15, retries=2)
            if not isinstance(data, dict):
                return {}

            results_obj = data.get("results", [])
            if not isinstance(results_obj, list):
                return {}
            results = [item for item in results_obj if isinstance(item, dict)]
            if not results:
                return {}

            top = results[0]
            movie_id = top.get("id")
            release_date = str(top.get("release_date", ""))
            parsed_year = self._extract_year(release_date)

            genres: list[str] = []
            characters: list[str] = []
            if movie_id:
                detail_url = f"{conf.TMDB_BASE_URL}/movie/{movie_id}"
                detail_data = safe_request_json(
                    detail_url,
                    params={
                        "api_key": conf.TMDB_API_KEY,
                        "language": "zh-CN",
                        "append_to_response": "credits",
                    },
                    timeout=15,
                    retries=2,
                )
                if isinstance(detail_data, dict):
                    genres_obj = detail_data.get("genres", [])
                    if isinstance(genres_obj, list):
                        genres = [
                            str(item.get("name", "")).strip()
                            for item in genres_obj
                            if isinstance(item, dict) and str(item.get("name", "")).strip()
                        ]

                    credits = detail_data.get("credits", {})
                    if isinstance(credits, dict):
                        cast_obj = credits.get("cast", [])
                        if isinstance(cast_obj, list):
                            characters = [
                                str(item.get("name", "")).strip()
                                for item in cast_obj[:15]
                                if isinstance(item, dict) and str(item.get("name", "")).strip()
                            ]

            return {
                "title": str(top.get("title") or movie_name),
                "year": parsed_year,
                "synopsis": self._normalize_text(str(top.get("overview", "") or "")),
                "genre": genres,
                "characters": characters,
            }
        except (ValueError, RuntimeError, requests.RequestException, KeyError) as error:
            logger.warning("TMDb 查询失败: %s", error)
            return {}

    def save(self, info: dict[str, object], output_path: str) -> None:
        """将采集结果保存为 JSON 文件。"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as file:
            json.dump(info, file, ensure_ascii=False, indent=2)
        logger.info("信息已保存: %s", output_file)

    def _search_imdb_omdb(self, movie_name: str) -> dict[str, object]:
        """使用 IMDb Suggest + OMDb 兜底采集。"""
        try:
            suggest_url = f"https://v2.sg.media-imdb.com/suggestion/x/{quote(movie_name)}.json"
            suggest_data = safe_request_json(suggest_url, headers=conf.DOUBAN_HEADERS, timeout=15, retries=2)
            if not isinstance(suggest_data, dict):
                return {}

            candidates = suggest_data.get("d", [])
            if not isinstance(candidates, list):
                return {}

            movies = [
                item
                for item in candidates
                if isinstance(item, dict) and str(item.get("qid", "")) == "movie"
            ]
            if not movies:
                return {}

            top = movies[0]
            imdb_id = str(top.get("id", "")).strip()
            title = str(top.get("l", "")).strip()
            year_value = top.get("y")
            parsed_year = int(year_value) if isinstance(year_value, int) else self._extract_year(str(year_value or ""))

            actors_text = str(top.get("s", "")).strip()
            characters = [item.strip() for item in actors_text.split(",") if item.strip()]

            if not conf.OMDB_API_KEY or not imdb_id:
                return {
                    "title": title or movie_name,
                    "year": parsed_year,
                    "characters": characters,
                }

            omdb_data = safe_request_json(
                conf.OMDB_BASE_URL,
                timeout=15,
                params={"apikey": conf.OMDB_API_KEY, "i": imdb_id, "plot": "full"},
                retries=2,
            )
            if not isinstance(omdb_data, dict) or str(omdb_data.get("Response", "False")) != "True":
                return {
                    "title": title or movie_name,
                    "year": parsed_year,
                    "characters": characters,
                }

            genre_text = str(omdb_data.get("Genre", "")).strip()
            genres = [item.strip() for item in genre_text.split(",") if item.strip()]

            omdb_actors = [item.strip() for item in str(omdb_data.get("Actors", "")).split(",") if item.strip()]
            if omdb_actors:
                characters = omdb_actors

            plot = self._normalize_text(str(omdb_data.get("Plot", "")).strip())
            if len(plot) > 700:
                plot = plot[:700].rstrip()

            omdb_year = self._extract_year(str(omdb_data.get("Year", "")))
            return {
                "title": str(omdb_data.get("Title") or title or movie_name),
                "year": omdb_year or parsed_year,
                "genre": genres,
                "characters": characters,
                "synopsis": plot,
            }
        except (ValueError, RuntimeError, requests.RequestException, KeyError):
            return {}

    def _merge_dict(self, base: dict[str, object], incoming: dict[str, object]) -> None:
        """按字段类型合并信息字典。"""
        for key, value in incoming.items():
            if value in (None, "", [], {}):
                continue

            if key not in base:
                base[key] = value
                continue

            current = base[key]
            if isinstance(current, list) and isinstance(value, list):
                merged = list(dict.fromkeys([*current, *value]))
                base[key] = merged
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            elif key == "synopsis" and isinstance(current, str) and isinstance(value, str):
                if self._is_better_synopsis(current, value):
                    base[key] = value
            elif current in (None, "", 0, [], {}):
                base[key] = value

    def _extract_year(self, text: str) -> int:
        """从文本中提取四位年份。"""
        match = re.search(r"(19\\d{2}|20\\d{2})", text)
        if not match:
            return 0
        return int(match.group(1))

    def _extract_synopsis(self, text: str, movie_name: str) -> str:
        """从清洗文本中抽取简介片段。"""
        anchor = text.find(movie_name)
        if anchor >= 0:
            snippet = text[anchor : anchor + 500]
        else:
            snippet = text[:500]
        return self._normalize_text(snippet)

    def _normalize_text(self, text: str) -> str:
        """清洗文本中的噪声信息。"""
        normalized = re.sub(r"\s+", " ", text).strip()
        for noise in ["登录/注册", "下载豆瓣客户端", "扫码直接下载", "豆瓣", "电影", "音乐", "读书"]:
            normalized = normalized.replace(noise, "")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _is_better_synopsis(self, current: str, incoming: str) -> bool:
        """判断 incoming 简介是否优于 current。"""
        current_clean = self._normalize_text(current)
        incoming_clean = self._normalize_text(incoming)
        if not incoming_clean:
            return False
        if not current_clean:
            return True
        if len(incoming_clean) >= len(current_clean) + 20:
            return True
        if len(current_clean) < 40 <= len(incoming_clean):
            return True
        return False

    def _enrich_with_llm_v2(self, movie_name: str, merged: dict[str, Any]) -> dict[str, Any]:
        """使用多轮 Qwen 对剧情做结构化增强（整合自 regenerate_movie_info_with_qwen.py）"""
        if not str(conf.DASHSCOPE_API_KEY or "").strip():
            logger.warning("未配置 DASHSCOPE_API_KEY，跳过 LLM 增强")
            return merged

        api_key = conf.DASHSCOPE_API_KEY
        base_url = getattr(conf, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = getattr(conf, "M3_QWEN_MODEL", "qwen3.5-plus")

        seed = {
            "title": merged.get("title") or movie_name,
            "year": merged.get("year") or 0,
            "genre": merged.get("genre", []),
            "synopsis": merged.get("synopsis", ""),
            "characters": merged.get("characters", []),
            "plot_structure": merged.get("plot_structure", {}),
            "key_scenes": merged.get("key_scenes", []),
            "emotional_arc": merged.get("emotional_arc", []),
        }

        current_messages = self._build_llm_messages(seed)
        result: dict[str, Any] = {}
        normalized: dict[str, Any] = {}
        max_rounds = 3

        for round_index in range(max_rounds):
            result = self._call_llm_v2(base_url, api_key, model, current_messages)
            normalized = self._normalize_output_v2(result, seed)
            issues = self._assess_quality_v2(normalized, seed)
            if not issues:
                break
            if round_index == max_rounds - 1:
                logger.warning("达到最大轮次，仍有 %d 个问题未解决", len(issues))
                break
            current_messages = self._build_revision_messages(seed, normalized, issues)

        merged.update(normalized)
        merged = self._strip_provenance_fields(merged)
        logger.info("LLM 增强完成，生成 %d 个关键场景", len(merged.get("key_scenes", [])))
        return merged

    def _build_llm_messages(self, seed: dict[str, Any]) -> list[dict[str, str]]:
        """构建初始 LLM 消息（整合自 regenerate_movie_info_with_qwen.py）"""
        payload = {
            "task": "重建完整 movie_info.json，所有文本字段都由模型生成",
            "hard_rules": [
                "必须输出严格JSON，不得输出任何解释文字",
                "场景数量保持与输入 key_scenes 相同",
                "scene_id 必须连续并与输入 scene_id 一一对应",
                "importance 必须为 1-10 整数",
                "confidence 必须为 0-1 浮点",
                "synopsis 必须是 900-1200 字中文完整剧情梳理，按时间推进覆盖开端-发展-转折-高潮-结局",
                "chapter_breakdown 覆盖 6 个 phase: setup, inciting_incident, rising_action, midpoint, climax, resolution",
                "sample_dialogue 必须是2句中文对白",
                "unique_keywords 必须是2-4个短动词、名词短语或形容词，在10个字以内就可以，并且每个片段之间unique_keywords需要有区分度，使其能够作为区分每个片段的关键词",
                "key_scenes 每一项都必须细化：summary/scene_goal/conflict/turning_point 至少 18 字，action_line 至少 12 字",
                "禁止输出 generated_by、importance_source、details_source、content_generated_by、generation_method 等模型来源字段",
            ],
            "seed": seed,
            "output_schema": {
                "title": "string",
                "year": "int",
                "genre": ["string"],
                "synopsis": "string",
                "characters": [
                    {"name": "string", "role": "string", "motivation": "string"}
                ],
                "plot_structure": {
                    "setup": "string",
                    "inciting_incident": "string",
                    "rising_action": "string",
                    "midpoint": "string",
                    "climax": "string",
                    "resolution": "string",
                },
                "chapter_breakdown": [
                    {
                        "chapter_id": "int",
                        "phase": "string",
                        "title": "string",
                        "core_goal": "string",
                        "main_conflict": "string",
                        "plot_progress": "string",
                        "emotional_shift": "string",
                        "stakes": "string",
                    }
                ],
                "key_scenes": [
                    {
                        "scene_id": "int",
                        "phase": "string",
                        "summary": "string",
                        "importance": "int",
                        "suggested_emotion": "string",
                        "location": "string",
                        "characters_present": ["string"],
                        "scene_goal": "string",
                        "conflict": "string",
                        "turning_point": "string",
                        "visual_tone": "string",
                        "dialogue_focus": "string",
                        "action_line": "string",
                        "sample_dialogue": ["string", "string"],
                        "unique_keywords": ["string"],
                        "score_reason": "string",
                        "confidence": "float",
                    }
                ],
                "emotional_arc": [{"stage": "string", "emotion": "string"}],
                "usage_notes": ["string"],
            },
        }

        return [
            {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def _build_revision_messages(self, seed: dict[str, Any], draft: dict[str, Any], issues: list[str]) -> list[dict[str, str]]:
        """构建修复消息"""
        payload = {
            "task": "修复 movie_info.json 质量问题，必须输出完整 JSON",
            "issues": issues,
            "hard_rules": [
                "必须输出严格JSON，不得输出任何解释文字",
                "synopsis 必须是 900-1200 字中文完整剧情梳理",
                "场景数量保持与输入一致，scene_id 与输入一一对应",
                "importance 为 1-10 整数，confidence 为 0-1 浮点",
                "summary/scene_goal/conflict/turning_point 每项不少于18字",
                "sample_dialogue 必须是2句中文对白",
                "禁止输出 generated_by、importance_source、details_source、content_generated_by、generation_method",
            ],
            "seed": seed,
            "draft": draft,
        }
        return [
            {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def _call_llm_v2(self, base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        """调用 Qwen API（整合自 regenerate_movie_info_with_qwen.py）"""
        url = f"{base_url.rstrip('/')}/chat/completions"
        req_payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(req_payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                req = request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                )
                with request.urlopen(req, timeout=300) as resp:
                    content = resp.read().decode("utf-8")
                parsed = json.loads(content)
                response_text = parsed["choices"][0]["message"]["content"]
                return self._extract_json_v2(response_text)
            except (error.URLError, error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise RuntimeError(f"Qwen 请求失败: {last_error}")

    def _extract_json_v2(self, text: str) -> dict[str, Any]:
        """从 LLM 响应中提取 JSON"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        return json.loads(cleaned)

    def _normalize_output_v2(self, result: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
        """标准化 LLM 输出（整合自 regenerate_movie_info_with_qwen.py）"""
        expected_ids = []
        for scene in seed.get("key_scenes", []):
            try:
                raw_scene_id = scene.get("scene_id")
                expected_ids.append(int(raw_scene_id if raw_scene_id is not None else -1))
            except (TypeError, ValueError):
                continue
        if not expected_ids:
            expected_ids = list(range(1, 11))

        payload = {
            "title": str(result.get("title", seed.get("title", ""))),
            "year": int(result.get("year", seed.get("year", 0)) or 0),
            "genre": result.get("genre", seed.get("genre", [])),
            "synopsis": str(result.get("synopsis", "")),
            "characters": result.get("characters", []),
            "plot_structure": result.get("plot_structure", {}),
            "chapter_breakdown": result.get("chapter_breakdown", []),
            "key_scenes": self._normalize_scenes_v2(result.get("key_scenes", []), expected_ids),
            "emotional_arc": result.get("emotional_arc", []),
            "usage_notes": result.get("usage_notes", []),
        }

        return self._strip_provenance_fields(payload)

    def _normalize_scenes_v2(self, scenes: list[dict[str, Any]], expected_ids: list[int]) -> list[dict[str, Any]]:
        """标准化场景数据"""
        by_id: dict[int, dict[str, Any]] = {}
        for scene in scenes:
            try:
                raw_scene_id = scene.get("scene_id")
                scene_id = int(raw_scene_id if raw_scene_id is not None else -1)
            except (TypeError, ValueError):
                continue
            by_id[scene_id] = scene

        normalized: list[dict[str, Any]] = []
        for scene_id in expected_ids:
            source = by_id.get(scene_id, {"scene_id": scene_id})
            try:
                importance = int(source.get("importance", 6))
            except (TypeError, ValueError):
                importance = 6
            importance = max(1, min(10, importance))

            try:
                confidence = float(source.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            confidence = max(0.0, min(1.0, confidence))

            raw_dialogue = source.get("sample_dialogue", [])
            if not isinstance(raw_dialogue, list):
                raw_dialogue = []
            sample_dialogue = [str(line).strip() for line in raw_dialogue if str(line).strip()][:2]
            while len(sample_dialogue) < 2:
                sample_dialogue.append("")

            raw_chars = source.get("characters_present", [])
            if not isinstance(raw_chars, list):
                raw_chars = []

            normalized.append(
                {
                    "scene_id": scene_id,
                    "phase": str(source.get("phase", "")).strip(),
                    "summary": str(source.get("summary", "")).strip(),
                    "importance": importance,
                    "suggested_emotion": str(source.get("suggested_emotion", "")).strip(),
                    "location": str(source.get("location", "")).strip(),
                    "characters_present": [str(ch).strip() for ch in raw_chars if str(ch).strip()],
                    "scene_goal": str(source.get("scene_goal", "")).strip(),
                    "conflict": str(source.get("conflict", "")).strip(),
                    "turning_point": str(source.get("turning_point", "")).strip(),
                    "visual_tone": str(source.get("visual_tone", "")).strip(),
                    "dialogue_focus": str(source.get("dialogue_focus", "")).strip(),
                    "action_line": str(source.get("action_line", "")).strip(),
                    "sample_dialogue": sample_dialogue,
                    "unique_keywords": [str(kw).strip() for kw in source.get("unique_keywords", []) if str(kw).strip()][:4],
                    "score_reason": str(source.get("score_reason", "")).strip(),
                    "confidence": confidence,
                }
            )
        return normalized

    def _assess_quality_v2(self, payload: dict[str, Any], seed: dict[str, Any]) -> list[str]:
        """评估输出质量并返回问题列表"""
        issues: list[str] = []

        synopsis_len = len(str(payload.get("synopsis", "")).strip())
        if synopsis_len < 900:
            issues.append(f"synopsis 过短，当前约 {synopsis_len} 字，需扩展至 900-1200 字")

        expected_scene_count = len(seed.get("key_scenes", [])) or 10
        scenes_obj = payload.get("key_scenes", [])
        scenes = scenes_obj if isinstance(scenes_obj, list) else []
        if len(scenes) != expected_scene_count:
            issues.append(f"key_scenes 数量不匹配，当前 {len(scenes)}，期望 {expected_scene_count}")

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                issues.append(f"scene {index} 结构非法")
                continue
            for field in ["summary", "scene_goal", "conflict", "turning_point"]:
                text = str(scene.get(field, "")).strip()
                if len(text) < 18:
                    issues.append(f"scene {index} 的 {field} 过短，需至少18字")
            dialogues_obj = scene.get("sample_dialogue", [])
            dialogues = dialogues_obj if isinstance(dialogues_obj, list) else []
            if len(dialogues) < 2 or not str(dialogues[0]).strip() or not str(dialogues[1]).strip():
                issues.append(f"scene {index} 的 sample_dialogue 不满足2句中文对白")

        return issues

    def _match_timestamps(self, movie_info: dict[str, Any], subtitles: list[dict[str, Any]]) -> dict[str, Any]:
        """为 key_scenes 匹配视频时间戳（整合自 add_keyscene_timestamps.py）"""
        total_duration = subtitles[-1]["end"] if subtitles else 6926
        logger.info("电影时长: %.1f 分钟，使用模糊匹配库: %s", total_duration / 60, "rapidfuzz" if HAS_RAPIDFUZZ else "difflib")

        results = []
        prev_end = 0

        for scene in movie_info.get("key_scenes", []):
            unique_keywords = scene.get("unique_keywords", [])
            if isinstance(unique_keywords, str):
                unique_keywords = [unique_keywords]

            interval = self._find_best_interval(unique_keywords, subtitles, prev_end=prev_end, window=120, threshold=75)

            if interval:
                start = interval["start"]
                end = interval["end"]
            else:
                start = prev_end + 30
                end = start + 120

            if end > total_duration:
                end = total_duration

            prev_end = end

            scene["video_clip"] = {
                "start": round(start, 1),
                "end": round(end, 1),
            }
            results.append(scene)

            if interval:
                logger.info("Scene %s: %.1fm - %.1fm (匹配%d个关键词, 相似度%.0f%%)",
                           scene.get("scene_id"), start/60, end/60, interval["matched_keywords"], interval["score"])
            else:
                logger.info("Scene %s: %.1fm - %.1fm (顺序估算)", scene.get("scene_id"), start/60, end/60)

        movie_info["key_scenes"] = results
        return movie_info

    def _fuzzy_match(self, keyword: str, text: str, threshold: float = 75) -> float:
        """模糊匹配关键词和文本"""
        if HAS_RAPIDFUZZ:
            score = fuzz.partial_ratio(keyword, text)
        else:
            score = SequenceMatcher(None, keyword, text).ratio() * 100
            score = max(score, SequenceMatcher(None, keyword, text[:len(keyword)+5]).ratio() * 100)
        return score if score >= threshold else 0

    def _find_best_match(self, keyword: str, subtitles: list[dict[str, Any]], threshold: float = 75) -> list[dict[str, Any]]:
        """找关键词在字幕中的最佳匹配"""
        if not keyword or not subtitles:
            return []

        matches = []
        for sub in subtitles:
            text = sub.get("text", "")
            score = self._fuzzy_match(keyword, text, threshold)
            if score > 0:
                matches.append({
                    "start": sub["start"],
                    "end": sub["end"],
                    "text": text[:50],
                    "keyword": keyword,
                    "score": score
                })
        return matches

    def _find_best_interval(self, keywords: list, subtitles: list, prev_end: float = 0,
                           window: float = 120, threshold: float = 75) -> dict | None:
        """多关键词区间评分，找到最佳时间区间"""
        if not keywords:
            return None

        all_matches = []
        for kw in keywords:
            matches = self._find_best_match(kw, subtitles, threshold)
            all_matches.append({"keyword": kw, "matches": matches})

        valid_keyword_count = sum(1 for m in all_matches if m["matches"])
        if valid_keyword_count == 0:
            return None

        centers = []
        for m in all_matches:
            if m["matches"]:
                best = max(m["matches"], key=lambda x: x["score"])
                center = (best["start"] + best["end"]) / 2
                centers.append({"center": center, "score": best["score"]})

        if not centers:
            return None

        avg_center = sum(c["center"] for c in centers) / len(centers)
        avg_score = sum(c["score"] for c in centers) / len(centers)

        interval_start = max(0, avg_center - window)
        interval_end = avg_center + window

        original_start = interval_start
        original_end = interval_end

        if interval_start < prev_end + 30:
            interval_start = prev_end + 30
            interval_end = interval_start + (original_end - original_start)

        actual_center = (interval_start + interval_end) / 2

        return {
            "start": interval_start,
            "end": interval_end,
            "center": actual_center,
            "score": avg_score,
            "matched_keywords": valid_keyword_count
        }
