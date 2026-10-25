"""Append-only signal history — one row per successful pipeline commit."""

import sqlite3
import sys
from pathlib import Path

# repo root on sys.path so `import analysis` resolves when run via scripts
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.signals import compute_signals  # noqa: E402

TABLE = "signal_history"
_CREATE = """CREATE TABLE IF NOT EXISTS signal_history (
    ts TEXT NOT NULL, data_as_of TEXT,
    merrill TEXT, credit TEXT, inventory TEXT, debt TEXT,
    macro_score REAL, macro_label TEXT, macro_pressure REAL,
    macro_confidence REAL, score_version TEXT)"""


def append_signal_history(db_path, ts):
    """成功提交后追加一行版本化分数+四相位快照。"""
    sig = compute_signals(str(db_path))          # 提交后的新库
    macro = sig.get("macro_score") or {}
    conn = sqlite3.connect(db_path)
    try:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        if have and "composite" in have:
            # The user explicitly dropped the old [-4,+4] model and accepts
            # resetting its history. Never mix two incompatible score scales.
            conn.execute(f"DROP TABLE {TABLE}")
        conn.execute(_CREATE)
        data_as_of = str(macro.get("as_of") or "")[:7] or None
        if data_as_of is None:
            try:
                row = conn.execute("SELECT MAX(date) FROM derived_monthly").fetchone()
                data_as_of = str(row[0])[:7] if row and row[0] else None
            except sqlite3.Error:
                data_as_of = None
        conn.execute(
            f"INSERT INTO {TABLE} "
            "(ts, data_as_of, merrill, credit, inventory, debt, "
            "macro_score, macro_label, macro_pressure, macro_confidence, score_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, data_as_of, sig["merrill"]["phase"], sig["credit"]["phase"],
             sig["inventory"]["phase"], sig["debt"]["phase"], macro.get("score"),
             macro.get("label"), macro.get("pressure"), macro.get("confidence"),
             macro.get("version")))
        conn.commit()
    finally:
        conn.close()
