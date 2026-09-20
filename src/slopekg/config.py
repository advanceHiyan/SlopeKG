from __future__ import annotations

from dataclasses import dataclass
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "rawPDF"
ATTRIBUTE_DICTIONARY_FILE = ROOT / "data" / "初步需求" / "slope_attribute_data_dictionary_v0.2_feedback.json"
OUTPUT_DIR = ROOT / "output" / "demo"
WEB_DIR = ROOT / "web"
CACHE_DIR = OUTPUT_DIR / "cache"


@dataclass(frozen=True)
class DemoPaths:
    root: Path = ROOT
    raw_dir: Path = RAW_DIR
    output_dir: Path = OUTPUT_DIR
    parsed_dir: Path = OUTPUT_DIR / "parsed"
    graph_dir: Path = OUTPUT_DIR / "graph"
    web_data_dir: Path = OUTPUT_DIR / "web" / "data"
    assets_dir: Path = OUTPUT_DIR / "assets"
    extracted_dir: Path = OUTPUT_DIR / "extracted"
    rules_dir: Path = OUTPUT_DIR / "rules"
    multimodal_dir: Path = OUTPUT_DIR / "multimodal"
    cache_dir: Path = CACHE_DIR
    web_dir: Path = WEB_DIR
    attribute_dictionary_file: Path = ATTRIBUTE_DICTIONARY_FILE

    @property
    def graph_json(self) -> Path:
        return self.web_data_dir / "demo_graph.json"

    @property
    def basic_graph_json(self) -> Path:
        return self.web_data_dir / "basic_graph.json"

    @property
    def deep_graph_json(self) -> Path:
        return self.web_data_dir / "deep_graph.json"

    @property
    def state_json(self) -> Path:
        return self.output_dir / "pipeline_state.json"

    @property
    def basic_state_json(self) -> Path:
        return self.output_dir / "pipeline_state.basic.json"

    @property
    def deep_state_json(self) -> Path:
        return self.output_dir / "pipeline_state.deep.json"

    @property
    def schema_json(self) -> Path:
        return self.output_dir / "schema.json"

    @property
    def completeness_json(self) -> Path:
        return self.output_dir / "completeness.json"

    @property
    def interfaces_json(self) -> Path:
        return self.output_dir / "interfaces.json"

    @property
    def evaluation_json(self) -> Path:
        return self.output_dir / "evaluation.json"

    @property
    def rules_json(self) -> Path:
        return self.rules_dir / "rule_library.json"

    @property
    def rules_state_json(self) -> Path:
        return self.rules_dir / "state.json"

    @property
    def manual_dir(self) -> Path:
        return self.output_dir / "manual"

    @property
    def slope_overrides_json(self) -> Path:
        return self.manual_dir / "slope_overrides.json"

    @property
    def manual_rules_json(self) -> Path:
        return self.manual_dir / "rules.json"

    @property
    def manual_audit_jsonl(self) -> Path:
        return self.manual_dir / "audit.jsonl"

    @property
    def multimodal_catalog_json(self) -> Path:
        return self.multimodal_dir / "asset_catalog.json"

    @property
    def multimodal_quality_json(self) -> Path:
        return self.multimodal_dir / "quality_report.json"

    @property
    def multimodal_contract_json(self) -> Path:
        return self.multimodal_dir / "data_contract.json"


PATHS = DemoPaths()


def configure_runtime_cache() -> Path:
    """Keep OCR/ML runtime caches inside the project workspace."""
    home_dir = CACHE_DIR / "home"
    temp_dir = CACHE_DIR / "tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    for name, value in {
        "HOME": home_dir,
        "USERPROFILE": home_dir,
        "TEMP": temp_dir,
        "TMP": temp_dir,
        "TMPDIR": temp_dir,
        "XDG_CACHE_HOME": CACHE_DIR / "xdg",
        "PADDLE_HOME": CACHE_DIR / "paddle",
        "PADDLEOCR_HOME": CACHE_DIR / "paddleocr",
        "HF_HOME": CACHE_DIR / "huggingface",
    }.items():
        os.environ[name] = str(value)
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
    tempfile.tempdir = str(temp_dir)
    return CACHE_DIR
