"""
使用智增增API执行关键场景模式 Step 3（M3 信息采集）
完全匹配模块三逻辑：多轮对话 + 质量评估 + 修复机制
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import conf

ZHIZENGZENG_API_KEY = conf.M3_LLM_API_KEY
ZHIZENGZENG_BASE_URL = conf.M3_LLM_BASE_URL
DEFAULT_MODEL = conf.M3_LLM_MODEL
MAX_ROUNDS = 5  # 从3轮增加到5轮，给LLM更多修复机会
SAMPLE_DIALOGUE_COUNT = getattr(conf, "SAMPLE_DIALOGUE_COUNT", 6)
MIN_SCENE_SUMMARY_LENGTH = 50  # summary最小字数从18增加到50
PROMPTS_DIR = conf.PROMPTS_DIR


def _load_prompt(prompt_name: str, **kwargs) -> str:
    """从 prompts 目录读取指定模板，支持占位符替换。"""
    if not prompt_name.endswith('.txt'):
        prompt_name = f"{prompt_name}.txt"
    prompt_file = Path(PROMPTS_DIR) / prompt_name
    content = prompt_file.read_text(encoding="utf-8")
    
    for key, value in kwargs.items():
        placeholder = f"{{{key}}}"
        content = content.replace(placeholder, str(value))
    
    return content


def _safe_request(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
    params: dict[str, Any] | None = None,
    retries: int = 2,
) -> str | None:
    """执行安全的 HTTP GET 请求，异常时返回 None。"""
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, params=params)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as error:
            if attempt == retries:
                logging.warning("请求失败: %s (%s)", url, error)
                return None
    return None


def _safe_request_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
    params: dict[str, Any] | None = None,
    retries: int = 2,
) -> dict[str, Any] | list[Any] | None:
    """执行安全的 HTTP GET JSON 请求，异常时返回 None。"""
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, params=params)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            if attempt == retries:
                logging.warning("JSON 请求失败: %s (%s)", url, error)
                return None
    return None


def _clean_html_text(html: str) -> str:
    """清理 HTML 标签并返回纯文本。"""
    import re
    from html import unescape
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _estimate_scene_count(
    subtitles: list[dict[str, Any]] | None,
    min_scene_count: int,
    max_scene_count: int,
) -> int:
    """根据电影时长动态计算场景数量（总时长÷8分钟）。"""
    if not subtitles:
        # 默认按2小时电影计算：120÷8=15个场景
        return 15

    try:
        total_duration = float(subtitles[-1].get("end", 0.0))
    except (TypeError, ValueError, IndexError, AttributeError):
        total_duration = 0.0

    if total_duration <= 0:
        return 15

    # 计算：总时长(分钟) ÷ 8 = 目标场景数
    total_minutes = total_duration / 60.0
    estimated = int(round(total_minutes / 8.0))
    
    # 确保在合理范围内（最少10个，最多25个）
    return max(10, min(25, estimated))


def _extract_year(text: str) -> int:
    """从文本中提取四位年份。"""
    match = re.search(r"(19\d{2}|20\d{2})", text)
    if not match:
        return 0
    return int(match.group(1))


def _extract_synopsis(text: str, movie_name: str) -> str:
    """从清洗文本中抽取简介片段。"""
    anchor = text.find(movie_name)
    if anchor >= 0:
        snippet = text[anchor : anchor + 500]
    else:
        snippet = text[:500]
    return _normalize_text(snippet)


def _normalize_text(text: str) -> str:
    """清洗文本中的噪声信息。"""
    normalized = re.sub(r"\s+", " ", text).strip()
    for noise in ["登录/注册", "下载豆瓣客户端", "扫码直接下载", "豆瓣", "电影", "音乐", "读书"]:
        normalized = normalized.replace(noise, "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_better_synopsis(current: str, incoming: str) -> bool:
    """判断 incoming 简介是否优于 current。"""
    current_clean = _normalize_text(current)
    incoming_clean = _normalize_text(incoming)
    if not incoming_clean:
        return False
    if not current_clean:
        return True
    if len(incoming_clean) >= len(current_clean) + 20:
        return True
    if len(current_clean) < 40 <= len(incoming_clean):
        return True
    return False


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


def _format_timestamp(seconds: float) -> str:
    """将秒数转换为 HH:MM:SS.m 格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:04.1f}"


