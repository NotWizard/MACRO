"""Versioned macro expansion score.

The former composite added four categorical cycle phases on a [-4, +4] scale.
It was removed because it discarded magnitude, data freshness and the fact that
several frameworks share the same underlying indicators.

This module is the v1 replacement used by the overview.  It is deliberately
transparent and rule-based:

* five non-overlapping pillars: growth, credit, external demand, fiscal and
  housing;
* debt, inflation and property imbalance are a separate pressure score instead
  of being treated as bullish growth;
* every available sub-indicator is mapped to 0–100 with documented ranges;
* missing inputs are excluded from the score and reduce confidence;
* the result carries its version so old and new history are never mixed.

This is an expert-weighted v1.  Weights and mappings live at module scope and
must be frozen when a scored history row is written.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from typing import Iterable

import pandas as pd

from analysis.real_estate import analyze_real_estate

SCORE_VERSION = "macro-expansion-v1"

COMPONENT_WEIGHTS = {
    "growth": 0.30,
    "credit": 0.25,
    "external": 0.15,
    "fiscal": 0.10,
    "housing": 0.20,
}

COMPONENT_LABELS = {
    "growth": "实体增长",
    "credit": "信用条件",
    "external": "外需",
    "fiscal": "财政姿态",
    "housing": "房地产",
}

# Expected freshness half-life in months.  Annual/quarterly series are allowed to
# age more before they materially reduce confidence.
FRESHNESS_HALF_LIFE = {
    "growth": 3.0,
    "credit": 2.0,
    "external": 2.0,
    "fiscal": 3.0,
    "housing": 2.0,
}


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _scale(value: float | None, low: float, high: float) -> float | None:
    """Map ``low..high`` to ``0..100`` (the range may be descending)."""
    if value is None or pd.isna(value) or high == low:
        return None
    return _clip((float(value) - low) / (high - low) * 100.0)


def _read_table(db_path: str, table: str) -> pd.DataFrame:
    """Read an optional table without making a missing table fatal.

    The signal endpoint must stay usable with old/fresh databases.  Tests also
    construct intentionally minimal schemas, so callers must not assume every
    optional column exists.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            return pd.read_sql(f"SELECT * FROM {table}", conn)
        finally:
            conn.close()
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def _latest(df: pd.DataFrame, col: str) -> tuple[float | None, str | None]:
    if df.empty or col not in df.columns:
        return None, None
    rows = df.dropna(subset=[col]).sort_values("date")
    if rows.empty:
        return None, None
    row = rows.iloc[-1]
    return float(row[col]), str(row["date"])[:10]


def _month_gap(latest: str | None, frontier: str | None) -> int | None:
    if not latest or not frontier:
        return None
    try:
        a = datetime.fromisoformat(latest[:10])
        b = datetime.fromisoformat(frontier[:10])
    except ValueError:
        return None
    return max(0, (b.year - a.year) * 12 + b.month - a.month)


def _avg_parts(parts: Iterable[dict]) -> tuple[float | None, list[dict]]:
    """Weighted availability-aware average for sub-indicators."""
    usable = [p for p in parts if p["score"] is not None]
    if not usable:
        return None, []
    total = sum(float(p["weight"]) for p in usable)
    score = sum(float(p["weight"]) * float(p["score"]) for p in usable) / total
    return score, usable


def _part(name: str, label: str, value, score, weight: float,
          unit: str, as_of: str | None) -> dict:
    return {
        "name": name,
        "label": label,
        "value": None if value is None or (isinstance(value, float) and pd.isna(value)) else round(float(value), 3),
        "score": None if score is None else round(float(score), 1),
        "weight": weight,
        "unit": unit,
        "as_of": as_of,
    }


def _component(key: str, parts: list[dict]) -> dict:
    score, usable = _avg_parts(parts)
    weight = COMPONENT_WEIGHTS[key]
    return {
        "key": key,
        "label": COMPONENT_LABELS[key],
        "score": None if score is None else round(score, 1),
        "weight": weight,
        "contribution": 0.0 if score is None else round((score - 50.0) * weight, 2),
        "available": score is not None,
        "parts": parts,
        "as_of": max((p["as_of"] for p in usable if p["as_of"]), default=None),
    }


def _label(score: float | None) -> str:
    if score is None:
        return "数据不足"
    if score < 25:
        return "明显收缩"
    if score < 42:
        return "偏弱"
    if score < 55:
        return "弱复苏 · 结构分化"
    if score < 68:
        return "温和扩张"
    if score < 82:
        return "扩张偏强"
    return "强扩张"


