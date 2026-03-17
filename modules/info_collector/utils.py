from __future__ import annotations


import logging
import re
from html import unescape
from typing import Any

import requests

logger = logging.getLogger(__name__)


def safe_request(
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
                logger.warning("请求失败: %s (%s)", url, error)
                return None
    return None


def safe_request_json(
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
                logger.warning("JSON 请求失败: %s (%s)", url, error)
                return None
    return None


def clean_html_text(html: str) -> str:
    """清理 HTML 标签并返回纯文本。"""
    text = re.sub(r"<script[\\s\\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()
