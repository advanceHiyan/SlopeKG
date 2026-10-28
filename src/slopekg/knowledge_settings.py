"""Budgets for domain-independent, document-wide knowledge recognition."""
from dataclasses import dataclass

VERSION = 'knowledge-v2'


@dataclass(frozen=True)
class KnowledgeSettings:
    chunk_chars: int = 2800
    batch_chars: int = 22000
    synthesis_chars: int = 16000
    overlap_units: int = 1
    min_page_chars: int = 40
    max_entities: int = 100
    max_calls: int = 500
    model_concurrency: int = 4
    max_prompt_chars: int = 60000
    allow_model_calls: bool = True
    retry_failed: bool = False
    # The shared transport also bounds images; this route sends text only.
    max_images: int = 0
    max_image_pixels: int = 0
    max_image_bytes: int = 0

    def __post_init__(self):
        for key, low, high in [('chunk_chars', 600, 10000), ('batch_chars', 4000, 100000),
                ('synthesis_chars', 4000, 100000), ('overlap_units', 0, 2),
                ('min_page_chars', 1, 200), ('max_entities', 1, 500), ('max_calls', 1, 10000),
                ('model_concurrency', 1, 16), ('max_prompt_chars', 16000, 500000)]:
            if type(getattr(self, key)) is not int or not low <= getattr(self, key) <= high:
                raise ValueError('Invalid knowledge setting: ' + key)
        if self.batch_chars >= self.max_prompt_chars // 2 or self.synthesis_chars >= self.max_prompt_chars // 2:
            raise ValueError('Knowledge source budgets must leave room for identities and instructions')
        if any(type(getattr(self, key)) is not bool for key in ('allow_model_calls', 'retry_failed')):
            raise ValueError('Knowledge switches must be boolean')
        if any(getattr(self, key) != 0 for key in ('max_images', 'max_image_pixels', 'max_image_bytes')):
            raise ValueError('Knowledge recognition is a text route; image pages remain explicitly unread')
