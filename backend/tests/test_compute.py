"""Compute facts are isolated, versioned and calculated only on comparable inputs."""
import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core import auth, compute_analysis as analysis, compute_collect as collect, compute_service as service, compute_store as store
from backend.app.main import app
from backend.app.schemas.compute import Observation, ObservationBatch


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "compute.db")
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "token")
    monkeypatch.setattr(auth, "_TOKEN", None)


def point(**changes):
    return dict(entity="NVDA", metric="revenue", period_start="2025-01-01", period_end="2025-03-31",
                frequency="quarterly", kind="actual", value=100, unit="currency", currency="USD", basis="US-GAAP",
                scope="consolidated", source_url="https://example.com/official", source_locator="financial results table",
                source_excerpt="Official consolidated financial results", published_at="2025-05-01", method="agent",
                source_id="official:test", definition_version="v1", observed_at="2025-05-01") | changes


def batch(*points):
    return ObservationBatch.model_validate({"observations": list(points)})


@pytest.mark.parametrize("changes", [
    {"value": float("nan")}, {"value": float("inf")}, {"unit": "millions"}, {"entity": "UNKNOWN"},
    {"source_url": "javascript:alert(1)"}, {"source_url": "https://user:password@example.com"},
    {"source_excerpt": ""}, {"currency": "TWD"}, {"published_at": "2099-01-01"},
    {"period_start": "2024-01-01"}, {"period_end": "2025-07-01"},
    {"metric": "dio", "unit": "days"},
    {"metric": "eps_consensus", "unit": "currency/share", "kind": "consensus", "basis": "unknown"},
])
def test_observation_rejects_ambiguous_or_impossible(changes):
    with pytest.raises(ValidationError):
        Observation.model_validate(point(**changes))


def test_dry_run_atomicity_and_idempotent_versions():
    p = point()
    assert store.import_batch(batch(p), dry_run=True)["dry_run"]
    assert store.observations() == []
    assert store.import_batch(batch(p))["inserted"] == 1
    assert store.import_batch(batch(p))["inserted"] == 0
    revision = point(value=120, published_at="2025-06-01")
    store.import_batch(batch(revision))
    assert store.observations()[0]["value"] == 120
    assert store.observations("2025-05-10")[0]["value"] == 100
    with pytest.raises(ValidationError):
        store.import_batch(batch(point(value=130), point(unit="bad")))
    assert store.observations()[0]["value"] == 120


def test_scopes_and_basis_are_not_overwritten():
    store.import_batch(batch(point(), point(scope="Data Center", value=90), point(basis="non-GAAP", value=105)))
    assert len(store.observations()) == 3


def test_long_rental_history_is_not_truncated_or_spliced_into_quotes():
    start = date(2023, 6, 13)
    points = [point(entity="SEMIANALYSIS_A100", metric="gpu_rental_composite", unit="USD/GPU-hour",
                    period_start=None, period_end=(start + timedelta(days=i)).isoformat(), frequency="daily",
                    basis="reported", value=2, published_at="2025-05-01") for i in range(650)]
    store.import_batch(batch(*points))
    d = analysis.overview(as_of="2025-05-01", months=60)
    p = next(p for p in d["panels"] if p["id"] == "rental")
    s = next(s for s in p["series"] if s["metric"] == "gpu_rental_composite")
    assert len(s["points"]) == 650 and s["points"][0]["period_end"] == "2023-06-13"
    assert len(p["charts"]) == 2
    assert p["charts"][0][1] == ["gpu_hour_price"]
    assert p["charts"][1][1] == ["gpu_rental_composite"]
    full = analysis.overview(as_of="2025-05-01")
    limited = analysis.overview(as_of="2025-05-01", months=12)
    full_rental = next(p for p in full["panels"] if p["id"] == "rental")
    recent_rental = next(p for p in limited["panels"] if p["id"] == "rental")
    assert len(full_rental["series"][0]["points"]) == 650
    assert len(recent_rental["series"][0]["points"]) < 650
    assert full["data_hash"] == limited["data_hash"]


def test_guidance_vintages_keep_same_target():
    original = point(metric="revenue_guidance", kind="guidance", period_start="2025-07-01", period_end="2025-09-30")
    store.import_batch(batch(original, original | {"published_at": "2025-06-01", "value": 95}))
    assert len(store.observations()) == 1
    assert [p["value"] for p in store.expectation_vintages()] == [100, 95]


