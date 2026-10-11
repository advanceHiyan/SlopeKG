"""Small synthetic fixtures and isolated workspace paths for unit tests."""
from pathlib import Path

from slopekg.config import DemoPaths


def workspace_paths(root: Path) -> DemoPaths:
    output = root / "output" / "demo"
    return DemoPaths(
        root=root, raw_dir=root / "data" / "rawPDF", output_dir=output,
        parsed_dir=output / "parsed", graph_dir=output / "graph",
        web_data_dir=output / "web" / "data", assets_dir=output / "assets",
        extracted_dir=output / "extracted", rules_dir=output / "rules",
        multimodal_dir=output / "multimodal", cache_dir=output / "cache",
        web_dir=root / "web", attribute_dictionary_file=root / "dictionary.json",
    )


def sample_graph() -> dict:
    return {"meta": {}, "nodes": [
        {"id": "s1", "type": "Slope", "label": "样例边坡", "summary": "",
         "props": {"slope_id": "S1"}},
    ], "edges": [], "evidence": [], "property_assertions": []}
