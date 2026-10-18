"""Provider-neutral JSON/vision interface; credentials belong to one explicit profile."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import http.client
import os
from pathlib import Path
from typing import Any, Callable, Protocol
import urllib.error
import urllib.parse
import urllib.request


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
                 transport: Callable[[dict], dict] | None = None):
        self.profile, self.root, self.transport = profile, root.resolve(), transport

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

    def complete(self, *, system: str, prompt: str, images: list[ImageInput]) -> tuple[dict, dict]:
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
                    result = json.load(response)
            except urllib.error.HTTPError as exc:
                raise ModelError(f"Model HTTP {exc.code}; not automatically retried",
                                 receipt={**transport_receipt, "http_status": exc.code}) from None
            except (OSError, ValueError, http.client.HTTPException):
                raise ModelError("Model transport failed; billing outcome may be unknown", receipt=transport_receipt) from None
        if not isinstance(result, dict):
            raise ModelError("Model returned an invalid response envelope")
        receipt = {"requested_model": self.profile.model, "model": result.get("model", self.profile.model), "id": result.get("id"),
                   "system_fingerprint": result.get("system_fingerprint"),
                   "usage": result.get("usage", {}),
                   "environment_variables_used": [self.profile.api_key_env] if self.profile.api_key_env else []}
        try:
            choice = result["choices"][0]
            receipt["finish_reason"] = choice.get("finish_reason")
            if choice.get("finish_reason") != "stop":
                raise ModelError("Model response incomplete", receipt=receipt)
            answer = json.loads(choice["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelError("Model returned invalid JSON contract", receipt=receipt) from None
        return answer, receipt


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode("utf-8")).hexdigest()
