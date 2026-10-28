"""Model dispatch, cache, concurrency and accounting, independent of PDF orchestration."""
import asyncio
import hashlib
import threading
from typing import Callable
from .model_client import ModelError, content_hash
from .storage import read_json, write_json
from .visual_contract import VERSION, SYSTEM, ContractError
from .visual_batching import compact


class VisualRuntime:
    def __init__(self, model, review_model, output_dir, settings, *, version=VERSION,
                 system=SYSTEM, review_stages=("blind_read", "verify", "audit"), recover_invalid_answers=False):
        self.model, self.review_model = model, review_model
        self.output_dir, self.settings = output_dir, settings
        self.version, self.system, self.review_stages = version, system, review_stages
        self.recover_invalid_answers = recover_invalid_answers
        self._state_lock = threading.Lock()
        self._request_locks = {}
        self.reset()

    def reset(self):
        self.calls = self.cache_hits = self.peak_concurrency = self._active_calls = 0
        self.receipts = []
        self._blocked_profiles = {}
        self._call_slots = asyncio.Semaphore(self.settings.model_concurrency)

    async def ask_async(self, *args):
        # Only network/cache work enters threads. All PyMuPDF operations and
        # shared document updates stay on the event-loop thread.
        async with self._call_slots:
            return await asyncio.to_thread(self.ask, *args)


    def ask(self, stage: str, instruction: str, context: dict, images: list, validate: Callable) -> dict:
        model = self.review_model if stage in self.review_stages else self.model
        prompt = instruction + "\n输入：" + compact(context)
        if len(prompt) + len(self.system) > self.settings.max_prompt_chars:
            raise ContractError("Prompt budget exceeded; request was not sent")
        if (len(images) > self.settings.max_images
                or sum(i.width * i.height for i in images) > self.settings.max_image_pixels
                or sum(i.path.stat().st_size for i in images) > self.settings.max_image_bytes):
            raise ContractError("Image budget exceeded; request was not sent")
        identity = {"version": self.version, "stage": stage, "model": model.identity(),
                    "system": self.system, "prompt": prompt,
                    "images": [{"label": i.label, "sha256": hashlib.sha256(i.path.read_bytes()).hexdigest()}
                               for i in images]}
        key = content_hash(identity)
        with self._state_lock:
            request_lock = self._request_locks.setdefault(key, threading.Lock())
        with request_lock:
            return self._cached_request(key, stage, model, prompt, images, validate)


    def _cached_request(self, key, stage, model, prompt, images, validate):
        path = self.output_dir / "cache" / (key + ".json")
        cached = read_json(path, {})
        if (self.recover_invalid_answers and cached.get('status') == 'failed'
                and isinstance(cached.get('invalid_answer'), dict) and cached.get('receipt')):
            answer = validate(cached['invalid_answer'])
            write_json(path, {**cached, 'status': 'completed', 'answer': answer,
                'revalidated_without_request': True, 'original_error': cached.get('error')})
            with self._state_lock:
                self.cache_hits += 1
                self.receipts.append({'stage': stage, 'cache_key': key, 'cache_hit': True,
                    'revalidated_without_request': True, **cached['receipt']})
            return answer
        if cached.get("status") == "completed":
            answer = validate(cached["answer"])
            with self._state_lock:
                self.cache_hits += 1
                self.receipts.append({"stage": stage, "cache_key": key, "cache_hit": True, **cached["receipt"]})
            return answer
        if cached and not self.settings.retry_failed:
            raise ModelError("Previous failed/interrupted call requires explicit retry_failed; not charged again")
        with self._state_lock:
            profile_key=content_hash(model.identity())
            if not self.settings.allow_model_calls:
                raise ModelError("Cache-only run; no model request sent")
            if profile_key in self._blocked_profiles:
                raise ModelError("Model profile blocked after " + self._blocked_profiles[profile_key] + "; no new request sent")
            if self.calls >= self.settings.max_calls:
                raise ModelError("Run call budget exhausted; unfinished work retained")
            self.calls += 1
        previous_attempts = cached.get("attempts", [])
        marker = {"status": "started", "stage": stage, "cache_key": key,
                  "attempts": previous_attempts + [{"status": "started"}]}
        write_json(path, marker)  # Persist before transmitting a potentially billable request.
        raw, receipt = None, None
        try:
            with self._state_lock:
                self._active_calls += 1
                self.peak_concurrency = max(self.peak_concurrency, self._active_calls)
            try:
                raw, receipt = model.complete(system=self.system, prompt=prompt, images=images)
            finally:
                with self._state_lock:
                    self._active_calls -= 1
            receipt = {**receipt, "requested_model_profile": model.identity()}
            answer = validate(raw)
        except Exception as exc:
            # Runtime errors from injected clients can contain secrets; persist types only.
            safe = str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__
            marker.update(status="failed", error=safe)
            marker["attempts"][-1] = {"status": "failed", "error": safe}
            receipt = receipt or getattr(exc, "receipt", None)
            if receipt:
                marker["receipt"] = receipt
                marker["attempts"][-1]["receipt"] = receipt
                with self._state_lock:
                    self.receipts.append({"stage": stage, "cache_key": key, "status": "failed", "cache_hit": False, **receipt})
                    if receipt.get("http_status") in {401,402,403,429}:
                        self._blocked_profiles[profile_key]="HTTP " + str(receipt["http_status"])
            if raw is not None:
                marker["invalid_answer"] = raw
            write_json(path, marker)
            raise ModelError(f"{stage}: {safe}") from None
        marker["attempts"][-1] = {"status": "completed"}
        write_json(path, {**marker, "status": "completed", "answer": answer, "receipt": receipt})
        with self._state_lock:
            self.receipts.append({"stage": stage, "cache_key": key, "cache_hit": False, **receipt})
        return answer