def test_monthly_three_months_is_ratio_of_sums_and_no_gap_fill():
    values = [(2024, 1, 10), (2024, 2, 20), (2024, 3, 70), (2025, 1, 30), (2025, 2, 30), (2025, 3, 90)]
    points = [point(entity="TW6669", metric="monthly_revenue", currency="TWD", basis="TIFRS", value=v,
                    period_start=None, period_end=f"{y}-{m:02d}-28", frequency="monthly") for y, m, v in values]
    result = analysis.calculate(points)
    growth = [p for p in result if p["metric"] == "monthly_revenue_3m_yoy"]
    assert len(growth) == 1 and growth[0]["value"] == 50
    assert not [p for p in analysis.calculate(points[:-2] + points[-1:]) if p["metric"] == "monthly_revenue_3m_yoy"]


def test_manufacturing_order_history_keeps_missing_revenue_and_odm_monthly_views():
    history = Path(__file__).resolve().parents[2] / "data/ai_compute_evidence/2026-11-19-manufacturing-orders.json"
    store.import_batch(ObservationBatch.model_validate_json(history.read_text()))
    store.import_batch(batch(point(entity="TW6669", metric="monthly_revenue", frequency="monthly",
                                   currency="TWD", period_start=None, scope="consolidated")))
    panels = analysis.overview(as_of="2026-11-19")["panels"]
    manufacturing = next(p for p in panels if p["id"] == "manufacturing")
    assert manufacturing["entities"][0] == "DELL"
    assert manufacturing["charts"][0] == ("订单与排期", ["new_orders", "backlog", "ai_server_revenue"])
    series = {(s["entity"], s["metric"]): s for s in manufacturing["series"]}
    for metric, count, latest in [("new_orders", 11, 60.9e9), ("backlog", 12, 95e9), ("ai_server_revenue", 10, 16.4e9)]:
        rows = series["DELL", metric]["points"]
        assert len(rows) == count
        assert rows[-1]["period_end"] == "2026-07-31" and rows[-1]["value"] == latest
    assert series["DELL", "ai_server_revenue"]["points"][0]["period_end"] == "2024-05-03"
    assert len(series["HPE", "backlog"]["points"]) == len(series["HPE", "new_orders"]["points"]) == 8
    assert ("HPE", "ai_server_revenue") not in series
    assert ("TW6669", "monthly_revenue") in series and ("TW6669", "backlog") not in series


def test_turnover_uses_average_stock_and_same_period_cost():
    points = [point(metric="inventory_total", period_start=None, period_end="2024-12-31", value=100),
              point(metric="inventory_total", period_start=None, value=200),
              point(metric="cost_of_revenue", value=300), point(value=600)]
    dio = next(p for p in analysis.calculate(points) if p["metric"] == "dio")
    assert dio["value"] == 45  # average150 / cost300 * actual90 days, not revenue600
    assert not [p for p in analysis.calculate(points[1:]) if p["metric"] == "dio"]
    points[2]["value"] = 0
    assert not [p for p in analysis.calculate(points) if p["metric"] == "dio"]


def test_eps_nonpositive_base_not_percentage_and_scope_break():
    rows = [point(metric="eps", unit="currency/share", value=-1, period_start="2024-01-01", period_end="2024-03-31"),
            point(metric="eps", unit="currency/share", value=1)]
    assert not [p for p in analysis.calculate(rows) if p["metric"] == "eps_yoy"]
    rows[0]["value"] = 1
    rows[1]["definition_version"] = "reclassified"
    assert not [p for p in analysis.calculate(rows) if p["metric"] == "eps_yoy"]


def sec_payload(metric, facts):
    return {"facts": {"us-gaap": {metric: {"units": {"USD": facts}}}}}


def fact(value, start, end, **kw):
    return dict(val=value, start=start, end=end, filed="2025-11-01", form="10-Q", accn="0000") | kw


