"""Validated provider-independent rendering, context and call budgets."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VisualSettings:
    overview_edge: int = 1600
    detail_edge: int = 3000
    reread_edge: int = 4500
    window_pages: int = 4
    max_calls: int = 200
    max_prompt_chars: int = 24000
    max_images: int = 6
    max_image_pixels: int = 24000000
    max_pixels_per_image: int = 12000000
    max_image_bytes: int = 30000000
    max_regions_per_page: int = 64
    max_rereads_per_region: int = 2
    audit_rounds: int = 2
    max_audit_regions: int = 8
    use_native_text: bool = True
    use_existing_ocr: bool = True
    retry_failed: bool = False
    model_concurrency: int = 1
    crop_padding: float = .01
    allow_model_calls: bool = True
    independent_review: bool = True

    def __post_init__(self):
        if any(type(getattr(self, k)) is not bool for k in
               ('allow_model_calls','independent_review','use_native_text','use_existing_ocr','retry_failed')):
            raise ValueError("Visual switches must be boolean")
        for key in ("overview_edge", "detail_edge", "reread_edge"):
            if not 256 <= getattr(self, key) <= 10000:
                raise ValueError("Render edges must be 256..10000")
        if not (1 <= self.window_pages <= 16 and 1 <= self.max_calls <= 100000
                and 8000 <= self.max_prompt_chars <= 1000000 and 2 <= self.max_images <= 32
                and self.max_image_pixels >= 1000000 and self.max_pixels_per_image >= 65536 and self.max_image_bytes >= 100000
                and 1 <= self.max_regions_per_page <= 1024 and 0 <= self.max_rereads_per_region <= 8
                and 1 <= self.audit_rounds <= 4 and 1 <= self.max_audit_regions <= 64):
            raise ValueError("Invalid visual pipeline budget")
        if type(self.model_concurrency) is not int or not 1 <= self.model_concurrency <= 32:
            raise ValueError("model_concurrency must be 1..32")
        if not 0 <= self.crop_padding <= .1:
            raise ValueError("crop_padding must be a normalized margin in 0..0.1")
