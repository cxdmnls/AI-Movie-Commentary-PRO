"""
智增增API通用调用接口
支持 OpenAI 兼容格式调用（GPT、Gemini、Claude、DeepSeek 等）
"""
import json
import time
from typing import Any
from urllib import error, request

API_BASE_URL = "https://api.zhizengzeng.com/v1/chat/completions"
API_KEY = "sk-zk2069ef4bbde1703d22693c1979c24bae469002956da154"

DEFAULT_TIMEOUT = 600
DEFAULT_MAX_RETRIES = 3


def call_llm(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs
) -> dict[str, Any]:
    """
    调用智增增API（OpenAI兼容格式）

    Args:
        model: 模型名称，如 gpt-4o, gemini-2.5-flash, claude-sonnet-4-6, deepseek-chat 等
        messages: 消息列表 [{"role": "user", "content": "..."}]
        temperature: 采样温度 (0-2)
        max_tokens: 最大生成长度
        response_format: 响应格式，如 {"type": "json_object"}
        timeout: 超时时间（秒）
        max_retries: 最大重试次数

    Returns:
        API响应字典，包含 choices[0].message.content
    """
    url = API_BASE_URL
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    req_payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if max_tokens is not None:
        req_payload["max_tokens"] = max_tokens

    if response_format is not None:
        req_payload["response_format"] = response_format

    req_payload.update(kwargs)

    body = json.dumps(req_payload, ensure_ascii=False).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = request.Request(
                url,
                data=body,
                method="POST",
                headers=headers,
            )
            with request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8")
            parsed = json.loads(content)
            return parsed
        except (error.URLError, error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    raise RuntimeError(f"智增增API调用失败: {last_error}")


def call_llm_with_json_response(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """
    调用API并自动解析JSON响应

    Args:
        model: 模型名称
        messages: 消息列表
        temperature: 采样温度
        max_tokens: 最大生成长度
        timeout: 超时时间
        max_retries: 最大重试次数

    Returns:
        解析后的JSON字典
    """
    response = call_llm(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        timeout=timeout,
        max_retries=max_retries,
    )

    content = response["choices"][0]["message"]["content"]
    return extract_json(content)


def extract_json(text: str) -> dict[str, Any]:
    """
    从文本中提取JSON内容

    Args:
        text: 包含JSON的文本

    Returns:
        解析后的JSON字典
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    return json.loads(cleaned)


if __name__ == "__main__":
    result = call_llm(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": "你好，请用一句话介绍你自己"}
        ],
        temperature=0.7,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))