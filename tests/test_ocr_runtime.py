from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from slopekg.ocr import call_paddle_ocr_batch, resolve_paddle_device, title_block_render_plan, with_layout_groups


class OcrRuntimeTests(unittest.TestCase):
    def test_title_block_crop_handles_portrait_encoded_landscape_drawing(self) -> None:
        clip, rotation = title_block_render_plan(842, 1191)
        self.assertEqual(rotation, -90)
        self.assertEqual(clip, (0.0, 0.0, 252.6, 1191.0))

    def test_title_block_crop_keeps_complete_landscape_title_strip(self) -> None:
        clip, rotation = title_block_render_plan(1191, 842)
        self.assertEqual(rotation, 0)
        for actual, expected in zip(clip, (0.0, 437.84, 1191.0, 842.0)):
            self.assertAlmostEqual(actual, expected)

    def test_wrapped_label_joins_without_interleaving_neighbor_column(self):
        rows = [
            {"id": "a", "text": "河湾路基凹岸", "bbox": [669, 2069, 846, 2107], "task_id": "t", "page": 1},
            {"id": "b", "text": "沿河路基水毁", "bbox": [465, 2088, 647, 2135], "task_id": "t", "page": 1},
            {"id": "c", "text": "冲刷", "bbox": [721, 2109, 796, 2157], "task_id": "t", "page": 1},
        ]
        result = with_layout_groups(rows)
        self.assertEqual(result[:3], rows)
        self.assertEqual(result[3]["text"], "河湾路基凹岸冲刷")
        self.assertEqual(result[3]["source_item_ids"], ["a", "c"])
        self.assertEqual(with_layout_groups(result), result)
        rows[2]["page"] = 2
        self.assertEqual(with_layout_groups(rows), rows)

    def test_layout_join_does_not_combine_numeric_table_cells(self):
        rows = [{"id": "a", "text": "1.058", "bbox": [0, 0, 50, 20]},
                {"id": "b", "text": "1.15", "bbox": [0, 21, 50, 41]}]
        self.assertEqual(with_layout_groups(rows), rows)

    def test_layout_join_keeps_separate_legend_entries(self):
        rows = [{"id": "a", "text": "变形观测点", "bbox": [0, 0, 100, 20]},
                {"id": "b", "text": "设计排水方向", "bbox": [-10, 22, 110, 42]}]
        self.assertEqual(with_layout_groups(rows), rows)

    def test_cpu_device_does_not_require_cuda(self) -> None:
        self.assertEqual(resolve_paddle_device("cpu"), "cpu")

    def test_auto_selects_gpu_when_cuda_build_is_available(self) -> None:
        fake_paddle = SimpleNamespace(
            device=SimpleNamespace(
                is_compiled_with_cuda=lambda: True,
                cuda=SimpleNamespace(device_count=lambda: 1),
            )
        )
        with patch.dict(sys.modules, {"paddle": fake_paddle}):
            self.assertEqual(resolve_paddle_device("auto"), "gpu:0")

    def test_explicit_gpu_reports_cpu_only_install(self) -> None:
        fake_paddle = SimpleNamespace(
            device=SimpleNamespace(
                is_compiled_with_cuda=lambda: False,
                cuda=SimpleNamespace(device_count=lambda: 0),
            )
        )
        with patch.dict(sys.modules, {"paddle": fake_paddle}):
            with self.assertRaisesRegex(RuntimeError, "CPU-only"):
                resolve_paddle_device("gpu")

    def test_batch_uses_one_predict_call_and_preserves_order(self) -> None:
        class Engine:
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def predict(self, inputs: list[str]):
                self.inputs = inputs
                return iter([{"rec_texts": ["甲"]}, {"rec_texts": ["乙"]}])

        engine = Engine()
        paths = [Path("a.png"), Path("b.png")]
        results = call_paddle_ocr_batch(engine, paths)
        self.assertEqual(engine.inputs, ["a.png", "b.png"])
        self.assertEqual([row["rec_texts"][0] for row in results], ["甲", "乙"])


if __name__ == "__main__":
    unittest.main()
