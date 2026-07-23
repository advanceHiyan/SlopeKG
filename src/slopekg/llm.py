from __future__ import annotations

import os
import json
import urllib.request
from pathlib import Path
from typing import Any

from .config import ROOT


# DeepSeek V4 documents a 384K maximum output. Keep this provider-specific
# limit in one place so callers do not silently use smaller, scattered values.
DEEPSEEK_MAX_OUTPUT_TOKENS = 384 * 1024


def deepseek_key_info() -> tuple[str | None, str | None]:
    """Return the key and its non-sensitive source label.

    The repository-local ignored secret intentionally wins over machine-wide
    environment variables so this project cannot accidentally use another
    project's account or an older key.
    """
    candidates = [
        (ROOT / ".secrets" / "deepseek_api_key.txt", "project_secret_file"),
        (Path.home() / ".deepseek_api_key", "user_secret_file"),
        (Path.home() / ".config" / "deepseek" / "api_key", "user_config_file"),
    ]
    for candidate, source in candidates:
        if candidate.exists():
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value, source
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_V4_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, f"environment:{name}"
    return None, None


def read_deepseek_key() -> str | None:
    return deepseek_key_info()[0]


def llm_status() -> dict[str, Any]:
    key, source = deepseek_key_info()
    return {
        "provider": "deepseek",
        "recommended_model": "deepseek-v4-flash",
        "enabled": False,
        "key_available": bool(key),
        "key_source": source,
        "max_output_tokens": DEEPSEEK_MAX_OUTPUT_TOKENS,
        "note": "大模型是可选深度解析层；基础解析可独立运行，只有通过原文证据校验的深度候选才会进入深度图谱。",
    }


def call_deepseek_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = "deepseek-v4-flash",
    max_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS,
    timeout: int = 300,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = read_deepseek_key()
    if not key:
        raise RuntimeError("DeepSeek API key is not configured")
    base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    metadata = {
        "model": result.get("model", model),
        "usage": result.get("usage", {}),
        "id": result.get("id"),
        "system_fingerprint": result.get("system_fingerprint"),
    }
    return parsed, metadata
