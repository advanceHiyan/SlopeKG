"""Budgets for domain-independent, document-wide knowledge recognition."""
from dataclasses import dataclass

VERSION = 'knowledge-v3.8'


@dataclass(frozen=True)
class KnowledgeSettings:
    chunk_chars: int = 2800
    batch_chars: int = 24000
    synthesis_chars: int = 24000
    overlap_units: int = 1
    min_page_chars: int = 40
    max_entities: int = 100
    max_calls: int = 64
    model_concurrency: int = 6
    max_prompt_chars: int = 60000
    allow_model_calls: bool = True
    retry_failed: bool = False
    synthesis_batch_objects: int = 6
    review_mode: str = 'selective'
    max_review_calls: int = 4
    # Existing OCR is reused; new Basic local OCR requires an explicit page cap.
    ocr_pages: int = 0
    max_transient_retries: int = 2
    transient_retry_budget: int = 4
    retry_backoff_seconds: float = 2.0
    record_model_inputs: bool = False
    # The shared transport also bounds images; this route sends text only.
    max_images: int = 0
    max_image_pixels: int = 0
    max_image_bytes: int = 0

    def __post_init__(self):
        for key, low, high in [('chunk_chars', 600, 10000), ('batch_chars', 4000, 100000),
                ('synthesis_chars', 4000, 100000), ('overlap_units', 0, 2),
                ('min_page_chars', 1, 200), ('max_entities', 1, 500), ('max_calls', 1, 10000),
                ('model_concurrency', 1, 16), ('max_prompt_chars', 16000, 500000),
                ('synthesis_batch_objects', 1, 16), ('max_review_calls', 0, 32), ('ocr_pages', 0, 1000),
                ('max_transient_retries', 0, 3), ('transient_retry_budget', 0, 16)]:
            if type(getattr(self, key)) is not int or not low <= getattr(self, key) <= high:
                raise ValueError('Invalid knowledge setting: ' + key)
        if self.batch_chars >= self.max_prompt_chars // 2 or self.synthesis_chars >= self.max_prompt_chars // 2:
            raise ValueError('Knowledge source budgets must leave room for identities and instructions')
        if any(type(getattr(self, key)) is not bool for key in ('allow_model_calls', 'retry_failed', 'record_model_inputs')):
            raise ValueError('Knowledge switches must be boolean')
        if not isinstance(self.review_mode, str) or self.review_mode not in {'selective', 'all', 'none'}:
            raise ValueError('Invalid knowledge review mode')
        if type(self.retry_backoff_seconds) not in (int, float) or not 0 <= self.retry_backoff_seconds <= 30:
            raise ValueError('Invalid retry backoff')
        if any(getattr(self, key) != 0 for key in ('max_images', 'max_image_pixels', 'max_image_bytes')):
            raise ValueError('Knowledge recognition is a text route; image pages remain explicitly unread')
