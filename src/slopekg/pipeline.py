"""Named entry points: 基读 Basic, 知联 Knowledge, 视读 Visual, 整读 Document."""
from .basic_pipeline import BasicPipeline
from .recognition_routes import normalize_recognizer, recognition_route
from .legacy_pipeline import (LegacyPipeline, graph_matches_documents, mark_cached_ocr_tasks,
                              source_file_matches_record, ocr_result_is_current)
from .pipeline_lock import (PipelineAlreadyRunningError, single_pipeline_run, pipeline_run_guard,
                            read_pipeline_lock, process_is_running)


class DemoPipeline(BasicPipeline):
    """Dispatch Basic / Knowledge / Visual / Document; accept historical aliases."""

    def run(self, *args, recognizer="basic", input_pdfs=None, model_profile=None, review_profile=None,
            visual_settings=None, knowledge_settings=None, document_settings=None, agent_settings=None,
            prepare_only=False, resume_from=None, page_selections=None, **options):
        if any(key in options for key in ('use_llm', 'llm_model')):
            raise ValueError("旧文字deep参数已移除；多模态模型请使用model_profile")
        recognizer = normalize_recognizer(recognizer)
        if recognizer != 'agent' and (resume_from is not None or page_selections is not None):
            raise ValueError('接续选页设置需要选择自主 Agent')
        if recognizer == "basic":
            if any(v is not None for v in (input_pdfs, model_profile, review_profile, visual_settings, knowledge_settings, document_settings, agent_settings)) or prepare_only:
                raise ValueError("模型参数需要选择知联、视读或整读")
            return self.run_basic(*args, **options)
        if args:
            raise ValueError("模型路线参数必须显式命名")
        if options.get('parse_mode', 'basic') != 'basic' or options.get('reuse_parsed'):
            raise ValueError("基读 Basic 的缓存选项不能混用模型路线")
        if recognizer == 'agent':
            if any(v is not None for v in (visual_settings,knowledge_settings,document_settings,review_profile)):
                raise ValueError('自主 Agent 不能混用其他路线或复核模型设置')
            from .agent_service import run_agent_recognition
            return {**run_agent_recognition(self.paths,input_pdfs=input_pdfs,model_profile=model_profile,
                agent_settings=agent_settings,prepare_only=prepare_only,progress_callback=options.get('progress_callback'),
                resume_from=resume_from,page_selections=page_selections),
                'route':recognition_route('agent').payload()}
        if agent_settings is not None:raise ValueError('自主设置需要选择自主 Agent')
        if recognizer == 'document':
            if visual_settings is not None or knowledge_settings is not None:
                raise ValueError('整读 Document 不能混用其他路线设置')
            from .document_service import run_document_recognition
            return {**run_document_recognition(self.paths,input_pdfs=input_pdfs,model_profile=model_profile,
                review_profile=review_profile,document_settings=document_settings,prepare_only=prepare_only,
                progress_callback=options.get('progress_callback')),'route':recognition_route('document').payload()}
        if document_settings is not None:raise ValueError('整读设置需要选择整读 Document')
        if recognizer == 'knowledge':
            if visual_settings is not None:
                raise ValueError('知联 Knowledge 不能混用视读 Visual 设置')
            return self.run_knowledge(input_pdfs=input_pdfs, model_profile=model_profile,
                review_profile=review_profile, knowledge_settings=knowledge_settings, prepare_only=prepare_only,
                progress_callback=options.get('progress_callback'))
        if knowledge_settings is not None:
            raise ValueError('知识设置需要选择知联 Knowledge')
        return self.run_visual(input_pdfs=input_pdfs, model_profile=model_profile,
            review_profile=review_profile, visual_settings=visual_settings, prepare_only=prepare_only,
            progress_callback=options.get('progress_callback'))

    def run_basic(self, *args, **options):
        """基读 Basic: local parsing/OCR and deterministic extraction."""
        return {**super().run(*args, **options), 'route': recognition_route('basic').payload()}

    def run_knowledge(self, **options):
        """知联 Knowledge: document-wide discovery, ownership and reorganization."""
        from .knowledge_service import run_knowledge_recognition
        return {**run_knowledge_recognition(self.paths, **options),
                'route': recognition_route('knowledge').payload()}

    def run_visual(self, **options):
        """视读 Visual: source-page images, regions, reading and verification."""
        from .recognition import run_visual_recognition
        return {**run_visual_recognition(self.paths, **options),
                'route': recognition_route('visual').payload()}
