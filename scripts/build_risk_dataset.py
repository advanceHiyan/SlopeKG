"""Retired graph-based candidate builder; user prohibited this input source."""
from __future__ import annotations


def main() -> None:
    raise SystemExit(
        '旧图谱候选构建入口已停用：不得用自动识别结果预填风险数据集。\n'
        '原报告重建版及复查说明：'
        '.private/datasets/risk_review_v0_3_rebuilt_from_reports/README.md\n'
        '新封装只读取逐条核对录入的原述，不自动识别工程字段。'
    )


if __name__ == '__main__':
    main()
