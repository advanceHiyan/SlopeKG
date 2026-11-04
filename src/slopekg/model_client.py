"""Provider-neutral JSON/vision interface; credentials belong to one explicit profile."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import http.client
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
from typing import Any, Callable, Protocol
import urllib.error
import urllib.parse
import urllib.request

from .model_limits import provider_output_ceiling, kimi_counts_input, KIMI_CONTEXT_TOKENS


class ModelError(RuntimeError):
    """Safe to persist: never contains response bodies, prompts or credentials."""

    def __init__(self, message: str, *, receipt: dict | None = None):
        super().__init__(message)
        self.receipt = receipt


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    credential_endpoint: str
    api_key_file: str = ""
    api_key_env: str = ""
    api_key_field: str = ""
    token_parameter: str = "max_completion_tokens"
    max_output_tokens: int = 131072
    timeout_seconds: int = 600
    request_options: dict[str, Any] = field(default_factory=dict)
    image_options: dict[str, Any] = field(default_factory=dict)
    image_url_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        url = urllib.parse.urlsplit(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or self.base_url.rstrip("/") != self.credential_endpoint.rstrip("/")):
            raise ValueError("Profile requires HTTPS and an exactly matching credential_endpoint")
        if bool(self.api_key_file) == bool(self.api_key_env):
            raise ValueError("Specify exactly one project key file or explicit environment variable")
        if self.token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported output token parameter")
        if not self.model or not 1 <= self.max_output_tokens <= 1048576 or not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("Invalid model or output/timeout budget")
        ceiling = provider_output_ceiling(self.base_url, self.model)
        if ceiling is not None:
            # One policy at construction also covers dataclasses.replace and
            # future callers that accidentally reintroduce small output caps.
            object.__setattr__(self, 'max_output_tokens', ceiling)
        reserved = {"model", "messages", "stream", "max_tokens", "max_completion_tokens", "response_format"}
        if reserved & self.request_options.keys():
            raise ValueError("request_options cannot replace transport contract fields")
        if {"type", "image_url"} & self.image_options.keys() or "url" in self.image_url_options:
            raise ValueError("Image options cannot replace image content")
        json.dumps(self.request_options, allow_nan=False)

    @classmethod
    def load(cls, path: Path) -> "ModelProfile":
        return cls(**json.loads(path.read_text(encoding="utf-8-sig")))

    def identity(self) -> dict:
        # Key paths and values never enter model prompts or result metadata.
        return {"name": self.name, "base_url": self.base_url.rstrip("/"), "model": self.model,
                "token_parameter": self.token_parameter, "max_output_tokens": self.max_output_tokens,
                "request_options": self.request_options, "image_options": self.image_options,
                "image_url_options": self.image_url_options}


@dataclass(frozen=True)
class ImageInput:
    path: Path
    label: str
    width: int
    height: int


class JsonVisionModel(Protocol):
    def identity(self) -> dict: ...

    def complete(self, *, system: str, prompt: str, images: list[ImageInput]) -> tuple[dict, dict]: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("Model endpoint redirect refused")


class CompatibleVisionClient:
    """OpenAI-compatible wire format, without assuming identical provider options.

    Calls use the orchestrator's bounded concurrency. Unknown
    billable outcomes are never retried automatically. A transport is injectable
    for offline contract tests and other model gateways.
    """

    def __init__(self, profile: ModelProfile, root: Path,
                 transport: Callable[[dict], dict] | None = None,
                 token_counter: Callable[[dict], int] | None = None):
        self.profile, self.root, self.transport = profile, root.resolve(), transport
        self.token_counter = token_counter

    def identity(self) -> dict:
        return self.profile.identity()

    def _key(self) -> str:
        if self.profile.api_key_env:
            value = os.environ.get(self.profile.api_key_env, "").strip()
        else:
            path = (self.root / self.profile.api_key_file).resolve()
            if not path.is_relative_to(self.root / ".secrets"):
                raise ModelError("Project key file must stay inside project .secrets")
            try:
                value = path.read_text(encoding="utf-8-sig").strip()
            except OSError:
                raise ModelError("Configured project key file is unavailable") from None
            if self.profile.api_key_field:
                values = dict(line.split("=", 1) for line in value.splitlines()
                              if "=" in line and not line.lstrip().startswith("#"))
                value = values.get(self.profile.api_key_field, "").strip().strip("\"'")
        if not value or any(c.isspace() for c in value):
            raise ModelError("Configured project key is missing or malformed")
        return value

    def complete(self, *, system: str, prompt: str, images: list[ImageInput], debug_callback=None) -> tuple[dict, dict]:
        content = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image.path.read_bytes()).decode("ascii")
            content.extend([{"type": "text", "text": image.label},
                            {"type": "image_url", **self.profile.image_options,
                             "image_url": {"url": "data:image/png;base64," + encoded,
                                           **self.profile.image_url_options}}])
        payload = {"model": self.profile.model, "stream": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content if images else prompt}],
                   "response_format": {"type": "json_object"},
                   self.profile.token_parameter: self.profile.max_output_tokens,
                   **self.profile.request_options}
        input_tokens = None
        if kimi_counts_input(self.profile.base_url, self.profile.model):
            input_tokens = self._count_input_tokens(payload['messages'])
            payload[self.profile.token_parameter] = KIMI_CONTEXT_TOKENS - input_tokens
        if debug_callback is not None:
            debug_callback(payload, None)
        if self.transport:
            result = self.transport(payload)
        else:
            transport_receipt = {"model": self.profile.model,
                "environment_variables_used": [self.profile.api_key_env] if self.profile.api_key_env else []}
            request = urllib.request.Request(self.profile.base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._key()})
            try:
                # TLS verification stays enabled; no machine proxy/account fallback.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                with opener.open(request, timeout=self.profile.timeout_seconds) as response:
                    if debug_callback is not None:
                        response_bytes = response.read()
                        response_text = response_bytes.decode('utf-8', errors='replace')
                        debug_callback(payload, None, raw_response_body=response_text,
                                       raw_response_base64=base64.b64encode(response_bytes).decode('ascii'))
                        # Match json.load(response), including BOM/UTF-16
                        # detection; debug text decoding must not change parsing.
                        result = json.loads(response_bytes)
                    else:
                        result = json.load(response)
            except urllib.error.HTTPError as exc:
                if debug_callback is not None:
                    error_bytes = exc.read()
                    error_body = error_bytes.decode('utf-8', errors='replace')
                    debug_callback(payload, {'http_status': exc.code}, raw_response_body=error_body,
                                   raw_response_base64=base64.b64encode(error_bytes).decode('ascii'))
                delay = (exc.headers.get('Retry-After', '') or '').strip() if exc.headers else ''
                retry_after = None
                try:
                    retry_after = max(0.0, float(delay)) if delay.replace('.', '', 1).isdigit() else max(
                        0.0, (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds()) if delay else None
                except (TypeError, ValueError, OverflowError):
                    pass
                raise ModelError(f"Model HTTP {exc.code}", receipt={**transport_receipt,
                    "http_status": exc.code, "retry_after_seconds": retry_after}) from None
            except (OSError, ValueError, http.client.HTTPException):
                raise ModelError("Model transport failed; billing outcome may be unknown", receipt=transport_receipt) from None
        # Opt-in local debug capture happens before parsing/validation, so an
        # invalid JSON content or a length stop retains the provider's reply.
        # Neither Authorization headers nor credential values are included.
        if debug_callback is not None:
            debug_callback(payload, result)
        if not isinstance(result, dict):
            raise ModelError("Model returned an invalid response envelope")
        receipt = {"requested_model": self.profile.model, "model": result.get("model", self.profile.model), "id": result.get("id"),
                   "system_fingerprint": result.get("system_fingerprint"),
                   "usage": result.get("usage", {}),
                   "environment_variables_used": [self.profile.api_key_env] if self.profile.api_key_env else [],
                   'requested_max_output_tokens': payload[self.profile.token_parameter],
                   'input_token_count': input_tokens, 'token_count_requests': int(input_tokens is not None),
                   'generation_request_sent': True, 'response_received': True}
        choices=result.get('choices')
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
            receipt.update(failure_kind='invalid_response_envelope',response_diagnostic={
                'choices_type':type(choices).__name__,'choices_count':len(choices) if isinstance(choices,list) else None})
            raise ModelError('Model returned invalid JSON contract',receipt=receipt)
        choice=choices[0]
        receipt['finish_reason']=choice.get('finish_reason')
        message=choice.get('message')
        body=message.get('content') if isinstance(message,dict) else None
        diagnostic={'message_type':type(message).__name__,'content_type':type(body).__name__,
                    'content_present':isinstance(message,dict) and 'content' in message}
        if isinstance(body,str):
            diagnostic.update(content_chars=len(body),content_blank=not body.strip(),
                content_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest())
        # Diagnostics describe shape and parser position, never source text,
        # reasoning, prompts or credentials. Receipts remain safe to display.
        if choice.get('finish_reason')!='stop':
            receipt.update(failure_kind='output_truncated' if choice.get('finish_reason')=='length'
                           else 'incomplete_response',response_diagnostic=diagnostic)
            raise ModelError('Model output truncated at provider limit' if choice.get('finish_reason')=='length'
                             else 'Model response incomplete',receipt=receipt)
        try:
            answer=json.loads(body)
            diagnostic['parsed_top_level_type']=type(answer).__name__
            if not isinstance(answer,dict):
                raise ValueError('JSON root must be an object')
        except (TypeError,ValueError) as exc:
            diagnostic['error_type']=type(exc).__name__
            if isinstance(exc,json.JSONDecodeError):
                diagnostic.update(json_error_position=exc.pos,json_error_line=exc.lineno,json_error_column=exc.colno)
            receipt.update(failure_kind='invalid_json_contract',response_diagnostic=diagnostic)
            raise ModelError('Model returned invalid JSON contract',receipt=receipt) from None
        return answer, receipt

    def _count_input_tokens(self, messages):
        """Kimi's maximum is its remaining context, using its own tokenizer."""
        counter = getattr(self, 'token_counter', None)
        if counter is not None:
            tokens = counter({'model': self.profile.model, 'messages': messages})
        else:
            if self.transport is not None:
                raise ModelError('Injected Kimi transport needs an injected token_counter',
                                 receipt={'operation': 'token_count', 'generation_request_sent': False})
            request = urllib.request.Request(
                self.profile.base_url.rstrip('/') + '/tokenizers/estimate-token-count',
                data=json.dumps({'model': self.profile.model, 'messages': messages}, ensure_ascii=False).encode('utf-8'),
                method='POST', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._key()})
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                with opener.open(request, timeout=self.profile.timeout_seconds) as response:
                    tokens = json.load(response)['data']['total_tokens']
            except urllib.error.HTTPError as exc:
                raise ModelError('Token count HTTP ' + str(exc.code), receipt={'operation': 'token_count',
                    'http_status': exc.code, 'generation_request_sent': False}) from None
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
                raise ModelError('Token count unavailable; generation request not sent',
                                 receipt={'operation': 'token_count', 'generation_request_sent': False}) from None
        if type(tokens) is not int or not 0 <= tokens < KIMI_CONTEXT_TOKENS:
            raise ModelError('Input exceeds Kimi context or invalid token count; generation request not sent',
                             receipt={'operation': 'token_count', 'generation_request_sent': False})
        return tokens


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode("utf-8")).hexdigest()
