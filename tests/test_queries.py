from pathlib import Path
import tempfile
import unittest

from slopekg.manual import save_slope_override
from slopekg.queries import current_graph, document_inventory
from slopekg.storage import write_json
from tests.support import workspace_paths, sample_graph


class QueryTests(unittest.TestCase):
    def test_queries_use_explicit_paths_and_apply_local_manual_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            write_json(paths.graph_json, sample_graph())
            save_slope_override("s1", {"fields": {"slope_height_m": 12}}, paths=paths)
            self.assertEqual(current_graph(paths)["nodes"][0]["props"]["slope_height_m"], 12)
            self.assertNotIn("slope_height_m", sample_graph()["nodes"][0]["props"])
            self.assertEqual(document_inventory(paths)["count"], 0)