def _text_similarity(text1: str, text2: str) -> float:
    """计算两个文本的相似度"""
    if not text1 or not text2:
        return 0.0
    set1 = set(text1)
    set2 = set(text2)
    return len(set1 & set2) / len(set1 | set2)


def _search_douban(movie_name: str) -> dict[str, object]:
    """通过豆瓣搜索页面抓取基础信息。"""
    try:
        suggest_url = "https://movie.douban.com/j/subject_suggest"
        suggest_data = _safe_request_json(
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
        year = _extract_year(str(subject.get("year", "")))
        subject_url = str(subject.get("url") or "")
        if not subject_url:
            subject_id = str(subject.get("id") or "").strip()
            if subject_id:
                subject_url = f"https://movie.douban.com/subject/{subject_id}/"
        if not subject_url:
            return {"title": title, "year": year}

        html = _safe_request(subject_url, headers=conf.DOUBAN_HEADERS, timeout=15)
        if not html:
            return {"title": title, "year": year}

        soup = BeautifulSoup(html, "lxml")
        synopsis_node = soup.select_one('span[property="v:summary"]')
        synopsis = _normalize_text(synopsis_node.get_text(" ", strip=True) if synopsis_node else "")

        genre_nodes = soup.select('span[property="v:genre"]')
        genres = [_normalize_text(node.get_text(" ", strip=True)) for node in genre_nodes]
        genres = [item for item in genres if item]

        starring_nodes = soup.select('a[rel="v:starring"]')
        characters = [_normalize_text(node.get_text(" ", strip=True)) for node in starring_nodes]
        characters = [item for item in characters if item]

        if not year:
            year_node = soup.select_one("span.year")
            year = _extract_year(year_node.get_text(" ", strip=True) if year_node else "")

        return {
            "title": title,
            "year": year,
            "genre": genres,
            "characters": characters[:12],
            "synopsis": synopsis,
        }
    except (ValueError, RuntimeError, requests.RequestException) as error:
        logging.warning("豆瓣搜索失败: %s", error)
        return {}


def _search_baidu_baike(movie_name: str) -> dict[str, object]:
    """通过百度百科页面抓取简介信息。"""
    try:
        url = f"https://baike.baidu.com/item/{quote(movie_name)}"
        html = _safe_request(url, headers=conf.DOUBAN_HEADERS, timeout=15)
        if not html:
            return {}

        soup = BeautifulSoup(html, "lxml")
        summary_node = soup.select_one("div.lemma-summary")
        synopsis = ""
        if summary_node:
            synopsis = _normalize_text(summary_node.get_text(" ", strip=True))
        if not synopsis:
            text = _clean_html_text(html)
            synopsis = _extract_synopsis(text, movie_name)
        return {
            "title": movie_name,
            "synopsis": synopsis,
        }
    except (ValueError, RuntimeError, requests.RequestException) as error:
        logging.warning("百度百科搜索失败: %s", error)
        return {}


def _search_tmdb(movie_name: str, year: int | None = None) -> dict[str, object]:
    """使用 TMDb API 查询电影信息。"""
    try:
        if not conf.TMDB_API_KEY:
            logging.info("未配置 TMDB_API_KEY，跳过 TMDb 采集")
            return {}

        query_url = f"{conf.TMDB_BASE_URL}/search/movie"
        params: dict[str, str | int] = {
            "api_key": conf.TMDB_API_KEY,
            "query": movie_name,
            "language": "zh-CN",
        }
        if year:
            params["year"] = year

        data = _safe_request_json(query_url, params=params, timeout=15, retries=2)
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
        parsed_year = _extract_year(release_date)

        genres: list[str] = []
        characters: list[str] = []
        if movie_id:
            detail_url = f"{conf.TMDB_BASE_URL}/movie/{movie_id}"
            detail_data = _safe_request_json(
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
            "synopsis": _normalize_text(str(top.get("overview", "") or "")),
            "genre": genres,
            "characters": characters,
        }
    except (ValueError, RuntimeError, requests.RequestException, KeyError) as error:
        logging.warning("TMDb 查询失败: %s", error)
        return {}


def _search_imdb_omdb(movie_name: str) -> dict[str, object]:
    """使用 IMDb Suggest + OMDb 兜底采集。"""
    try:
        suggest_url = f"https://v2.sg.media-imdb.com/suggestion/x/{quote(movie_name)}.json"
        suggest_data = _safe_request_json(suggest_url, headers=conf.DOUBAN_HEADERS, timeout=15, retries=2)
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
        parsed_year = int(year_value) if isinstance(year_value, int) else _extract_year(str(year_value or ""))

        actors_text = str(top.get("s", "")).strip()
        characters = [item.strip() for item in actors_text.split(",") if item.strip()]

        if not conf.OMDB_API_KEY or not imdb_id:
            return {
                "title": title or movie_name,
                "year": parsed_year,
                "characters": characters,
            }

        omdb_data = _safe_request_json(
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

        plot = _normalize_text(str(omdb_data.get("Plot", "")).strip())
        if len(plot) > 700:
            plot = plot[:700].rstrip()

        omdb_year = _extract_year(str(omdb_data.get("Year", "")))
        return {
            "title": str(omdb_data.get("Title") or title or movie_name),
            "year": omdb_year or parsed_year,
            "genre": genres,
            "characters": characters,
            "synopsis": plot,
        }
    except (ValueError, RuntimeError, requests.RequestException, KeyError):
        return {}


def _collect_search_context_by_keywords(
    movie_name: str,
    keywords: list[str] | None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    """关键词联网检索：代码抓取，再交给 LLM 归纳。"""
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
            source_results.append(("douban", _search_douban(term)))
        except (ValueError, RuntimeError, requests.RequestException):
            source_results.append(("douban", {}))

        try:
            source_results.append(("baidu_baike", _search_baidu_baike(term)))
        except (ValueError, RuntimeError, requests.RequestException):
            source_results.append(("baidu_baike", {}))

        try:
            source_results.append(("tmdb", _search_tmdb(term, year=year if term == movie_name else None)))
        except (ValueError, RuntimeError, requests.RequestException):
            source_results.append(("tmdb", {}))

        try:
            source_results.append(("imdb_omdb", _search_imdb_omdb(term)))
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

            hits.append({
                "source": source,
                "title": title,
                "year": data.get("year", 0),
                "genre": data.get("genre", []),
                "characters": data.get("characters", []),
                "synopsis": synopsis,
            })

            if len(hits) >= max_hits:
                break

        if len(hits) >= max_hits:
            break

    return hits


def _call_llm(model: str, messages: list[dict[str, str]], temperature: float = 0.3) -> dict[str, Any]:
    """调用智增增API"""
    from urllib import request
    import urllib.error

    url = ZHIZENGZENG_BASE_URL
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ZHIZENGZENG_API_KEY}",
    }

    req_payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(req_payload, ensure_ascii=False).encode("utf-8")

    last_error = None
    for attempt in range(3):
        try:
            req = request.Request(
                url,
                data=body,
                method="POST",
                headers=headers,
            )
            with request.urlopen(req, timeout=600) as resp:
                content = resp.read().decode("utf-8")
            parsed = json.loads(content)
            return parsed
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    raise RuntimeError(f"智增增API调用失败: {last_error}")


def _extract_json(text: str) -> dict[str, Any]:
    """从LLM响应中提取JSON"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)


def _time_to_seconds(time_str: str) -> float:
    """将 HH:MM:SS.m 格式转换为秒数"""
    try:
        parts = time_str.split(':')
        if len(parts) != 3:
            return 0.0
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, IndexError):
        return 0.0


def _assess_quality(
    scenes: list[dict],
    min_count: int,
    max_count: int,
    target_count: int,
) -> list[str]:
    """评估输出质量并返回建议列表（只检查时间戳递增和覆盖完整性）"""
    issues: list[str] = []
    MAX_GAP_SECONDS = 120  # 最大允许时间间隔：2分钟
    
    # 检查场景是否太粗略（鼓励细分）
    if len(scenes) < 10:
        issues.append(f"【建议】当前只有 {len(scenes)} 个场景，可能分得太粗略了。建议细分成更多场景（15-20个以上），让每个剧情事件都有独立的场景")

    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"scene {index} 结构需要调整")
            continue

        summary = str(scene.get("summary", "")).strip()
        if len(summary) < MIN_SCENE_SUMMARY_LENGTH:
            issues.append(f"scene {index} 的 summary 可以更详细一些（当前{len(summary)}字，建议{MIN_SCENE_SUMMARY_LENGTH}字以上）")

        dialogues_obj = scene.get("sample_dialogue", [])
        dialogues = dialogues_obj if isinstance(dialogues_obj, list) else []
        valid_dialogues = [d for d in dialogues if str(d).strip()]
        if len(valid_dialogues) < SAMPLE_DIALOGUE_COUNT:
            issues.append(f"scene {index} 的 sample_dialogue 可以补充到{SAMPLE_DIALOGUE_COUNT}句（当前{len(valid_dialogues)}句）")

        video_clip = scene.get("video_clip", {})
        if not isinstance(video_clip, dict):
            issues.append(f"scene {index} 需要补充 video_clip 信息")
            continue

        start: str = video_clip.get("start", "")
        end: str = video_clip.get("end", "")
        if not isinstance(start, str) or not isinstance(end, str):
            issues.append(f"scene {index} 的 video_clip.start/end 格式需要调整")
        elif not re.match(r"\d{2}:\d{2}:\d{2}\.\d", start) or not re.match(r"\d{2}:\d{2}:\d{2}\.\d", end):
            issues.append(f"scene {index} 的时间格式建议统一为 HH:MM:SS.m")
        else:
            # 检查场景时长，如果太长建议拆分
            duration = _time_to_seconds(end) - _time_to_seconds(start)
            if duration > 15 * 60:  # 超过15分钟建议拆分
                issues.append(f"【建议】scene {index} 时长较长（{duration/60:.1f}分钟），如果包含多个剧情事件，可以考虑拆分成2-3个场景")

        # 严格检查时间戳递增（硬性要求）
        if index > 1:
            prev_scene = scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}
            prev_clip = prev_scene.get("video_clip", {}) if isinstance(prev_scene, dict) else {}
            prev_end = prev_clip.get("end", "") if isinstance(prev_clip, dict) else ""
            if start and prev_end and start <= prev_end:
                issues.append(f"【必须修复】scene {index} 的时间戳必须递增：当前 start={start} 必须严格大于前一场景的 end={prev_end}")
            
            # 检查时间间隔
            if start and prev_end:
                gap = _time_to_seconds(start) - _time_to_seconds(prev_end)
                if gap > MAX_GAP_SECONDS:
                    issues.append(f"【建议】scene {index-1} 到 scene {index} 之间有时间间隔（{gap/60:.1f}分钟），可以在 {prev_end} 到 {start} 之间补充场景")

        if index > 1:
            prev_summary = str((scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}).get("summary", "")).strip()
            current_summary = str(scene.get("summary", "")).strip()
            if prev_summary and current_summary and _text_similarity(prev_summary, current_summary) >= 0.85:
                issues.append(f"scene {index - 1} 与 scene {index} 的剧情描述有些相似，可以尝试区分不同的剧情重点")

    return issues

    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"scene {index} 结构需要调整")
            continue

        summary = str(scene.get("summary", "")).strip()
        if len(summary) < MIN_SCENE_SUMMARY_LENGTH:
            issues.append(f"scene {index} 的 summary 可以更详细一些（当前{len(summary)}字，建议{MIN_SCENE_SUMMARY_LENGTH}字以上）")

        dialogues_obj = scene.get("sample_dialogue", [])
        dialogues = dialogues_obj if isinstance(dialogues_obj, list) else []
        valid_dialogues = [d for d in dialogues if str(d).strip()]
        if len(valid_dialogues) < SAMPLE_DIALOGUE_COUNT:
            issues.append(f"scene {index} 的 sample_dialogue 可以补充到{SAMPLE_DIALOGUE_COUNT}句（当前{len(valid_dialogues)}句）")

        video_clip = scene.get("video_clip", {})
        if not isinstance(video_clip, dict):
            issues.append(f"scene {index} 需要补充 video_clip 信息")
            continue

        start: str = video_clip.get("start", "")
        end: str = video_clip.get("end", "")
        if not isinstance(start, str) or not isinstance(end, str):
            issues.append(f"scene {index} 的 video_clip.start/end 格式需要调整")
        elif not re.match(r"\d{2}:\d{2}:\d{2}\.\d", start) or not re.match(r"\d{2}:\d{2}:\d{2}\.\d", end):
            issues.append(f"scene {index} 的时间格式建议统一为 HH:MM:SS.m")

        # 检查时间戳递增
        if index > 1:
            prev_scene = scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}
            prev_clip = prev_scene.get("video_clip", {}) if isinstance(prev_scene, dict) else {}
            prev_end = prev_clip.get("end", "") if isinstance(prev_clip, dict) else ""
            if start and prev_end and start <= prev_end:
                issues.append(f"scene {index} 的时间建议调整：当前 start={start} 应该晚于前一场景的 end={prev_end}")
            
            # 检查时间间隔
            if start and prev_end:
                gap = _time_to_seconds(start) - _time_to_seconds(prev_end)
                if gap > MAX_GAP_SECONDS:
                    issues.append(f"scene {index-1} 到 scene {index} 之间有时间间隔（{gap/60:.1f}分钟），可以考虑在 {prev_end} 到 {start} 之间补充一个场景，让剧情更连贯")

        if index > 1:
            prev_summary = str((scenes[index - 2] if isinstance(scenes[index - 2], dict) else {}).get("summary", "")).strip()
            current_summary = str(scene.get("summary", "")).strip()
            if prev_summary and current_summary and _text_similarity(prev_summary, current_summary) >= 0.85:
                issues.append(f"scene {index - 1} 与 scene {index} 的剧情描述有些相似，可以尝试区分不同的剧情重点")

    return issues


def _normalize_output(result: dict[str, Any], min_count: int, max_count: int, target_count: int) -> dict[str, Any]:
    """标准化输出（匹配模块三的 _normalize_output_v2）"""
    scenes_obj = result.get("key_scenes", [])
    scenes_list = scenes_obj if isinstance(scenes_obj, list) else []
    proposed_count = len(scenes_list) if scenes_list else target_count
    scene_count = max(min_count, min(max_count, proposed_count))
    expected_ids = list(range(1, scene_count + 1))

    # 处理不同格式
    key_scenes = result.get("key_scenes", [])
    if not key_scenes and "movie_info" in result:
        key_scenes = result.get("movie_info", {}).get("key_scenes", [])

    normalized_scenes = []
    for i, scene in enumerate(key_scenes, 1):
        if i > scene_count:
            break
        normalized_scenes.append({
            "scene_id": scene.get("scene_id", i),
            "phase": scene.get("phase", ""),
            "summary": scene.get("summary", ""),
            "sample_dialogue": (scene.get("sample_dialogue", []) or [])[:SAMPLE_DIALOGUE_COUNT],
            "video_clip": {
                "start": scene.get("video_clip", {}).get("start", "00:00:00.0"),
                "end": scene.get("video_clip", {}).get("end", "00:00:00.0"),
            }
        })

    # 填充空场景
    while len(normalized_scenes) < min_count:
        normalized_scenes.append({
            "scene_id": len(normalized_scenes) + 1,
            "phase": "",
            "summary": "",
            "sample_dialogue": [""] * SAMPLE_DIALOGUE_COUNT,
            "video_clip": {"start": "00:00:00.0", "end": "00:00:00.0"},
        })

    return {"key_scenes": normalized_scenes[:max_count]}


def _build_initial_messages(
    subtitles: list[dict],
    movie_name: str,
    min_count: int,
    max_count: int,
    target_count: int,
    search_context: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """构建初始LLM消息（匹配模块三的 _build_llm_messages）"""

    formatted_subtitles = []
    for sub in subtitles:
        formatted_subtitles.append({
            "start": _format_timestamp(sub.get("start", 0)),
            "end": _format_timestamp(sub.get("end", 0)),
            "text": sub.get("text", "")
        })

    subtitles_json = json.dumps(formatted_subtitles, ensure_ascii=False, indent=2)

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
        min_scene_count=min_count,
        max_scene_count=max_count,
        target_scene_count=target_count,
        sample_dialogue_count=SAMPLE_DIALOGUE_COUNT,
        search_context_json=json.dumps(search_context or [], ensure_ascii=False, indent=2),
        subtitles_json=subtitles_json,
        output_schema_json=json.dumps(output_schema, ensure_ascii=False, indent=2),
        issues_json="[]",
        draft_json="{}",
    )

    return [
        {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
        {"role": "user", "content": prompt},
    ]


def _build_revision_messages(
    subtitles: list[dict],
    movie_name: str,
    draft: dict,
    issues: list[str],
    min_count: int,
    max_count: int,
    target_count: int,
    search_context: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """构建修复消息（增强版：增加填补场景的具体指令）"""

    formatted_subtitles = []
    for sub in subtitles:
        formatted_subtitles.append({
            "start": _format_timestamp(sub.get("start", 0)),
            "end": _format_timestamp(sub.get("end", 0)),
            "text": sub.get("text", "")
        })

    subtitles_json = json.dumps(formatted_subtitles, ensure_ascii=False, indent=2)
    draft_json = json.dumps(draft, ensure_ascii=False, indent=2)
    issues_json = json.dumps(issues, ensure_ascii=False, indent=2)
    
    # 分析问题，提供建设性建议
    suggestions = []
    
    # 检查时间间隔建议
    for issue in issues:
        if "之间有时间间隔" in issue:
            suggestions.append(f"💡 {issue}")
            suggestions.append("  建议：查看该时间段内的字幕，找出其中的剧情事件，创建一个或多个场景来填补这个间隔")
    
    # 检查场景数量建议
    for issue in issues:
        if "可以考虑增加" in issue or "可以考虑减少" in issue:
            suggestions.append(f"💡 {issue}")
    
    # 检查summary长度建议
    for issue in issues:
        if "可以更详细一些" in issue:
            suggestions.append(f"💡 {issue}")
            suggestions.append("  建议：补充该场景中的具体情节、人物动作、对话背景等细节")
    
    # 构建额外的建议
    additional_suggestions = ""
    if suggestions:
        additional_suggestions = "\n\n【优化建议】\n根据当前的分段结果，有以下改进建议供参考：\n\n"
        for suggestion in suggestions:
            additional_suggestions += f"{suggestion}\n"
        additional_suggestions += "\n请根据这些建议调整分段，让电影的结构更加清晰完整。"

    prompt = _load_prompt(
        "m3_keyscene",
        mode="revision",
        min_scene_count=min_count,
        max_scene_count=max_count,
        target_scene_count=target_count,
        sample_dialogue_count=SAMPLE_DIALOGUE_COUNT,
        output_schema_json="{}",
        search_context_json=json.dumps(search_context or [], ensure_ascii=False, indent=2),
        subtitles_json=subtitles_json,
        draft_json=draft_json,
        issues_json=issues_json,
    )
    
    # 在prompt后追加建议
    prompt += additional_suggestions

    return [
        {"role": "system", "content": "你是电影结构化编剧助手，擅长输出严格 JSON。"},
        {"role": "user", "content": prompt},
    ]


def _generate_key_scenes(
    subtitles: list[dict],
    movie_name: str,
    model: str,
    min_count: int = 10,
    max_count: int = 20,
) -> list[dict]:
    """使用智增增API生成关键场景（完全匹配模块三的多轮逻辑）"""

    # 估算目标场景数
    target_count = _estimate_scene_count(subtitles, min_count, max_count)

    logging.info(f"目标场景数: {target_count} (范围: {min_count}-{max_count})")

    # 联网检索（使用电影名作为关键词）
    logging.info("开始联网检索上下文...")
    search_context = _collect_search_context_by_keywords(movie_name, None, None)
    logging.info(f"联网检索完成，获取 {len(search_context)} 条上下文")

    # 第一轮：构建初始消息
    current_messages = _build_initial_messages(
        subtitles, movie_name, min_count, max_count, target_count, search_context
    )

    result = {}
    normalized = {}

    for round_index in range(MAX_ROUNDS):
        logging.info(f"开始第 {round_index + 1}/{MAX_ROUNDS} 轮对话...")

        response = _call_llm(model, current_messages, temperature=0.3)
        content = response["choices"][0]["message"]["content"]
        result = _extract_json(content)

        normalized = _normalize_output(result, min_count, max_count, target_count)
        issues = _assess_quality(normalized.get("key_scenes", []), min_count, max_count, target_count)

        if not issues:
            logging.info(f"第 {round_index + 1} 轮通过质量检查")
            break

        if round_index == MAX_ROUNDS - 1:
            logging.warning(f"达到最大轮次，仍有 {len(issues)} 个问题未解决: {issues}")
            break

        # 构建修复消息
        logging.info(f"发现问题 {len(issues)} 个，构建修复消息...")
        current_messages = _build_revision_messages(
            subtitles, movie_name, normalized, issues, min_count, max_count, target_count, search_context
        )

    return normalized.get("key_scenes", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="使用智增增API执行关键场景模式 Step 3（完全匹配模块三逻辑）")
    parser.add_argument("--workspace", required=True, help="workspace 目录")
    parser.add_argument("--name", default="", help="电影名称（默认使用 workspace 目录名）")
    parser.add_argument("--srt", default="", help="SRT 文件路径（优先）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"使用的模型 (默认: {DEFAULT_MODEL})")
    parser.add_argument("--min-scenes", type=int, default=10, help="最小场景数 (默认: 10)")
    parser.add_argument("--max-scenes", type=int, default=20, help="最大场景数 (默认: 20)")
    parser.add_argument("--output", default="keyscenes_zhizengzeng.json", help="输出文件名 (默认: keyscenes_zhizengzeng.json)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace 不存在: {workspace}")

    movie_name = args.name.strip() or workspace.name
    subtitles = _load_subtitles(workspace, movie_name, args.srt)

    logging.info(f"加载字幕: {len(subtitles)} 条")

    # 生成关键场景（多轮对话 + 质量评估）
    key_scenes = _generate_key_scenes(
        subtitles,
        movie_name,
        args.model,
        min_count=args.min_scenes,
        max_count=args.max_scenes,
    )

    movie_info = {"key_scenes": key_scenes}

    # 保存结果
    output_path = workspace / args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(movie_info, f, ensure_ascii=False, indent=2)

    print(f"完成：{output_path}")
    print(f"关键场景数：{len(key_scenes)}")
    print(f"使用模型：{args.model}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())