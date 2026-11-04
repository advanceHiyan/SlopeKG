"""Official limits for configured providers, checked 2026-10-31.

These are transport limits, independent of documents, prompts and task budgets.
The request-count budget remains separate from the maximum output allowance.
"""
from urllib.parse import urlsplit

DEEPSEEK_MAX_OUTPUT_TOKENS = 393216
QWEN_MAX_OUTPUT_TOKENS = 131072
KIMI_CONTEXT_TOKENS = 1048576

LIMIT_SOURCES = {
    'deepseek': 'https://api-docs.deepseek.com/api/create-chat-completion/',
    'qwen': 'https://help.aliyun.com/zh/model-studio/qwen3-8-max',
    'kimi': 'https://www.kimi.com/help/kimi-api/api-troubleshooting',
    'kimi_token_count': 'https://platform.kimi.com/docs/api/estimate',
}


def provider_output_ceiling(base_url, model):
    host = (urlsplit(base_url).hostname or '').lower()
    if host == 'api.deepseek.com' and model in {'deepseek-flash', 'deepseek-v4-pro'}:
        return DEEPSEEK_MAX_OUTPUT_TOKENS
    if (host.endswith('.aliyuncs.com') and model in
            {'qwen3.8-max', 'qwen3.8-max-0902', 'qwen3.8-max-2026-09-02'}):
        return QWEN_MAX_OUTPUT_TOKENS
    if kimi_counts_input(base_url, model):
        return KIMI_CONTEXT_TOKENS
    return None  # Unknown providers retain their explicit, validated profile.


def kimi_counts_input(base_url, model):
    return ((urlsplit(base_url).hostname or '').lower() in {'api.moonshot.cn', 'api.moonshot.ai'}
            and model == 'kimi-k3')
