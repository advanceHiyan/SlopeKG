from __future__ import annotations

import json
import os
import tempfile
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def verify_file_sha256(path: Path, expected: str) -> None:
    """Validate immutable generated evidence; recheck when its file stats change."""
    if not isinstance(expected, str) or len(expected) != 64:
        raise OSError("数据校验记录缺失，请重新导入")
    stat = path.stat()
    _verified_digest(str(path.resolve()), expected, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=32)
def _verified_digest(path: str, expected: str, size: int, modified: int, changed: int) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise OSError("数据文件与已发布的校验和不一致，请重新导入")


def discover_pdf_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    candidates = directory.rglob("*") if recursive else directory.glob("*")
    return sorted(
        (path for path in candidates if path.suffix.casefold() == ".pdf" and path.is_file()),
        key=lambda path: str(path.relative_to(directory)).casefold(),
    )


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    _atomic_write_text(path, "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace generated data atomically so API readers never see half a file."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(path)
    except Exception:
        try:
            Path(temp_name).unlink(missing_ok=True)
        finally:
            raise
