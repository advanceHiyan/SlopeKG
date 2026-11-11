"""Lightweight service identity for launchers; never loads recognition data."""
from pathlib import Path

from .provenance import runtime_identity, source_revision


def service_health(paths):
    loaded = runtime_identity()
    disk = source_revision(Path(__file__).parent)
    from .standard_assessment.service import PACKAGE_LOADED_REVISION
    from .standard_assessment.debug_flow import BRIDGE_REVISION
    assessment_disk=source_revision(Path(__file__).parent/'standard_assessment')
    return {'app': 'SlopeKG', 'protocol_version': 1, 'root': str(paths.root.resolve()),
            'status': 'ready' if loaded['code_revision'] == disk and PACKAGE_LOADED_REVISION==assessment_disk and BRIDGE_REVISION==assessment_disk else 'restart_required',
            'runtime': loaded, 'disk_code_revision': disk,
            'assessment_loaded_revision':PACKAGE_LOADED_REVISION,'assessment_disk_revision':assessment_disk,
            'capabilities': ['recognition_debug', 'debug_datasets', 'debug_history','assessment_debug','standard_assessment','agent_recognition','assessment_workspace','slope_library','slope_identity']}
