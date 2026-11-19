"""Fiscal-period calculations and facts for a read-only Agent-authored panorama."""
from __future__ import annotations

import calendar
import hashlib
import json
import math
from collections import defaultdict
from datetime import date

from backend.app.core import compute_store as store
from backend.app.core.compute_catalog import ENTITIES, METRICS, PANELS, SOURCES


def _date(s):
    return date.fromisoformat(s[:10])


def _derived(base, metric, value, inputs, formula):
    if value is None or not math.isfinite(value):
        return None
    return {**base, "metric": metric, "value": round(value, 6), "unit": METRICS[metric]["unit"],
            "method": "calculated", "source_locator": formula,
            "published_at": max(p["published_at"] for p in inputs),
            "observed_at": max(p["observed_at"] for p in inputs),
            "source_urls": list(dict.fromkeys(p["source_url"] for p in inputs)),
            "source_excerpt": "程序按同公司、同口径原始数据计算；所需基期缺失则不生成。"}


def calculate(points):
    result = list(points)
    groups = defaultdict(dict)
    for p in points:
        if p["kind"] != "actual":
            continue
        key = (p["entity"], p["scope"], p["basis"], p["currency"], p["frequency"], p["definition_version"])
        # Stock fields have no start date. Statement flows are matched by end and scope.
        groups[key].setdefault(p["metric"], {})[p["period_end"]] = p
    for key, metrics in groups.items():
        frequency = key[4]
        for raw, out in [("revenue", "revenue_yoy"), ("eps", "eps_yoy"), ("segment_revenue", "segment_revenue_yoy"),
                         ("cloud_revenue", "cloud_revenue_yoy"), ("monthly_revenue", "monthly_revenue_yoy")]:
            rows = sorted(metrics.get(raw, {}).values(), key=lambda p: p["period_end"])
            for p in rows:
                t = _date(p["period_end"])
                # Fiscal years need not end on identical calendar dates; match within 15 days.
                candidates = [x for x in rows if 350 <= (t - _date(x["period_end"])).days <= 380]
                if not candidates:
                    continue
                old = min(candidates, key=lambda x: abs((t - _date(x["period_end"])).days - 365))
                if old["value"] <= 0:
                    continue
                if frequency == "monthly" and (t.month != _date(old["period_end"]).month):
                    continue
                q = _derived(p, out, (p["value"] / old["value"] - 1) * 100, [p, old], "同口径本期 / 上年同期 − 1；单位 %")
                if p["period_end"] not in metrics.get(out, {}):
                    result.append(q)
        monthly = metrics.get("monthly_revenue", {})
        if monthly:
            by_month = {(int(d[:4]), int(d[5:7])): p for d, p in monthly.items()}
            for (y, m), p in by_month.items():
                n = y * 12 + m - 1
                indexes = [n, n - 1, n - 2, n - 12, n - 13, n - 14]
                six = [by_month.get((i // 12, i % 12 + 1)) for i in indexes]
                if all(six):
                    prev = sum(x["value"] for x in six[3:])
                    if prev > 0:
                        result.append(_derived(p, "monthly_revenue_3m_yoy", (sum(x["value"] for x in six[:3]) / prev - 1) * 100,
                                               six, "最近三月合计 / 上年相同三月合计 − 1；单位 %"))
        if frequency != "quarterly":
            continue
        for end, p in metrics.get("revenue", {}).items():
            cost = metrics.get("cost_of_revenue", {}).get(end)
            gp = metrics.get("gross_profit", {}).get(end)
            op = metrics.get("operating_income", {}).get(end)
            if p["value"] > 0:
                if gp or cost:
                    inputs = [p, gp or cost]
                    value = gp["value"] if gp else p["value"] - cost["value"]
                    if end not in metrics.get("gross_margin", {}):
                        result.append(_derived(p, "gross_margin", value / p["value"] * 100, inputs, "报告毛利 / 营收 × 100"))
                if op and end not in metrics.get("operating_margin", {}):
                    result.append(_derived(p, "operating_margin", op["value"] / p["value"] * 100, [p, op], "营业利润 / 营收 × 100"))
            if not p.get("period_start"):
                continue
            days = (_date(end) - _date(p["period_start"])).days + 1
            for stock, denom, metric in [("inventory_total", cost, "dio"), ("trade_receivables", p, "dso")]:
                current = metrics.get(stock, {}).get(end)
                previous = [x for d, x in metrics.get(stock, {}).items()
                            if abs((_date(p["period_start"]) - _date(d)).days - 1) <= 7]
                if current and previous and denom and denom["value"] > 0:
                    old = max(previous, key=lambda x: x["period_end"])
                    result.append(_derived(p, metric, (old["value"] + current["value"]) / 2 / denom["value"] * days,
                                           [old, current, denom], "期初期末平均余额 / 同期成本或收入 × 财季实际天数"))
        for end, revenue in metrics.get("cloud_revenue", {}).items():
            profit = metrics.get("cloud_operating_income", {}).get(end)
            if profit and revenue["value"] > 0 and end not in metrics.get("cloud_operating_margin", {}):
                result.append(_derived(revenue, "cloud_operating_margin", profit["value"] / revenue["value"] * 100,
                                       [revenue, profit], "同披露范围云营业利润 / 云收入 × 100"))
    # API and original tables may duplicate a derived reported ratio; keep original.
    return [p for p in result if p is not None]


def _series(points):
    grouped = defaultdict(list)
    for p in points:
        key = (p["entity"], p["metric"], p["kind"], p["basis"], p["scope"], p["currency"], p["frequency"], p["definition_version"])
        grouped[key].append(p)
    rows = []
    for key, ps in grouped.items():
        entity, metric, kind, basis, scope, currency, frequency, version = key
        ordered = sorted(ps, key=lambda x: (x["period_end"], x["published_at"]))
        by_end = {p["period_end"]: p for p in ordered}
        ordered = list(by_end.values())
        max_age = {"daily": 7, "monthly": 75, "quarterly": 200, "weekly": 21, "annual": 450, "event": 365}[frequency]
        stale = kind == "actual" and (store.today() - _date(ordered[-1]["period_end"])).days > max_age
        rows.append(dict(id=hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16],
                         entity=entity, metric=metric, kind=kind, basis=basis, scope=scope,
                         currency=currency, unit=METRICS[metric]["unit"], frequency=frequency,
                         definition_version=version, stale=stale, points=ordered))
    return rows


def overview(as_of=None, months=None):
    raw = store.observations(as_of)
    cutoff = _date(as_of or store.today().isoformat())
    points = calculate(raw)
    if months is not None:
        n = cutoff.year * 12 + cutoff.month - 1 - months
        y, m = divmod(n, 12)
        lower = date(y, m + 1, min(cutoff.day, calendar.monthrange(y, m + 1)[1])).isoformat()
        points = [p for p in points if p["period_end"] >= lower]
    series = _series(points)
    panels = []
    for panel in PANELS:
        wanted = {m for _, ms in panel["charts"] for m in ms}
        ss = [s for s in series if s["entity"] in panel["entities"] and s["metric"] in wanted]
        panels.append({**panel, "series": ss,
                       "as_of": max((p["period_end"] for s in ss if s["kind"] == "actual" for p in s["points"]), default=None)})
    facts_hash = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report = store.report()
    return dict(as_of=cutoff.isoformat(), panels=panels, entities=ENTITIES, metrics=METRICS,
                sources=SOURCES, observation_count=len(raw), series_count=len(series),
                expectation_vintages=store.expectation_vintages(as_of), data_hash=facts_hash,
                analysis={**report, "stale": report["data_hash"] != facts_hash} if report else None)
