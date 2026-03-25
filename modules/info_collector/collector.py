from __future__ import annotations


import importlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

conf = importlib.import_module("conf")
from .utils import (
    clean_html_text,
    safe_request,
    safe_request_json,
)

logger = logging.getLogger(__name__)


def _load_prompt(prompt_name: str, **kwargs) -> str:
    """从 prompts 目录读取指定模板，支持占位符替换。
    
    Args:
        prompt_name: prompt 文件名（不含 .txt 后缀）
        **kwargs: 占位符键值对，如 sample_dialogue_count=6
    
    Returns:
        替换后的 prompt 文本
    """
    if not prompt_name.endswith('.txt'):
        prompt_name = f"{prompt_name}.txt"
    prompt_file = Path(conf.PROMPTS_DIR) / prompt_name
    content = prompt_file.read_text(encoding="utf-8")
    
    for key, value in kwargs.items():
        placeholder = f"{{{key}}}"
        content = content.replace(placeholder, str(value))
    
    return content


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
        keyscene_mode: bool = True,
        subtitles: list[dict[str, Any]] | None = None,
        workspace: str | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        """从多个来源采集电影信息并聚合返回。

        Args:
            movie_name: 电影名称
            year: 年份（可选）
            keyscene_mode: 是否启用关键场景模式（生成完整 key_scenes）
            subtitles: 字幕列表（用于时间戳匹配，默认自动从 workspace 加载）
            workspace: 工作目录（用于自动加载字幕文件）
            keywords: 关键词列表（用于联网检索上下文增强）
        """
        merged: dict[str, Any] = {
            "title": movie_name,
            "year": year or 0,
            "genre": [],
            "synopsis": "",
            "characters": [],
            "key_scenes": [],
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

        search_context = self._collect_search_context_by_keywords(movie_name, keywords, year=year)
        if search_context:
            merged["search_context"] = search_context

        min_scene_count = max(1, int(getattr(conf, "M3_KEYSCENE_MIN", 10)))
        max_scene_count = max(min_scene_count, int(getattr(conf, "M3_KEYSCENE_MAX", 20)))

        # 自动匹配时间戳：优先使用传入的 subtitles，否则尝试从 workspace 加载
        if subtitles is None and workspace:
            subtitles_path = Path(workspace) / "subtitles.json"
            if subtitles_path.exists():
                try:
                    with subtitles_path.open("r", encoding="utf-8") as f:
                        subtitles = json.load(f)
                    logger.info("已自动加载字幕文件: %s", subtitles_path)
                except Exception as e:
                    logger.warning("加载字幕文件失败: %s", e)

        target_scene_count = self._estimate_scene_count(subtitles, min_scene_count, max_scene_count)

        # 关键场景模式：使用多轮 LLM 增强 + 时间戳匹配
        logger.info("启用关键场景模式，生成完整剧情结构（场景数范围: %d-%d）...", min_scene_count, max_scene_count)
        merged = self._enrich_with_llm_v2(
            movie_name,
            merged,
            min_scene_count=min_scene_count,
            max_scene_count=max_scene_count,
            target_scene_count=target_scene_count,
            search_context=search_context,
            subtitles=subtitles or [],
        )

        for field in ("title", "year", "genre", "synopsis", "characters"):
            merged.pop(field, None)

        logger.info("信息采集完成: key_scenes count=%d", len(merged.get("key_scenes", [])))
        return merged

    def _estimate_scene_count(
        self,
        subtitles: list[dict[str, Any]] | None,
        min_scene_count: int,
        max_scene_count: int,
    ) -> int:
        """根据字幕密度估算场景数量（仅作为模型建议值）。"""
        midpoint = int(round((min_scene_count + max_scene_count) / 2))
        if not subtitles:
            return max(min_scene_count, min(max_scene_count, midpoint))

        try:
            total_duration = float(subtitles[-1].get("end", 0.0))
        except (TypeError, ValueError, IndexError, AttributeError):
            total_duration = 0.0

        if total_duration <= 0:
            return max(min_scene_count, min(max_scene_count, midpoint))

        subtitle_count = len(subtitles)
        duration_based = int(round((total_duration / 60.0) / 8.0))
        density_bonus = 1 if subtitle_count >= 1500 else 0
        estimated = duration_based + density_bonus
        return max(min_scene_count, min(max_scene_count, estimated))

    def _collect_search_context_by_keywords(
        self,
        movie_name: str,
        keywords: list[str] | None,
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        """关键词联网检索：代码抓取，再交给 Qwen 归纳。"""
        query_terms: list[str] = []
        base_term = str(movie_name or "").strip()
        if base_term:
            query_terms.append(base_term)

        if isinstance(keywords, list):
            for item in keywords:
                value = str(item or "").strip()
                if value and value not in query_terms:
                    query_terms.append(value)

        if not query_terms:
            return []

        max_terms = 8
        max_hits = 30
        seen: set[str] = set()
        hits: list[dict[str, Any]] = []

        for term in query_terms[:max_terms]:
            source_results: list[tuple[str, dict[str, object]]] = []

            try:
                source_results.append(("douban", self._search_douban(term)))
            except (ValueError, RuntimeError, requests.RequestException):
                source_results.append(("douban", {}))

            try:
                source_results.append(("baidu_baike", self._search_baidu_baike(term)))
            except (ValueError, RuntimeError, requests.RequestException):
                source_results.append(("baidu_baike", {}))

            try:
                source_results.append(("tmdb", self._search_tmdb(term, year=year if term == movie_name else None)))
            except (ValueError, RuntimeError, requests.RequestException):
                source_results.append(("tmdb", {}))

            try:
                source_results.append(("imdb_omdb", self._search_imdb_omdb(term)))
            except (ValueError, RuntimeError, requests.RequestException):
                source_results.append(("imdb_omdb", {}))

            for source, data in source_results:
                if not isinstance(data, dict) or not data:
                    continue

                title = str(data.get("title") or "").strip()
                synopsis = str(data.get("synopsis") or "").strip()
                if not title and not synopsis:
                    continue

                signature = f"{source}|{title}|{synopsis[:120]}"
                if signature in seen:
                    continue
                seen.add(signature)

                genre_value = data.get("genre", [])
                if not isinstance(genre_value, list):
                    genre_value = []
                characters_value = data.get("characters", [])
                if not isinstance(characters_value, list):
                    characters_value = []

                hits.append(
                    {
                        "keyword": term,
                        "source": source,
                        "title": title,
                        "year": self._extract_year(str(data.get("year", "") or "")),
                        "genre": [str(item).strip() for item in genre_value if str(item).strip()][:8],
                        "characters": [str(item).strip() for item in characters_value if str(item).strip()][:12],
                        "synopsis": synopsis[:1200],
                    }
                )

                if len(hits) >= max_hits:
                    break

            if len(hits) >= max_hits:
                break

        logger.info("关键词联网检索完成：%d 条上下文", len(hits))
        return hits

    def _strip_provenance_fields(self, value: Any) -> Any:
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

    def _enrich_with_llm_v2(
        self,
        movie_name: str,
        merged: dict[str, Any],
        min_scene_count: int,
        max_scene_count: int,
        target_scene_count: int,
        search_context: list[dict[str, Any]] | None = None,
        subtitles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
            "key_scenes": merged.get("key_scenes", []),
        }

        current_messages = self._build_llm_messages(
            seed,
            min_scene_count=min_scene_count,
            max_scene_count=max_scene_count,
            target_scene_count=target_scene_count,
            search_context=search_context or [],
            subtitles=subtitles or [],
        )
        result: dict[str, Any] = {}
        normalized: dict[str, Any] = {}
        max_rounds = 3

        for round_index in range(max_rounds):
            result = self._call_llm_v2(base_url, api_key, model, current_messages)
            normalized = self._normalize_output_v2(
                result,
                seed,
                min_scene_count=min_scene_count,
                max_scene_count=max_scene_count,
                target_scene_count=target_scene_count,
            )
            issues = self._assess_quality_v2(
                normalized,
                seed,
                min_scene_count=min_scene_count,
                max_scene_count=max_scene_count,
            )
            if not issues:
                break
            if round_index == max_rounds - 1:
                logger.warning("达到最大轮次，仍有 %d 个问题未解决: %s", len(issues), issues)
                break
            current_messages = self._build_revision_messages(
                seed,
                normalized,
                issues,
                min_scene_count=min_scene_count,
                max_scene_count=max_scene_count,
                target_scene_count=target_scene_count,
                search_context=search_context or [],
                subtitles=subtitles or [],
            )

        merged.update(normalized)
        merged = self._strip_provenance_fields(merged)
        logger.info("LLM 增强完成，生成 %d 个关键场景", len(merged.get("key_scenes", [])))
        return merged

    def _build_llm_messages(
        self,
        seed: dict[str, Any],
        min_scene_count: int,
        max_scene_count: int,
        target_scene_count: int,
        search_context: list[dict[str, Any]],
        subtitles: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """构建初始 LLM 消息"""
        sample_dialogue_count = getattr(conf, "SAMPLE_DIALOGUE_COUNT", 6)
        output_schema = {
            "key_scenes": [
                {
                    "scene_id": "int",
                    "phase": "string",
                    "summary": "string",
                    "sample_dialogue": ["string"],
                    "video_clip": {
                        "start": "string",
                        "end": "string"
                    }
                }
            ]
        }

        prompt = _load_prompt(
            "m3_keyscene",
            mode="initial",
            min_scene_count=min_scene_count,
            max_scene_count=max_scene_count,
            target_scene_count=target_scene_count,
            sample_dialogue_count=sample_dialogue_count,
            search_context_json=json.dumps(search_context, ensure_ascii=False, indent=2),
            subtitles_json=json.dumps(subtitles, ensure_ascii=False, indent=2),
            output_schema_json=json.dumps(output_schema, ensure_ascii=False, indent=2),
            issues_json="[]",
            draft_json="{}",
        )

        return [
            {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ]

    def _build_revision_messages(
        self,
        seed: dict[str, Any],
        draft: dict[str, Any],
        issues: list[str],
        min_scene_count: int,
        max_scene_count: int,
        target_scene_count: int,
        search_context: list[dict[str, Any]],
        subtitles: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """构建修复消息"""
        sample_dialogue_count = getattr(conf, "SAMPLE_DIALOGUE_COUNT", 6)
        prompt = _load_prompt(
            "m3_keyscene",
            mode="revision",
            min_scene_count=min_scene_count,
            max_scene_count=max_scene_count,
            target_scene_count=target_scene_count,
            sample_dialogue_count=sample_dialogue_count,
            output_schema_json="{}",
            issues_json=json.dumps(issues, ensure_ascii=False, indent=2),
            search_context_json=json.dumps(search_context, ensure_ascii=False, indent=2),
            subtitles_json=json.dumps(subtitles, ensure_ascii=False, indent=2),
            draft_json=json.dumps(draft, ensure_ascii=False, indent=2),
        )
        return [
            {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
            {"role": "user", "content": prompt},
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
                with request.urlopen(req, timeout=600) as resp:
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

    def _normalize_output_v2(
        self,
        result: dict[str, Any],
        seed: dict[str, Any],
        min_scene_count: int,
        max_scene_count: int,
        target_scene_count: int,
    ) -> dict[str, Any]:
        """标准化 LLM 输出"""
        scenes_obj = result.get("key_scenes", [])
        scenes_list = scenes_obj if isinstance(scenes_obj, list) else []
        proposed_count = len(scenes_list) if scenes_list else target_scene_count
        scene_count = max(min_scene_count, min(max_scene_count, proposed_count))
        expected_ids = list(range(1, scene_count + 1))

        payload = {
            "key_scenes": self._normalize_scenes_v2(result.get("key_scenes") or [], expected_ids),
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

            sample_dialogue_count = getattr(conf, "SAMPLE_DIALOGUE_COUNT", 6)
            raw_dialogue = source.get("sample_dialogue", [])
            if not isinstance(raw_dialogue, list):
                raw_dialogue = []
            sample_dialogue = [str(line).strip() for line in raw_dialogue if str(line).strip()][:sample_dialogue_count]
            while len(sample_dialogue) < sample_dialogue_count:
                sample_dialogue.append("")

            video_clip_raw = source.get("video_clip", {})
            if not isinstance(video_clip_raw, dict):
                video_clip_raw = {}

            normalized.append(
                {
                    "scene_id": scene_id,
                    "phase": str(source.get("phase", "")).strip(),
                    "summary": str(source.get("summary", "")).strip(),
                    "sample_dialogue": sample_dialogue,
                    "video_clip": {
                        "start": str(video_clip_raw.get("start", "00:00:00.0")).strip(),
                        "end": str(video_clip_raw.get("end", "00:00:00.0")).strip(),
                    },
                }
            )
        return normalized

    def _assess_quality_v2(
        self,
        payload: dict[str, Any],
        seed: dict[str, Any],
        min_scene_count: int,
        max_scene_count: int,
    ) -> list[str]:
        """评估输出质量并返回问题列表"""
        issues: list[str] = []

        scenes_obj = payload.get("key_scenes", [])
        scenes = scenes_obj if isinstance(scenes_obj, list) else []
        if len(scenes) < min_scene_count or len(scenes) > max_scene_count:
            issues.append(
                f"key_scenes 数量不在范围内，当前 {len(scenes)}，期望 {min_scene_count}-{max_scene_count}"
            )

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                issues.append(f"scene {index} 结构非法")
                continue

            summary = str(scene.get("summary", "")).strip()
            if len(summary) < 18:
                issues.append(f"scene {index} 的 summary 过短，需至少18字")

            dialogues_obj = scene.get("sample_dialogue", [])
            dialogues = dialogues_obj if isinstance(dialogues_obj, list) else []
            sample_dialogue_count = getattr(conf, "SAMPLE_DIALOGUE_COUNT", 6)
            valid_dialogues = [d for d in dialogues if str(d).strip()]
            if len(valid_dialogues) < sample_dialogue_count:
                issues.append(f"scene {index} 的 sample_dialogue 不足{sample_dialogue_count}句，当前{len(valid_dialogues)}句")

            video_clip = scene.get("video_clip", {})
            if not isinstance(video_clip, dict):
                issues.append(f"scene {index} 缺少 video_clip")
                continue

            start: str = video_clip.get("start", "")
            end: str = video_clip.get("end", "")
            if not isinstance(start, str) or not isinstance(end, str):
                issues.append(f"scene {index} 的 video_clip.start/end 必须为字符串格式")
            elif not re.match(r"\d{2}:\d{2}:\d{2}\.\d", start) or not re.match(r"\d{2}:\d{2}:\d{2}\.\d", end):
                issues.append(f"scene {index} 的 video_clip 格式错误，应为 HH:MM:SS.m 格式")

            if index > 1:
                prev_scene = scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}
                prev_clip = prev_scene.get("video_clip", {}) if isinstance(prev_scene, dict) else {}
                prev_end = prev_clip.get("end", "") if isinstance(prev_clip, dict) else ""
                if start and prev_end and start <= prev_end:
                    issues.append(f"scene {index} 时间戳不递增：当前 start={start}, 前一 end={prev_end}")

            if index > 1:
                prev_summary = str((scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}).get("summary", "")).strip()
                current_summary = str(scene.get("summary", "")).strip()
                if prev_summary and current_summary and self._text_similarity(prev_summary, current_summary) >= 0.85:
                    issues.append(f"scene {index - 1} 与 scene {index} summary 过于相似，需去重并推进剧情")

        return issues

    def _text_similarity(self, left: str, right: str) -> float:
        """简单字符级相似度，用于去重检查。"""
        left_set = {ch for ch in left if not ch.isspace()}
        right_set = {ch for ch in right if not ch.isspace()}
        if not left_set or not right_set:
            return 0.0
        inter = len(left_set & right_set)
        union = len(left_set | right_set)
        if union == 0:
            return 0.0
        return inter / union