def test_sec_differences_ytd_flows_not_eps_or_stocks():
    facts = [fact(10, "2025-01-01", "2025-03-31"), fact(28, "2025-01-01", "2025-06-30"), fact(60, "2025-01-01", "2025-09-30")]
    rows = collect.sec_points("NVDA", sec_payload("PaymentsToAcquirePropertyPlantAndEquipment", facts), as_of="2025-11-02")
    assert [(p.period_end.isoformat(), p.value) for p in rows] == [("2025-03-31", 10), ("2025-06-30", 18), ("2025-09-30", 32)]
    facts.append(fact(34, "2025-07-01", "2025-09-30"))
    rows = collect.sec_points("NVDA", sec_payload("PaymentsToAcquirePropertyPlantAndEquipment", facts), as_of="2025-11-02")
    assert next(p.value for p in rows if p.period_end.month == 9) == 34
    payload = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": facts[:3]}}}}}
    assert len(collect.sec_points("NVDA", payload, as_of="2025-11-02")) == 1


def test_sec_future_filing_is_not_actual_and_original_currency_only():
    with pytest.raises(ValueError):
        collect.sec_points("NVDA", sec_payload("Revenues", [fact(10, "2025-01-01", "2025-03-31", filed="2025-12-01")]), as_of="2025-11-02")
    with pytest.raises(ValueError):
        collect.sec_points("TSM", sec_payload("Revenues", [fact(10, "2025-01-01", "2025-03-31")]), as_of="2025-11-02")


def test_amazon_cash_capex_uses_issuer_verified_gross_purchases_tag():
    ytd = [fact(-421, "2025-01-01", "2025-03-31"), fact(-963, "2025-01-01", "2025-06-30")]
    payload = sec_payload("PaymentsToAcquireProductiveAssets", ytd.copy())
    payload["facts"]["us-gaap"].update(sec_payload("NetIncomeLoss", ytd)["facts"]["us-gaap"])
    rows = collect.sec_points("AMZN", payload, as_of="2025-11-02")
    capex = [p for p in rows if p.metric == "cash_capex"]
    assert [(p.period_end.month, p.value) for p in capex] == [(3, 421), (6, 542)]
    assert "PaymentsToAcquireProductiveAssets" in capex[-1].source_locator
    assert all(p.metric != "cash_capex" for p in collect.sec_points("META", payload, as_of="2025-11-02"))
    # A cumulative figure with a different tag does not provide a matching base.
    payload["facts"]["us-gaap"]["PaymentsToAcquireProductiveAssets"]["units"]["USD"].pop(0)
    payload["facts"]["us-gaap"].update(sec_payload("PaymentsToAcquirePropertyPlantAndEquipment", ytd[:1])["facts"]["us-gaap"])
    assert not any(p.metric == "cash_capex" and p.period_end.month == 6
                   for p in collect.sec_points("AMZN", payload, as_of="2025-11-02"))


def test_tw_parser_requires_real_table_date_and_converts_thousands():
    page = '<html>114年3月份 出表日期：114/4/10<table><tr><td>6669</td><td>緯穎</td><td>1,234</td><td>0</td><td>0</td></tr></table></html>'
    rows = collect.parse_tw(page, 2025, 3, "https://mopsov.twse.com.tw/test")
    assert rows[0].value == 1234000 and rows[0].currency == "TWD"
    with pytest.raises(ValueError):
        collect.parse_tw(page.replace("出表日期：114/4/10", ""), 2025, 3, "https://mopsov.twse.com.tw/test")
    with pytest.raises(ValueError):
        collect.parse_tw("<h1>Verification required</h1>", 2025, 3, "https://mopsov.twse.com.tw/test")


def test_runpod_fixed_secure_quote_not_utilization(monkeypatch):
    monkeypatch.setattr(collect, "fetch", lambda *a, **kw: {"data": {"gpuTypes": [
        {"displayName": "H100 SXM", "secureCloud": True, "securePrice": 2.5, "id": "h100"},
        {"displayName": "H200 SXM", "secureCloud": False, "securePrice": 1.0, "id": "h200"}]}})
    rows = collect.collect_gpu()
    assert len(rows) == 1 and rows[0].entity == "RUNPOD_H100_SXM" and rows[0].value == 2.5
    assert rows[0].metric == "gpu_hour_price"


def test_azure_excludes_windows_spot_and_divides_gpu_count(monkeypatch):
    linux = dict(type="Consumption", unitOfMeasure="1 Hour", currencyCode="USD", isPrimaryMeterRegion=True,
                 productName="Virtual Machines ND H100 v5 Series Linux", skuName="ND96isr H100 v5", meterName="ND96isr H100 v5",
                 retailPrice=80, meterId="linux", effectiveStartDate="2025-01-01", armSkuName="Standard_ND96isr_H100_v5", armRegionName="eastus")
    monkeypatch.setattr(collect, "fetch", lambda *a, **kw: {"Items": [linux, linux | {"skuName": "Spot"}, linux | {"productName": "Windows"}]})
    assert collect.collect_azure()[0].value == 10