def _price_pressure(cpi: float | None, ppi: float | None) -> float | None:
    parts: list[float] = []
    if cpi is not None:
        parts.append(_clip(abs(cpi - 2.0) * 32.0))
    if cpi is not None and ppi is not None:
        # A widening upstream/downstream gap squeezes margins even when CPI itself
        # is low.  Positive gaps are penalised; a large PPI deflation gap also
        # signals weak pricing power.
        parts.append(_clip(abs(ppi - cpi) * 14.0))
    return sum(parts) / len(parts) if parts else None


def _property_stress(new_yoy: float | None, used_yoy: float | None,
                     breadth: float | None) -> float | None:
    parts: list[float] = []
    if new_yoy is not None:
        parts.append(_scale(new_yoy, -5.0, 2.0))
    if used_yoy is not None:
        parts.append(_scale(used_yoy, -8.0, 2.0))
    if breadth is not None:
        parts.append(_clip(breadth * 100.0))
    return 100.0 - (sum(parts) / len(parts)) if parts else None


def _freshness(components: list[dict], frontier: str | None) -> float:
    vals: list[tuple[float, float]] = []
    for comp in components:
        if not comp["available"]:
            continue
        age = _month_gap(comp["as_of"], frontier)
        if age is None:
            continue
        half_life = FRESHNESS_HALF_LIFE[comp["key"]]
        vals.append((comp["weight"], math.exp(-age / half_life)))
    if not vals:
        return 0.0
    return sum(w * f for w, f in vals) / sum(w for w, _ in vals)


def _agreement(components: list[dict]) -> float:
    rows = [(c["weight"], c["score"]) for c in components
            if c["available"] and c["score"] is not None]
    if not rows:
        return 0.0
    mean = sum(w * s for w, s in rows) / sum(w for w, _ in rows)
    variance = sum(w * (s - mean) ** 2 for w, s in rows) / sum(w for w, _ in rows)
    # 20 points of weighted dispersion is already substantial disagreement.
    return _clip(1.0 - math.sqrt(variance) / 35.0, 0.0, 1.0)


def _pos_neg(components: list[dict]) -> tuple[list[str], list[str]]:
    usable = [c for c in components if c["available"]]
    positive = sorted(usable, key=lambda c: c["score"] - 50.0, reverse=True)
    negative = sorted(usable, key=lambda c: c["score"] - 50.0)
    pos = [f"{c['label']} {c['score']:.1f}/100" for c in positive[:2]
           if c["score"] > 52]
    neg = [f"{c['label']} {c['score']:.1f}/100" for c in negative[:2]
           if c["score"] < 48]
    return pos, neg


