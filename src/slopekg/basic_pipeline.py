"""基读 Basic: public name for local parsing, OCR and rule extraction.

The legacy module remains the implementation/compatibility layer so existing
imports, output folders and historical identifiers continue to work.
"""
from .legacy_pipeline import LegacyPipeline


class BasicPipeline(LegacyPipeline):
    """基读 Basic; identical parsing and publication behavior to LegacyPipeline."""
