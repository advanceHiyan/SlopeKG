"""Whole-document reconstruction and overlapping dossiers: experimental fourth route."""
from dataclasses import dataclass

VERSION = 'document-v0.2'


@dataclass(frozen=True)
class DocumentSettings:
    window_pages: int = 2
    page_edge: int = 2200
    max_calls: int = 24
    model_concurrency: int = 2
    max_prompt_chars: int = 100000
    max_images: int = 6
    max_image_pixels: int = 30000000
    max_image_bytes: int = 30000000
    max_pixels_per_image: int = 8000000
    allow_model_calls: bool = True
    retry_failed: bool = False
    record_model_inputs: bool = True
    max_transient_retries: int = 0
    transient_retry_budget: int = 0
    retry_backoff_seconds: float = 2.0
    dossier_chars: int = 20000
    max_structure_repairs: int = 1

    def __post_init__(self):
        for key, lo, hi in [('window_pages',1,6),('page_edge',800,4500),('max_calls',1,10000),
                ('model_concurrency',1,8),('max_prompt_chars',12000,500000),('max_images',1,12),
                ('max_image_pixels',1000000,100000000),('max_image_bytes',1000000,100000000),
                ('max_pixels_per_image',1000000,16000000),('max_transient_retries',0,3),
                ('transient_retry_budget',0,10),('dossier_chars',4000,100000),('max_structure_repairs',0,2)]:
            if type(getattr(self,key)) is not int or not lo<=getattr(self,key)<=hi:
                raise ValueError('Invalid document setting: '+key)
        for key in ('allow_model_calls','retry_failed','record_model_inputs'):
            if type(getattr(self,key)) is not bool:raise ValueError('Invalid document switch: '+key)
        if self.window_pages>self.max_images:raise ValueError('Page window exceeds image capacity')