def make_report():
    from backend.app.schemas.compute import AgentReport
    return AgentReport(data_hash=analysis.overview()["data_hash"], as_of="2025-05-01", agent="Local Test Agent",
        overall="Evidence grounded conclusion written by the local Agent, not the web application.",
        summary=[{"id": k, "headline": "Evidence based headline", "text": "Evidence grounded conclusion"} for k in ["earnings", "outlook", "conditions"]],
        panels={k: {"headline": "Evidence based headline", "evidence": "Evidence grounded panel interpretation", "watch": "Next verification conditions"} for k in ["earnings", "demand", "manufacturing", "rental", "power", "quality"]},
        sources=[{"title": "Official quarterly results", "url": "https://example.com/official"}])


def test_read_only_web_and_no_automatic_conclusions():
    client = TestClient(app)
    store.import_batch(batch(point()))
    d = client.get("/api/v1/compute/overview").json()
    assert len(d["panels"]) == 6 and d["analysis"] is None
    assert "summary" not in d and all("interpretation" not in p for p in d["panels"])
    for path in ["collect", "analyze", "import", "prompt"]:
        assert client.post("/api/v1/compute/" + path, json={}).status_code in {404, 405}
    assert not hasattr(service, "analyze") and not hasattr(service, "call_chat")


def test_local_report_preview_publish_and_staleness():
    store.import_batch(batch(point()))
    report = make_report()
    assert service.publish(report)["dry_run"] and store.report() is None
    service.publish(report, dry_run=False)
    d = analysis.overview()
    assert d["analysis"]["payload"]["agent"] == "Local Test Agent" and not d["analysis"]["stale"]
    store.import_batch(batch(point(value=123, published_at="2025-06-01")))
    assert analysis.overview()["analysis"]["stale"]
    with pytest.raises(ValueError):
        service.publish(report, dry_run=False)


def test_old_hosted_model_reports_never_display():
    store.save_report("oldhash", "hosted-model", {"overall": "old machine text", "panels": {}})
    assert store.report() is None and analysis.overview()["analysis"] is None


def test_report_must_include_three_distinct_judgments_and_all_sections():
    report = make_report().model_dump()
    from backend.app.schemas.compute import AgentReport
    report["summary"][0]["id"] = "outlook"
    with pytest.raises(ValidationError):
        AgentReport.model_validate(report)
    report = make_report().model_dump()
    report["panels"].pop("rental")
    with pytest.raises(ValidationError):
        AgentReport.model_validate(report)


def test_report_requires_named_sources_and_structured_agent_interpretation():
    from backend.app.schemas.compute import AgentReport
    for changes in [
        {"sources": ["https://example.com/unnamed"]},
        {"sources": [{"title": "Named source", "url": "http://example.com"}]},
        {"panels": make_report().model_dump()["panels"] | {"power": "Unstructured old text"}},
    ]:
        with pytest.raises(ValidationError):
            AgentReport.model_validate(make_report().model_dump() | changes)


def test_rental_history_keeps_weekly_observations_and_licensing():
    payload = {"meta": {"license": "CC-BY-4.0", "slug": "nvidia-h100", "last_snapshot": "2025-05-01",
                        "provisional_week": "2025-04-28"},
               "data": [{"date": d, "billing_type": "ON_DEMAND", "reservation_months": None,
                         "median_price": p, "provider_count": 8, "offering_count": 20}
                        for d, p in [("2025-04-21", 2.5), ("2025-04-28", 2.6)]]}
    payload["data"].append(payload["data"][0] | {"billing_type": "SPOT", "median_price": .5})
    rows = collect.rental_history_points("GETDEPLOYING_H100", "nvidia-h100", payload)
    assert len(rows) == 2 and all(p.frequency == "weekly" for p in rows)
    assert rows[0].value == 2.5 and "provisional=True" in rows[1].source_excerpt
    payload["meta"]["license"] = "all rights reserved"
    with pytest.raises(ValueError):
        collect.rental_history_points("GETDEPLOYING_H100", "nvidia-h100", payload)


def test_guidance_cannot_be_mislabeled_actual():
    with pytest.raises(ValidationError):
        Observation.model_validate(point(metric="revenue_guidance"))
