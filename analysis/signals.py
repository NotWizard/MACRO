"""Latest cycle states plus the versioned 0–100 macro expansion score.

The old [-4,+4] "sum four phases" score was intentionally removed. The four
cycle classifiers remain descriptive building blocks; the headline score lives
in :mod:`analysis.macro_score` and carries magnitude, pressure, confidence and
an explicit methodology version.
"""

import functools
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

# Allow imports when run as a script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.cycle_merrill import INSUFFICIENT_DATA, classify_merrill
from analysis.cycle_credit import classify_credit
from analysis.cycle_inventory import classify_inventory
from analysis.cycle_debt import classify_debt
from analysis.cross_indicator import leading_lag_analysis
from analysis.macro_score import compute_macro_score

FRAMEWORKS = ("merrill", "credit", "inventory", "debt")

_VALID_PHASES = {
    "merrill": {"recovery", "overheating", "stagflation", "recession"},
    "credit": {"easing", "neutral", "tightening"},
    "inventory": {
        "active_restocking", "passive_restocking",
        "passive_destocking", "active_destocking",
    },
    "debt": {
        "beautiful_deleveraging", "ugly_deleveraging",
        "leveraging_boom", "leveraging_bust",
        "stable_growth", "stable_contraction",
    },
}

# How many months a framework may legitimately lag the newest observation across
# all frameworks (its own publication cadence + one release lag) before its phase
# is describing a period the macro score does not: annual GDP is up to a year plus a
# quarter behind, quarterly leverage a quarter plus a quarter, monthly series at
# most a couple of months. Beyond this the sub-signal is flagged and down-weighted
# rather than summed as if it were current.
_STALE_TOLERANCE_MONTHS = {"merrill": 15, "credit": 3, "inventory": 3, "debt": 6}

# A sub-signal can also be stale from the INSIDE: the inventory demand axis is a
# carried-forward PMI, so its frame reports how old that input is.
_INPUT_STALENESS_KEY = {"inventory": "pmi_stale_months"}

# WHY half and not a decay curve: a stale macro reading is still evidence (these
# cycles are persistent), it just must not outvote current data. One flat factor
# is auditable and adds no knob that we have no data to fit.
_STALE_WEIGHT = 0.5


def _db_version(db_path: str) -> tuple:
    """Cheap content tag for *db_path*: ``(mtime_ns, size)``.

    Mirrors ``backend.app.core.db._db_version`` but keyed on the *argument*
    path — ``compute_signals`` is always called with an explicit ``db_path``
    (the real DB in production, a temp copy in tests). Replicated (not imported)
    so the ``analysis`` package stays importable without the ``backend`` package
    on ``sys.path``. Missing file → ``(0, 0)`` (fresh install must not crash).
    """
    try:
        st = os.stat(db_path)
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


# The four cycle classifiers are version-keyed themselves now (G03b:
# analysis.cycle_merrill.db_versioned_cache), so dropping them here is no longer
# required for correctness — it is a memory reclaim that runs exactly when a new
# DB version makes the old entries dead weight. Captured at import, so a test that
# monkeypatches the module-level names still runs fresh functions while these
# originals get cleared harmlessly. (cross_indicator's leading_lag_analysis is
# uncached — always fresh.)
_DOWNSTREAM_DB_PATH_CACHES = (
    classify_merrill,
    classify_credit,
    classify_inventory,
    classify_debt,
)


