"""Versioned macro score and phase-coverage fields must survive response_model.

Run:  .venv312/bin/python -m pytest backend/tests/test_signals_schema_fields.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from analysis.signals import compute_signals  # noqa: E402
from backend.app.core import db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.schemas.signals import SignalSummary  # noqa: E402

client = TestClient(app)

NEW_FIELDS = ("as_of", "included", "excluded", "stale", "macro_score")
EXISTING_FIELDS = ("merrill", "credit", "inventory", "debt", "cross_lags",
                   "as_of", "included", "excluded", "stale")


def test_endpoint_exposes_as_of_and_coverage():
    expected = compute_signals(str(db.DB_PATH))      # 只读真实库
    body = client.get("/api/v1/signals").json()

    for key in NEW_FIELDS:
        assert key in body, f"response_model 过滤掉了 {key}"
        assert body[key] == expected[key], f"{key} 与 compute_signals 不一致"
    assert body["as_of"], "真实库应有一个非空的公共 as-of"


def test_phase_fields_unchanged():
    """四周期相位字段仍逐一等于 compute_signals 的返回。"""
    expected = compute_signals(str(db.DB_PATH))
    body = client.get("/api/v1/signals").json()
    for key in EXISTING_FIELDS:
        assert body[key] == expected[key], f"{key} 被改动了"


def test_new_fields_are_optional():
    """A minimal payload can still validate while optional metadata is absent."""
    m = SignalSummary(merrill={}, credit={}, inventory={}, debt={},
                      cross_lags={})
    assert m.as_of is None
    assert m.included == [] and m.excluded == [] and m.stale == []
    assert m.macro_score is None
