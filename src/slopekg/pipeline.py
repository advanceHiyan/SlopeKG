"""Stable application entry point; select a route explicitly, never mix their artifacts."""
from .legacy_pipeline import (LegacyPipeline, graph_matches_documents, mark_cached_ocr_tasks,
                              source_file_matches_record, ocr_result_is_current)
from .pipeline_lock import (PipelineAlreadyRunningError, single_pipeline_run, pipeline_run_guard,
                            read_pipeline_lock, process_is_running)


class DemoPipeline(LegacyPipeline):
    """Compatibility facade for existing basic/deep callers and new recognition clients."""

    def run(self, *args, recognizer="legacy", input_pdfs=None, model_profile=None, review_profile=None,
            visual_settings=None, prepare_only=False, **options):
        if recognizer == "legacy":
            if any(v is not None for v in (input_pdfs, model_profile, review_profile, visual_settings)) or prepare_only:
                raise ValueError("Visual options require recognizer='multimodal'")
            return super().run(*args, **options)
        if recognizer != "multimodal":
            raise ValueError("recognizer must be 'legacy' or 'multimodal'")
        if args:
            raise ValueError("Multimodal options must be named explicitly")
        if options.get('parse_mode', 'basic') != 'basic' or options.get('use_llm') is not None or options.get('reuse_parsed'):
            raise ValueError("Legacy deep/use_llm/reuse options cannot select multimodal behavior")
        from .recognition import run_visual_recognition
        return run_visual_recognition(self.paths, input_pdfs=input_pdfs, model_profile=model_profile,
            review_profile=review_profile, visual_settings=visual_settings, prepare_only=prepare_only,
            progress_callback=options.get('progress_callback'))
