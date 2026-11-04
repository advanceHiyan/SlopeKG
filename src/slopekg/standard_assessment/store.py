"""Append-only assertions/runs/reviews, with transactional case version checks."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .contract import encoded

TABLES = {
    "assessment_case": "case_id", "fact_assertion": "fact_id", "derivation_run": "derivation_id",
    "case_resolution": "resolution_id", "assessment_result": "result_id", "review_decision": "review_id",
    "identity_binding": "binding_id", "evidence_context": "context_id", "source_record": "record_ref_id",
}


class AssessmentStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            for name in TABLES:
                db.execute(f"CREATE TABLE IF NOT EXISTS {name} (record_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            db.execute("PRAGMA busy_timeout=20000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, table: str, record_id: str, db=None) -> dict:
        if table not in TABLES:
            raise ValueError("unknown table")
        if db is None:
            with self.transaction() as session:
                return self.get(table, record_id, session)
        row = db.execute(f"SELECT payload FROM {table} WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return json.loads(row[0])

    def rows(self, table: str, db=None) -> list[dict]:
        if table not in TABLES:
            raise ValueError("unknown table")
        if db is None:
            with self.transaction() as session:
                return self.rows(table, session)
        return [json.loads(r[0]) for r in db.execute(f"SELECT payload FROM {table} ORDER BY rowid")]

    def put(self, table: str, record: dict, db) -> None:
        if table not in TABLES:
            raise ValueError("unknown table")
        rid = record[TABLES[table]]
        text = encoded(record)
        previous = db.execute(f"SELECT payload FROM {table} WHERE record_id=?", (rid,)).fetchone()
        if previous is not None:
            if previous[0] != text:
                raise ValueError(f"immutable {table} collision: {rid}")
            return
        db.execute(f"INSERT INTO {table}(record_id,payload) VALUES(?,?)", (rid, text))

    def update_case(self, case: dict, db) -> None:
        # All old resolutions and outputs remain immutable and versioned.
        db.execute("UPDATE assessment_case SET payload=? WHERE record_id=?", (encoded(case), case["case_id"]))

    def export(self) -> dict:
        with self.transaction() as db:
            return {name: self.rows(name, db) for name in TABLES}