def compute_macro_score(
    db_path: str,
    merrill_df: pd.DataFrame,
    credit_df: pd.DataFrame,
    inventory_df: pd.DataFrame,
    debt_df: pd.DataFrame,
) -> dict:
    """Build the versioned expansion/pressure/confidence result.

    ``*_df`` are the already-computed cycle frames.  Reusing them avoids reading
    the same tables twice and keeps the score aligned with the page's visible
    cycle classifiers.
    """
    monthly = _read_table(db_path, "derived_monthly")
    fiscal = _read_table(db_path, "fiscal")
    external = _read_table(db_path, "external_demand")

    # ── growth ──────────────────────────────────────────────────────────────
    m_last = merrill_df.iloc[-1] if not merrill_df.empty else None
    gdp_gap = None
    gdp_as_of = None
    if m_last is not None and pd.notna(m_last.get("gdp_yoy")) and pd.notna(m_last.get("gdp_trend")):
        gdp_gap = float(m_last["gdp_yoy"]) - float(m_last["gdp_trend"])
        gdp_as_of = str(m_last["date"])[:10]

    i_last = inventory_df.iloc[-1] if not inventory_df.empty else None
    pmi = float(i_last["pmi_used"]) if i_last is not None and pd.notna(i_last.get("pmi_used")) else None
    pmi_as_of = str(i_last["date"])[:10] if i_last is not None else None
    ip_gap = None
    if i_last is not None and pd.notna(i_last.get("ip_yoy")) and pd.notna(i_last.get("ip_trend")):
        ip_gap = float(i_last["ip_yoy"]) - float(i_last["ip_trend"])

    growth = _component("growth", [
        _part("gdp_gap", "GDP 相对潜在增长", gdp_gap, _scale(gdp_gap, -2.0, 2.0),
              0.35, "pp", gdp_as_of),
        _part("pmi", "官方制造业 PMI", pmi, _scale(pmi, 45.0, 55.0),
              0.35, "点", pmi_as_of),
        _part("ip_gap", "工业增加值相对趋势", ip_gap, _scale(ip_gap, -3.0, 3.0),
              0.30, "pp", pmi_as_of),
    ])

    # ── credit ──────────────────────────────────────────────────────────────
    c_last = credit_df.iloc[-1] if not credit_df.empty else None
    impulse = float(c_last["credit_impulse"]) if c_last is not None and pd.notna(c_last.get("credit_impulse")) else None
    credit_as_of = str(c_last["date"])[:10] if c_last is not None else None
    spread, spread_as_of = _latest(monthly, "m2_m1_spread")
    sf_yoy, sf_as_of = _latest(monthly, "sf_stock_yoy")
    loan_yoy, loan_as_of = _latest(monthly, "loan_stock_yoy")
    credit = _component("credit", [
        _part("credit_impulse", "M2 信贷脉冲", impulse, _scale(impulse, -3.0, 3.0),
              0.40, "pp", credit_as_of),
        _part("m2_m1_spread", "M2-M1 剪刀差", spread, _scale(spread, 10.0, -2.0),
              0.20, "pp", spread_as_of),
        _part("sf_flow_yoy", "12个月社融增量同比", sf_yoy, _scale(sf_yoy, -20.0, 20.0),
              0.20, "%", sf_as_of),
        _part("loan_flow_yoy", "12个月新增贷款同比", loan_yoy, _scale(loan_yoy, -30.0, 20.0),
              0.20, "%", loan_as_of),
    ])

    # ── external ────────────────────────────────────────────────────────────
    export_3m = None
    export_as_of = None
    if not external.empty and "exports_yoy" in external.columns:
        ex = external.dropna(subset=["exports_yoy"]).sort_values("date").tail(3)
        if not ex.empty:
            export_3m = float(ex["exports_yoy"].mean())
            export_as_of = str(ex.iloc[-1]["date"])[:10]
    ism, ism_as_of = _latest(external, "us_ism_pmi")
    external_comp = _component("external", [
        _part("exports_3m", "出口同比（近3月均值）", export_3m, _scale(export_3m, -10.0, 30.0),
              0.55, "%", export_as_of),
        _part("us_ism", "美国 ISM 制造业 PMI", ism, _scale(ism, 45.0, 55.0),
              0.45, "点", ism_as_of),
    ])

    # ── fiscal ──────────────────────────────────────────────────────────────
    rev, rev_as_of = _latest(fiscal, "revenue_cum_yoy")
    spend, spend_as_of = _latest(fiscal, "expenditure_cum_yoy")
    spend_minus_rev = spend - rev if spend is not None and rev is not None else None
    fiscal_comp = _component("fiscal", [
        _part("fiscal_impulse", "支出同比−收入同比", spend_minus_rev,
              _scale(spend_minus_rev, -10.0, 10.0), 1.0, "pp",
              max((d for d in (rev_as_of, spend_as_of) if d), default=None)),
    ])

    # ── housing ─────────────────────────────────────────────────────────────
    assessment = analyze_real_estate(db_path).get("assessment", {})
    new_mom = assessment.get("price_mom_12m")
    new_mom_score = (
        assessment.get("price_momentum_score")
        if assessment.get("price_momentum_available") else None
    )
    hp = _read_table(db_path, "house_price")
    used_mom = None
    used_as_of = None
    breadth = None
    breadth_as_of = None
    if not hp.empty and {"date", "new_mom"}.issubset(hp.columns):
        by_date = hp.groupby("date", dropna=True)
        agg = by_date.agg(new_mom=("new_mom", "mean"),
                          breadth_new=("new_mom", lambda s: float((s > 100).mean())))
        if "used_mom" in hp.columns:
            agg["used_mom"] = by_date["used_mom"].mean()
            used_series = agg["used_mom"].dropna()
            if not used_series.empty:
                used_mom = float(used_series.tail(12).mean())
                used_as_of = str(used_series.index[-1])[:10]
        breadth_series = agg["breadth_new"].dropna()
        if not breadth_series.empty:
            breadth = float(breadth_series.iloc[-1])
            breadth_as_of = str(breadth_series.index[-1])[:10]
    housing = _component("housing", [
        _part("new_price_momentum", "新房环比12月均值", new_mom,
              float(new_mom_score) if new_mom_score is not None else _scale(new_mom, 99.0, 101.0),
              0.35, "指数", assessment.get("as_of_price")),
        _part("used_price_momentum", "二手房环比12月均值", used_mom,
              _scale(used_mom, 99.0, 101.0), 0.35, "指数", used_as_of),
        _part("new_price_breadth", "新房环比上涨城市占比", breadth,
              _clip(breadth * 100.0) if breadth is not None else None,
              0.30, "%", breadth_as_of),
    ])

    components = [growth, credit, external_comp, fiscal_comp, housing]
    available = [c for c in components if c["available"]]
    avail_weight = sum(c["weight"] for c in available)
    score = (
        sum(c["score"] * c["weight"] for c in available) / avail_weight
        if avail_weight else None
    )
    coverage = avail_weight
    frontier = max((c["as_of"] for c in available if c["as_of"]), default=None)
    fresh = _freshness(components, frontier)
    agreement = _agreement(components)
    # Disagreement must materially reduce confidence: a fully covered but highly
    # contradictory cross-section is not a high-confidence regime.
    confidence = 100.0 * coverage * (0.25 + 0.35 * fresh + 0.40 * agreement)

    cpi, _ = _latest(monthly, "cpi_yoy")
    ppi, _ = _latest(monthly, "ppi_yoy")
    pressure_price = _price_pressure(cpi, ppi)
    pressure_credit_parts = [
        _clip(-impulse / 3.0 * 100.0) if impulse is not None else None,
        _clip(-sf_yoy / 20.0 * 100.0) if sf_yoy is not None else None,
        _clip(-loan_yoy / 20.0 * 100.0) if loan_yoy is not None else None,
    ]
    credit_known = [x for x in pressure_credit_parts if x is not None]
    pressure_credit = sum(credit_known) / len(credit_known) if credit_known else None
    latest_q = _read_table(db_path, "derived_quarterly")
    debt_to_income, _ = _latest(latest_q, "hh_debt_to_income")
    d_last = debt_df.iloc[-1] if not debt_df.empty else None
    corp_change = abs(float(d_last["net_change"])) if d_last is not None and pd.notna(d_last.get("net_change")) else None
    pressure_debt_parts = [
        _scale(debt_to_income, 100.0, 180.0) if debt_to_income is not None else None,
        _scale(corp_change, 0.0, 12.0) if corp_change is not None else None,
    ]
    debt_known = [x for x in pressure_debt_parts if x is not None]
    pressure_debt = sum(debt_known) / len(debt_known) if debt_known else None
    new_yoy = None
    used_yoy = None
    if not hp.empty and "used_yoy" in hp.columns:
        latest_hp = hp.sort_values("date").iloc[-1]["date"]
        row = hp[hp["date"] == latest_hp]
        if not row.empty:
            new_yoy = float(row["new_yoy"].mean()) - 100.0 if "new_yoy" in row else None
            used_yoy = float(row["used_yoy"].mean()) - 100.0 if "used_yoy" in row else None
    pressure_housing = _property_stress(new_yoy, used_yoy, breadth)
    pressure_parts = [
        (pressure_price, 0.30),
        (pressure_credit, 0.25),
        (pressure_debt, 0.25),
        (pressure_housing, 0.20),
    ]
    pressure_total = sum(w for v, w in pressure_parts if v is not None)
    pressure = (
        sum(v * w for v, w in pressure_parts if v is not None) / pressure_total
        if pressure_total else None
    )

    pos, neg = _pos_neg(components)
    warnings: list[str] = []
    caixin_raw = _read_table(db_path, "pmi")
    _, caixin_as_of = _latest(caixin_raw, "pmi_caixin")
    frontier_dt = frontier
    if caixin_as_of and frontier_dt and (_month_gap(caixin_as_of, frontier_dt) or 0) > 2:
        warnings.append("财新 PMI 已超过两个数据月未更新，本次不计入扩张度")
    if export_as_of and frontier_dt and (_month_gap(export_as_of, frontier_dt) or 0) > 1:
        warnings.append("进出口最新完整数据早于 ISM，外需分项使用最近可得月份")
    if any(not c["available"] for c in components):
        warnings.append("部分一级维度无可用数据，分数按已覆盖维度重算")

    return {
        "version": SCORE_VERSION,
        "score": None if score is None else round(score, 1),
        "label": _label(score),
        "pressure": None if pressure is None else round(pressure, 1),
        "pressure_components": {
            "price": None if pressure_price is None else round(pressure_price, 1),
            "credit": None if pressure_credit is None else round(pressure_credit, 1),
            "debt": None if pressure_debt is None else round(pressure_debt, 1),
            "housing": None if pressure_housing is None else round(pressure_housing, 1),
        },
        "confidence": round(_clip(confidence), 1),
        "freshness": round(fresh * 100.0, 1),
        "agreement": round(agreement * 100.0, 1),
        "coverage": round(coverage * 100.0, 1),
        "as_of": frontier,
        "components": {c["key"]: c for c in components},
        "positives": pos,
        "negatives": neg,
        "warnings": warnings,
        "weights": dict(COMPONENT_WEIGHTS),
        "method": "expert-weighted robust score; 50=neutral; five non-overlapping pillars",
    }
