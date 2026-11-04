"""Conservative structural headers for tables with merged leading rows."""
from decimal import Decimal, InvalidOperation


def _number(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def header_rows(rows):
    """Use the leading label/unit rows, stopping at the first scalar data row.

    Empty merged cells do not vote against a header. Consecutive column numbers
    are retained as structural labels, never interpreted as a measurement.
    A numeric matrix starting on row zero has no inferred header.
    """
    result = []
    for index, row in enumerate(rows):
        cells = [str(c or '').strip() for c in row]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        numbers = [_number(c) for c in nonempty]
        numeric = [n for n in numbers if n is not None]
        column_numbers = (bool(result) and len(numeric) >= 3 and len(numeric) == len(nonempty)
            and len(nonempty) < len(cells) and all(n == n.to_integral() for n in numeric)
            and all(b == a + 1 for a, b in zip(numeric, numeric[1:])))
        if column_numbers:
            result.append(index)
            continue
        if len(numeric) >= 2 or (numeric and len(nonempty) > 1):
            break
        # Without a data boundary only the first text row is a tentative label;
        # subsequent multirow headers are used only when a scalar row follows.
        result.append(index)
    if result and len(result) == len([r for r in rows if any(str(c or '').strip() for c in r)]):
        return result[:1]
    return result
