"""Validated observations shared by collectors, CLI and Agent imports."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.core.compute_catalog import ENTITIES, METRICS


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    metric: str
    period_start: date | None = None
    period_end: date
    frequency: Literal["quarterly", "annual", "monthly", "weekly", "daily", "event"]
    kind: Literal["actual", "guidance", "consensus", "estimate"] = "actual"
    value: float
    unit: str
    currency: Literal["USD", "TWD", "EUR", "KRW"] | None = None
    basis: Literal["US-GAAP", "IFRS", "TIFRS", "K-IFRS", "non-GAAP", "reported", "company-defined", "unknown"]
    scope: str = Field(min_length=2, max_length=500)
    source_url: str = Field(max_length=1800)
    source_locator: str = Field(min_length=2, max_length=1200)
    source_excerpt: str = Field(default="", max_length=2400)
    published_at: str
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    method: Literal["api", "parser", "agent"]
    source_id: str = Field(min_length=1, max_length=200)
    definition_version: str = Field(default="v1", min_length=1, max_length=100)

    @field_validator("value")
    @classmethod
    def finite(cls, value):
        if not math.isfinite(value):
            raise ValueError("数值必须有限；缺失数据请省略并在 notes 解释，不填零")
        return value

    @field_validator("source_url")
    @classmethod
    def source_http(cls, value):
        u = urlparse(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("来源须为无凭据的 HTTPS 原文 URL")
        return value

    @field_validator("published_at", "observed_at")
    @classmethod
    def iso_time(cls, value):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("时间须为 ISO 日期或时间") from exc
        if value[:10] > today().isoformat():
            raise ValueError("发布时间/采集时间不能在未来")
        return value

    @model_validator(mode="after")
    def contract(self):
        if self.entity not in ENTITIES or self.metric not in METRICS:
            raise ValueError("未知公司或指标，请先更新数据字典")
        m = METRICS[self.metric]
        if self.unit != m["unit"]:
            raise ValueError(f"{self.metric} 单位应为 {m['unit']}")
        original_usd_outlook = self.entity == "TSM" and self.kind == "guidance" and self.currency == "USD"
        if self.unit in {"currency", "currency/share"} and self.currency != ENTITIES[self.entity]["currency"] and not original_usd_outlook:
            raise ValueError("财务数据必须使用公司字典中的原币，不混入 ADR 或换算值")
        if m["nonnegative"] and self.value < 0:
            raise ValueError("此指标须为非负值；现金支出以正的支出额记录")
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("期间起点不得晚于期末")
        if self.kind == "actual" and self.period_end.isoformat() > self.published_at[:10]:
            raise ValueError("实际数据所属期不能晚于发布时间；未来目标请标 guidance")
        if self.frequency == "quarterly" and self.period_start:
            days = (self.period_end - self.period_start).days + 1
            if not 65 <= days <= 105:
                raise ValueError("季度流量必须是单季区间，累计值请先正确拆季")
        if self.metric == "eps_consensus" and (self.kind != "consensus" or self.basis == "unknown"):
            raise ValueError("EPS 共识必须有已核会计口径、固定目标期和供应商来源")
        if self.metric in {"revenue_guidance", "capex_guidance"} and self.kind != "guidance":
            raise ValueError("指引指标只能记录为 guidance，不能冒充实际业绩")
        if self.method == "agent":
            if len(self.source_excerpt.strip()) < 10:
                raise ValueError("Agent 数据须包含可复核原文摘录")
            if self.metric in {"dio", "dso", "monthly_revenue_3m_yoy"}:
                raise ValueError("周转/滚动指标由程序计算，不能由 Agent 写入")
        return self


class ObservationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["ai-compute-v1"] = "ai-compute-v1"
    observations: list[Observation] = Field(min_length=1, max_length=20000)
    notes: list[str] = Field(default_factory=list, max_length=100)


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["earnings", "outlook", "conditions"]
    headline: str = Field(min_length=5, max_length=120)
    text: str = Field(min_length=10, max_length=1500)


class PanelInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=5, max_length=120)
    evidence: str = Field(min_length=20, max_length=4000)
    watch: str = Field(min_length=10, max_length=1500)


class ReportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=5, max_length=160)
    url: str = Field(max_length=1800)

    @field_validator("url")
    @classmethod
    def source_http(cls, value):
        return Observation.source_http(value)


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["ai-compute-report-v2"] = "ai-compute-report-v2"
    data_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: date
    agent: str = Field(min_length=1, max_length=120)
    overall: str = Field(min_length=30, max_length=6000)
    summary: list[ReportSummary] = Field(min_length=3, max_length=3)
    panels: dict[str, PanelInterpretation]
    sources: list[ReportSource] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def complete(self):
        if {s.id for s in self.summary} != {"earnings", "outlook", "conditions"}:
            raise ValueError("经营判断必须包含盈利、预期、经营条件三项，不能重复")
        if set(self.panels) != {"earnings", "demand", "manufacturing", "rental", "power", "quality"}:
            raise ValueError("必须一次发布六板块解读")
        if self.as_of > today():
            raise ValueError("报告日期不能在未来")
        return self
