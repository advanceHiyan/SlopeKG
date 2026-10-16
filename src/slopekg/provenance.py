"""Identity of the implementation loaded by this process, not current HEAD."""
from datetime import datetime
import hashlib
import os
import platform
from pathlib import Path

def source_revision(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob('*.py')):
        digest.update(path.name.encode('utf8'))
        digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()

# Capture once: editing disk files cannot make an old server claim to have
# loaded new code. Restart the process to load a different implementation.
_LOADED = dict(code_revision=source_revision(Path(__file__).parent),
               process_started_at=datetime.now().isoformat(timespec='seconds'),
               pid=os.getpid(), python=platform.python_version())

def runtime_identity() -> dict:
    return dict(_LOADED)

def version_status(producer: dict | None) -> str:
    revision = (producer or {}).get('code_revision')
    if not revision:
        return 'unversioned'
    return 'current' if revision == _LOADED['code_revision'] else 'different_implementation'
