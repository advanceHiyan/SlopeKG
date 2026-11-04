"""Model dispatch, cache, concurrency and accounting, independent of PDF orchestration."""
import asyncio
import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from .model_client import CompatibleVisionClient, ModelError, content_hash
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
        self._debug_local = threading.local()
        self.reset()

    def reset(self):
        self.calls = self.cache_hits = self.peak_concurrency = self._active_calls = 0
        self.reserved_calls = 0
        self.receipts = []
        self._blocked_profiles = {}
        self._cooldown_until = {}
        self.transient_retries = 0
        self._transient_retry_reservations = 0
        self.debug_trace = []
        self.document_id = None
        self._call_slots = asyncio.Semaphore(self.settings.model_concurrency)

    def reserve_calls(self,count):
        """Protect later source windows; cache reads remain available."""
        if type(count) is not int or not 0<=count<=self.settings.max_calls:raise ValueError('Invalid call reservation')
        with self._state_lock:self.reserved_calls=count

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
        recording = getattr(self.settings, 'record_model_inputs', False)
        event = None
        if recording:
            existing_input = read_json(self.output_dir / 'inputs' / (key + '.json'), {})
            write_json(self.output_dir / 'inputs' / (key + '.json'), {**identity,
                'cache_key': key, 'document_id': self.document_id,
                'input_capture': existing_input.get('input_capture', 'historical_saved_input' if existing_input else 'reconstructed_for_cache_lookup'),
                'images': [{'label': i.label, 'path': str(i.path), 'width': i.width, 'height': i.height,
                            'sha256': hashlib.sha256(i.path.read_bytes()).hexdigest()} for i in images]})
            event = {'stage': stage, 'cache_key': key, 'document_id': self.document_id,
                     'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat()}
            with self._state_lock:
                self.debug_trace.append(event)
                write_json(self.output_dir / 'debug-trace.json', self.debug_trace)
        with self._state_lock:
            request_lock = self._request_locks.setdefault(key, threading.Lock())
        with request_lock:
            self._debug_local.recording = recording
            self._debug_local.event = event
            started = time.monotonic()
            try:
                answer = self._cached_request(key, stage, model, prompt, images, validate)
                if event is not None:
                    event['status'] = 'completed'
                return answer
            except Exception as exc:
                if event is not None:
                    event.update(status='failed', error=str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__)
                raise
            finally:
                if event is not None:
                    with self._state_lock:
                        event.update(finished_at=datetime.now(timezone.utc).isoformat(),
                                     duration_seconds=round(time.monotonic() - started, 3))
                        write_json(self.output_dir / 'debug-trace.json', self.debug_trace)


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
        return self._attempt_request(key, stage, model, prompt, images, validate, path, cached)

    def _attempt_request(self, key, stage, model, prompt, images, validate, path, cached):
        """Retry only explicit transient responses; every attempt uses the cap.

        Timeouts, HTTP 408/504, transport uncertainty, bad JSON/contracts and old
        started markers never enter this automatic retry path.
        """
        retries, attempts = 0, list(cached.get('attempts', []))
        profile_key = content_hash(model.identity())
        while True:
            while True:
                with self._state_lock:
                    remaining = self._cooldown_until.get(profile_key, 0) - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 30))
            try:
                return self._one_attempt(key, stage, model, prompt, images, validate,
                                         path, profile_key, attempts, retries)
            except ModelError as exc:
                receipt = exc.receipt or {}
                status = receipt.get('http_status')
                max_retries = getattr(self.settings, 'max_transient_retries', 0)
                with self._state_lock:
                    retry = (status in {429, 500, 502, 503} and retries < max_retries
                        and self._transient_retry_reservations < getattr(self.settings, 'transient_retry_budget', 0)
                        and (receipt.get('retry_after_seconds') or 0) <= 30
                        and self.calls+self.reserved_calls < self.settings.max_calls and self.settings.allow_model_calls)
                    if retry:
                        delay = max(getattr(self.settings, 'retry_backoff_seconds', 2.0) * 2 ** retries,
                                    receipt.get('retry_after_seconds') or 0)
                        self._cooldown_until[profile_key] = max(self._cooldown_until.get(profile_key, 0),
                                                              time.monotonic() + min(delay, 30))
                        self._transient_retry_reservations += 1
                    elif receipt.get('finish_reason') == 'length':
                        self._blocked_profiles[profile_key] = 'output limit reached; response received, no automatic resend'
                    elif status in {401, 402, 403, 429} or max_retries and status in {500, 502, 503}:
                        self._blocked_profiles[profile_key] = 'HTTP ' + str(status)
                if not retry:
                    raise
                retries += 1

    def _one_attempt(self, key, stage, model, prompt, images, validate, path, profile_key, attempts, retries):
        with self._state_lock:
            if not self.settings.allow_model_calls:
                raise ModelError("Cache-only run; no model request sent")
            if profile_key in self._blocked_profiles:
                raise ModelError("Model profile blocked after " + self._blocked_profiles[profile_key] + "; no new request sent")
            if self.calls+self.reserved_calls >= self.settings.max_calls:
                raise ModelError("Run call budget exhausted; unfinished work retained")
            self.calls += 1
            if retries:
                self.transient_retries += 1
        attempts.append({'status': 'started', 'retry_number': retries})
        marker = {"status": "started", "stage": stage, "cache_key": key,
                  "attempts": attempts}
        if getattr(self.settings, 'record_model_inputs', False):
            input_path = self.output_dir / 'inputs' / (key + '.json')
            input_record = read_json(input_path, {})
            write_json(input_path, {**input_record, 'input_capture': 'before_transmission'})
        write_json(path, marker)  # Persist before transmitting a potentially billable request.
        raw, receipt = None, None
        try:
            with self._state_lock:
                self._active_calls += 1
                self.peak_concurrency = max(self.peak_concurrency, self._active_calls)
            try:
                options = {}
                if getattr(self._debug_local, 'recording', False) and isinstance(model, CompatibleVisionClient):
                    def capture(payload, response, raw_response_body=None, raw_response_base64=None):
                        exchange_path = self.output_dir / 'exchanges' / (key + '.json')
                        old = read_json(exchange_path, {})
                        exchanges = old.get('attempts', [])
                        if (response is not None or raw_response_body is not None) and exchanges and exchanges[-1].get('response') is None:
                            exchanges[-1].update(response=response, response_received_at=datetime.now(timezone.utc).isoformat())
                        else:
                            exchanges.append({'request': payload, 'response': response,
                                              'captured_at': datetime.now(timezone.utc).isoformat()})
                        if raw_response_body is not None:
                            exchanges[-1]['raw_response_body'] = raw_response_body
                        if raw_response_base64 is not None:
                            exchanges[-1]['raw_response_base64'] = raw_response_base64
                        write_json(exchange_path, {'cache_key': key, 'attempts': exchanges})
                    options['debug_callback'] = capture
                raw, receipt = model.complete(system=self.system, prompt=prompt, images=images, **options)
            finally:
                with self._state_lock:
                    self._active_calls -= 1
            receipt = {**receipt, "requested_model_profile": model.identity()}
            answer = validate(raw)
        except Exception as exc:
            # Runtime errors from injected clients can contain secrets; persist types only.
            safe = str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__
            marker.update(status="failed", error=safe)
            marker["attempts"][-1].update(status='failed', error=safe)
            receipt = receipt or getattr(exc, "receipt", None)
            if receipt:
                marker["receipt"] = receipt
                marker["attempts"][-1]["receipt"] = receipt
                with self._state_lock:
                    self.receipts.append({"stage": stage, "cache_key": key, "status": "failed", "cache_hit": False,
                                          'retry_number': retries, **receipt})
            if raw is not None:
                marker["invalid_answer"] = raw
            write_json(path, marker)
            raise ModelError(f"{stage}: {safe}", receipt=receipt) from None
        marker["attempts"][-1].update(status='completed', receipt=receipt)
        write_json(path, {**marker, "status": "completed", "answer": answer, "receipt": receipt,
                          **({'raw_answer': raw} if getattr(self.settings, 'record_model_inputs', False) else {})})
        with self._state_lock:
            self.receipts.append({"stage": stage, "cache_key": key, "cache_hit": False,
                                  'retry_number': retries, **receipt})
        return answer
