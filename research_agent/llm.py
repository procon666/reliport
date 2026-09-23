"""DeepSeek LLM 客户端封装（OpenAI 兼容接口）。
提供对话、结构化 JSON 输出两种能力。
"""
import json
import logging
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def _client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise LLMError("未安装 openai SDK，请先 pip install openai") from e

    if not settings.is_llm_ready():
        raise LLMError("未配置 DEEPSEEK_API_KEY，请在 config 或环境变量中设置")

    return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.base_url)


def chat(
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """普通对话，返回文本。"""
    client = _client()
    try:
        resp = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("DeepSeek 调用失败: %s", e)
        raise LLMError(f"DeepSeek 调用失败: {e}") from e


def chat_json(
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict:
    """对话并要求返回 JSON，自动解析。失败时抛错。"""
    text = chat(
        system=system + "\n\n你必须只输出一个合法的 JSON 对象，不要输出任何其他内容。",
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    """宽容解析 JSON：去掉可能的代码块围栏和多余文本。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # 尝试直接解析；失败则截取首个 { 到末个 }
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(f"无法解析模型输出的 JSON: {text[:200]}")
