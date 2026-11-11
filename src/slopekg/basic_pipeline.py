"""基读 Basic: public name for local parsing, OCR and rule extraction.

The legacy module remains the implementation/compatibility layer so existing
imports, output folders and historical identifiers continue to work.
"""
from .legacy_pipeline import LegacyPipeline


class BasicPipeline(LegacyPipeline):
    """Local recognition publishes source data; assessment is a later operation."""
    recognition_only = True
