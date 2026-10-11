"""Run anonymous unit tests or explicitly opt into the local corpus checks."""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("unit", "integration", "all"), default="unit")
    args = parser.parse_args()
    if args.suite in {"integration", "all"}:
        from slopekg.config import PATHS
        required = [PATHS.parsed_dir / f"{name}.jsonl" for name in
                    ("documents", "pages", "text_blocks", "tables", "ocr_tasks", "ocr_results")]
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        if missing:
            parser.exit(2, "Local integration prerequisites are missing; run the PDF pipeline first:\n"
                        + "\n".join(missing) + "\n")
    suite = unittest.TestSuite()
    if args.suite in {"unit", "all"}:
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    if args.suite in {"integration", "all"}:
        # No integration/__init__.py: ordinary unit discovery never runs private data checks.
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests" / "integration")))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