def _invalidate_downstream_caches() -> None:
    for fn in _DOWNSTREAM_DB_PATH_CACHES:
        cache_clear = getattr(fn, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


def _month_gap(later: pd.Timestamp, earlier: pd.Timestamp) -> int:
    """Whole months between two observation dates (calendar, not day-count)."""
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _fmt(ts, fmt: str) -> Optional[str]:
    return ts.strftime(fmt) if hasattr(ts, "strftime") else (None if ts is None else str(ts))


def _num(row, col: str) -> Optional[float]:
    """Row value as a JSON-safe float; ``None`` for a missing row/column or NaN."""
    if row is None or col not in row or pd.isna(row[col]):
        return None
    return float(row[col])


def _sub_signal(
    name: str,
    df: pd.DataFrame,
    phase_col: str,
    date_fmt: str,
    numeric_cols: tuple = (),
    phase_cols: tuple = (),
) -> Dict:
    """Latest row of one classifier as a self-describing sub-signal.

    An empty frame yields ``phase="insufficient_data"``, ``included=False`` and
    weight 0 — this is the guard for the crash that used to take ``/api/v1/signals``
    down with ``IndexError`` on a fresh/empty DB. Unknown phases are excluded.
    """
    row = df.iloc[-1] if df is not None and len(df) else None
    phase = str(row[phase_col]) if row is not None else INSUFFICIENT_DATA
    included = phase in _VALID_PHASES[name]
    date = row["date"] if row is not None and pd.notna(row["date"]) else None

    out = {
        "date": _fmt(date, date_fmt),
        "as_of": _fmt(date, "%Y-%m-%d"),
        "phase": phase,
        "included": included,
        "weight": 1.0 if included else 0.0,
        "stale": False,
        "stale_months": None,
        # Private alignment reference (popped before returning): only a USABLE
        # observation may define the common as-of frontier.
        "_date": date if included else None,
    }
    out.update({c: _num(row, c) for c in numeric_cols})
    out.update({c: (str(row[c]) if row is not None else None) for c in phase_cols})
    return out


def _apply_staleness(subs: Dict[str, Dict], frontier: Optional[pd.Timestamp]) -> None:
    """Measure each sub-signal's lag against the common as-of and down-weight.

    Alignment note: the frameworks are annual/quarterly/monthly, so a single
    literal as-of date is impossible — truncating the monthly credit read back to
    the annual GDP date would throw away five months of the freshest information.
    Instead the common as-of is the NEWEST observation across frameworks and each
    sub-signal reports its own lag against it; only a lag beyond that framework's
    publication cadence is treated as stale.
    """
    if frontier is None:
        return
    for name, sub in subs.items():
        if not sub["included"] or sub["_date"] is None:
            continue
        lag = _month_gap(frontier, sub["_date"])
        input_key = _INPUT_STALENESS_KEY.get(name)
        input_lag = sub.get(input_key) if input_key else None
        effective = max(lag, int(input_lag or 0))
        sub["stale_months"] = lag
        if effective > _STALE_TOLERANCE_MONTHS[name]:
            sub["stale"] = True
            sub["weight"] = _STALE_WEIGHT


def compute_signals(db_path: str) -> Dict:
    """Compute the versioned macro score and latest cycle states.

    Thin wrapper: the cache key of :func:`_compute_signals_versioned` includes
    ``_db_version(db_path)`` so an atomic swap of the DB file — regardless of who
    performed it — yields a fresh key and a fresh computation, with no dependency
    on an explicit ``clear_all_caches()`` call.
    """
    return _compute_signals_versioned(db_path, _db_version(db_path))


@functools.lru_cache(maxsize=4)
def _compute_signals_versioned(db_path: str, version: tuple) -> Dict:
    """Cached body, keyed by ``(db_path, DB version)``.

    Runs only on a real version change (or first call). ``version`` is otherwise
    unused (key-only).

    Returns
    -------
    dict
        Keys:
            merrill       : dict  — current phase + score + as_of + coverage flags
            credit        : dict  — idem
            inventory     : dict  — idem
            debt          : dict  — idem (overall_phase)
            cross_lags    : dict  — leading-lag correlation summary
            as_of           : str   — newest observation across frameworks
            included        : list  — frameworks with a current phase
            excluded        : list  — frameworks with no usable phase
            stale           : list  — frameworks kept at half weight
            macro_score     : dict  — versioned 0–100 expansion score
    """
    _invalidate_downstream_caches()
    # ── Run classifiers ──────────────────────────────────────────────────────
    frames = {
        "merrill": classify_merrill(db_path),
        "credit": classify_credit(db_path),
        "inventory": classify_inventory(db_path),
        "debt": classify_debt(db_path),
    }
    cross = leading_lag_analysis(db_path)

    # ── Latest phase from each framework, each with its OWN as-of ───────────
    subs = {
        "merrill": _sub_signal(
            "merrill", frames["merrill"], "phase", "%Y",
            numeric_cols=("gdp_yoy", "cpi_yoy"),
        ),
        "credit": _sub_signal(
            "credit", frames["credit"], "phase", "%Y-%m",
            numeric_cols=("m2_yoy", "credit_impulse"),
        ),
        "inventory": _sub_signal(
            "inventory", frames["inventory"], "phase", "%Y-%m",
            numeric_cols=("pmi_official", "pmi_used", "pmi_stale_months"),
        ),
        "debt": _sub_signal(
            "debt", frames["debt"], "overall_phase", "%Y-%m",
            numeric_cols=("net_change",),
            phase_cols=("household_phase", "corp_phase", "gov_phase"),
        ),
    }

    # Common as-of = the newest USABLE observation any framework has (each
    # sub-signal carries its own date in the private "_date" key).
    dates = [s["_date"] for s in subs.values() if s["_date"] is not None]
    frontier = max(dates) if dates else None
    _apply_staleness(subs, frontier)
    for sub in subs.values():
        sub.pop("_date")

    included = [n for n in FRAMEWORKS if subs[n]["included"]]
    excluded = [n for n in FRAMEWORKS if not subs[n]["included"]]
    stale = [n for n in FRAMEWORKS if subs[n]["stale"]]

    return {
        "merrill": subs["merrill"],
        "credit": subs["credit"],
        "inventory": subs["inventory"],
        "debt": subs["debt"],
        "macro_score": compute_macro_score(
            db_path, frames["merrill"], frames["credit"],
            frames["inventory"], frames["debt"],
        ),
        "cross_lags": {
            "m1_ppi_best_lag": cross["m1_ppi_best_lag"],
            "m1_ppi_max_corr": round(cross["m1_ppi_max_corr"], 3) if pd.notna(cross["m1_ppi_max_corr"]) else None,
            "spread_cpi_best_lag": cross["spread_cpi_best_lag"],
            "spread_cpi_max_corr": round(cross["spread_cpi_max_corr"], 3) if pd.notna(cross["spread_cpi_max_corr"]) else None,
        },
        "as_of": _fmt(frontier, "%Y-%m-%d"),
        "included": included,
        "excluded": excluded,
        "stale": stale,
    }


if __name__ == "__main__":
    import json
    result = compute_signals("data/macro_data.db")
    print(json.dumps(result, indent=2, default=str))
