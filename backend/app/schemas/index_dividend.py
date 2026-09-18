"""红利低波(H30269)估值契约。

percentile 必须随窗口定义（window_start/window_end/n_obs）一同返回——
没有窗口定义的分位数是无效数据（规范 §3.3/§4.4）。三个指标的「成立以来」
实际起点不同（股息率 2006 / PE 2013-12 / PB 2016-09），由 n_obs 与
window_start 如实暴露，前端必须展示。
"""

from pydantic import BaseModel


class PercentileResult(BaseModel):
    percentile: float | None = None     # 样本不足 → None（不静默降级）
    window_start: str | None = None
    window_end: str | None = None
    n_obs: int = 0


class MetricSummary(BaseModel):
    value: float | None = None
    date: str | None = None
    # 当前值来源：official（中证官方锚）/ recon（TR/PR 重建）/ danjuan（蛋卷）/ derived
    value_source: str | None = None
    # higher_is_expensive（PE/PB：越高越贵）/ higher_is_cheap（股息率/利差）/ neutral
    direction: str
    presets: dict[str, PercentileResult] = {}
    custom: PercentileResult | None = None


class IndexDividendSummary(BaseModel):
    index_code: str
    index_name: str
    latest_date: str | None = None
    caliber: str | None = None          # 自动判定的官方口径（dp1/dp2）
    k_dy: float | None = None           # 股息率校准系数
    metrics: dict[str, MetricSummary]


class GateStatus(BaseModel):
    gate: str
    status: str                         # ok / warn / block
    detail: str
    checked_at: str | None = None


class SourceFreshness(BaseModel):
    table: str
    label: str
    latest_date: str | None = None
    rows: int = 0


class IndexDividendHealth(BaseModel):
    gates: list[GateStatus]
    sources: list[SourceFreshness]
