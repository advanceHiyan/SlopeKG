from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from .config import ROOT, configure_runtime_cache


configure_runtime_cache()


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def bundled_poppler_exe(name: str) -> Path | None:
    candidates = [
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "native"
        / "poppler"
        / "Library"
        / "bin"
        / f"{name}.exe",
        ROOT / "bin" / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def dependency_report() -> dict[str, bool | str | None]:
    return {
        "pypdf": has_module("pypdf"),
        "pdfplumber": has_module("pdfplumber"),
        "pymupdf": has_module("fitz"),
        "paddleocr": has_module("paddleocr"),
        "paddlepaddle": has_module("paddle"),
        "pandas": has_module("pandas"),
        "networkx": has_module("networkx"),
        "pdftoppm": str(bundled_poppler_exe("pdftoppm")) if bundled_poppler_exe("pdftoppm") else None,
    }
