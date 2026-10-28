"""Stable application entry point; select a route explicitly, never mix their artifacts."""
from .legacy_pipeline import (LegacyPipeline, graph_matches_documents, mark_cached_ocr_tasks,
                              source_file_matches_record, ocr_result_is_current)
from .pipeline_lock import (PipelineAlreadyRunningError, single_pipeline_run, pipeline_run_guard,
                            read_pipeline_lock, process_is_running)


class DemoPipeline(LegacyPipeline):
    """Entry point for local basic parsing and independent visual recognition."""

    def run(self, *args, recognizer="legacy", input_pdfs=None, model_profile=None, review_profile=None,
            visual_settings=None, knowledge_settings=None, prepare_only=False, **options):
        if any(key in options for key in ('use_llm', 'llm_model')):
            raise ValueError("旧文字deep参数已移除；多模态模型请使用model_profile")
        if recognizer == "legacy":
            if any(v is not None for v in (input_pdfs, model_profile, review_profile, visual_settings, knowledge_settings)) or prepare_only:
                raise ValueError("Visual options require recognizer='multimodal'")
            return super().run(*args, **options)
        if recognizer not in {"multimodal", "knowledge"}:
            raise ValueError("recognizer must be 'legacy', 'multimodal' or 'knowledge'")
        if args:
            raise ValueError("Multimodal options must be named explicitly")
        if options.get('parse_mode', 'basic') != 'basic' or options.get('reuse_parsed'):
            raise ValueError("Basic reuse options and removed text-model options cannot select multimodal behavior")
        if recognizer == 'knowledge':
            if visual_settings is not None:
                raise ValueError('全文知识路线不能混用多模态设置')
            from .knowledge_service import run_knowledge_recognition
            return run_knowledge_recognition(self.paths, input_pdfs=input_pdfs, model_profile=model_profile,
                review_profile=review_profile, knowledge_settings=knowledge_settings, prepare_only=prepare_only,
                progress_callback=options.get('progress_callback'))
        if knowledge_settings is not None:
            raise ValueError('全文知识设置需要knowledge路线')
        from .recognition import run_visual_recognition
        return run_visual_recognition(self.paths, input_pdfs=input_pdfs, model_profile=model_profile,
            review_profile=review_profile, visual_settings=visual_settings, prepare_only=prepare_only,
            progress_callback=options.get('progress_callback'))
