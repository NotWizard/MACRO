"""Versioned 0–100 macro score contract and deterministic aggregation."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analysis.signals import compute_signals  # noqa: E402

DB = Path(__file__).resolve().parents[2] / "data" / "macro_data.db"


def _score():
    return compute_signals(str(DB))["macro_score"]


@pytest.mark.skipif(not DB.exists(), reason="real macro DB absent")
def test_macro_score_contract_and_weights():
    m = _score()
    assert m["version"] == "macro-expansion-v1"
    assert set(m["components"]) == {"growth", "credit", "external", "fiscal", "housing"}
    assert sum(m["weights"].values()) == pytest.approx(1.0)
    assert 0 <= m["score"] <= 100
    assert 0 <= m["pressure"] <= 100
    assert 0 <= m["confidence"] <= 100
    assert 0 <= m["coverage"] <= 100


@pytest.mark.skipif(not DB.exists(), reason="real macro DB absent")
def test_headline_is_weighted_component_average():
    m = _score()
    weighted = sum(
        c["score"] * c["weight"] for c in m["components"].values() if c["available"]
    )
    coverage = sum(c["weight"] for c in m["components"].values() if c["available"])
    assert m["score"] == pytest.approx(weighted / coverage, abs=0.11)


@pytest.mark.skipif(not DB.exists(), reason="real macro DB absent")
def test_components_remain_interpretable():
    m = _score()
    for c in m["components"].values():
        assert c["weight"] > 0
        assert c["parts"]
        for p in c["parts"]:
            if p["score"] is not None:
                assert not math.isnan(p["score"])
                assert 0 <= p["score"] <= 100
            assert p["as_of"] is None or len(p["as_of"]) >= 7
