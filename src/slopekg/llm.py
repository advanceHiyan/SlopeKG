from __future__ import annotations

import os
import json
import urllib.request
from typing import Any

from .config import ROOT


# Official model/pricing documentation checked 2026-10-15. Legacy V4 Flash
# aliases now route to V4.1 Flash; use the canonical ID for new requests.
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_API_BASE = "https://api.deepseek.com"
# DeepSeek V4.1 documents a 384K maximum output. Keep this provider-specific
# limit in one place so callers do not silently use smaller, scattered values.
DEEPSEEK_MAX_OUTPUT_TOKENS = 384 * 1024


def deepseek_key_info() -> tuple[str | None, str | None]:
    """Return the key and its non-sensitive source label.

    This project uses its dedicated ignored secret only. A missing/empty file
    must not silently charge a machine-wide or another project's account.
    """
    candidate = ROOT / ".secrets" / "deepseek_api_key.txt"
    if candidate.is_file():
        value = candidate.read_text(encoding="utf-8").strip()
        if value:
            return value, "project_secret_file"
    return None, None


def read_deepseek_key() -> str | None:
    return deepseek_key_info()[0]


def llm_status() -> dict[str, Any]:
    key, source = deepseek_key_info()
    return {
        "provider": "deepseek",
        "recommended_model": DEFAULT_DEEPSEEK_MODEL,
        "model_version": "DeepSeek-V4.1-Flash",
        "provider_input_modalities": ["text", "image"],
        "pipeline_input_modalities": ["text"],
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
    model: str = DEFAULT_DEEPSEEK_MODEL,
    max_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS,
    timeout: int = 300,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = read_deepseek_key()
    if not key:
        raise RuntimeError("Project DeepSeek API key is missing: .secrets/deepseek_api_key.txt")
    base_url = os.environ.get("DEEPSEEK_API_BASE", DEEPSEEK_API_BASE).rstrip("/")
    if base_url not in {DEEPSEEK_API_BASE, DEEPSEEK_API_BASE + "/v1"}:
        raise ValueError("Project DeepSeek credentials may only be sent to https://api.deepseek.com")
    if not 1 <= max_tokens <= DEEPSEEK_MAX_OUTPUT_TOKENS:
        raise ValueError("max_tokens must be between 1 and 393216")
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
    # Do not forward project credentials if a server redirects the request.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError("DeepSeek API redirect refused")

    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise RuntimeError(f"DeepSeek response did not complete: {choice.get('finish_reason')}")
    content = choice["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek JSON response must be an object")
    metadata = {
        "requested_model": model,
        "model": result.get("model", model),
        "finish_reason": choice.get("finish_reason"),
        "usage": result.get("usage", {}),
        "id": result.get("id"),
        "system_fingerprint": result.get("system_fingerprint"),
    }
    return parsed, metadata
