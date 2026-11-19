"""Local Agent publishing contract. No hosted model invocation or webpage writes."""
from backend.app.core import compute_analysis, compute_store as store
from backend.app.core.compute_catalog import ENTITIES, METRICS, PANELS, SOURCES
from backend.app.schemas.compute import ObservationBatch, AgentReport

def contract():
    snapshot = compute_analysis.overview(months=36)
    return dict(schema_version="ai-compute-v1", schema=ObservationBatch.model_json_schema(),
                report_schema=AgentReport.model_json_schema(), data_hash=snapshot["data_hash"],
                entities=ENTITIES, metrics=METRICS, sources=SOURCES,
                tasks=[{"panel": p["id"], "entities": p["entities"], "question": p["question"],
                        "coverage": [{"entity": s["entity"], "metric": s["metric"], "periods": len(s["points"]),
                                      "first": s["points"][0]["period_end"], "last": s["points"][-1]["period_end"],
                                      "frequency": s["frequency"], "scope": s["scope"]}
                                     for s in p["series"]]} for p in snapshot["panels"]])

def publish(report: AgentReport, *, dry_run=True):
    current = compute_analysis.overview(months=36)
    if report.data_hash != current["data_hash"]:
        raise ValueError("报告绑定的数据已变化，请重新读取 overview 核对后再发布")
    if report.as_of.isoformat() > current["as_of"]:
        raise ValueError("报告日期不能晚于当前可见日期")
    if not dry_run:
        store.save_report(report.data_hash, report.agent, report.model_dump(mode="json"))
        store.log("agent:report", "ok", message="本地 Agent 已发布经营判断及六板块解读")
    return {"status": "valid" if dry_run else "ok", "dry_run": dry_run, "panels": len(report.panels)}
