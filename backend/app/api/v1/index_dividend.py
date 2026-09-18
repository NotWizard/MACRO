"""红利低波(H30269)估值端点：序列切片 + 当前值与分位 + 质量门健康。

分位为查询时计算（analysis.index_dividend），支持预设窗口（成立以来/1/3/5/10年）
与任意自定义起止——数据量约 5k 行，逐次计算毫秒级，无需预计算落库。
"""

from datetime import date

import pandas as pd
from fastapi import APIRouter, Query

from analysis.index_dividend import METRIC_DIRECTION, preset_percentiles, percentile_in_window
from backend.app.core import db
from backend.app.core.serial import df_to_records
from backend.app.schemas.cycles import DerivedFrame
from backend.app.schemas.index_dividend import (
    GateStatus,
    IndexDividendHealth,
    IndexDividendSummary,
    MetricSummary,
    PercentileResult,
    SourceFreshness,
)

router = APIRouter(prefix="/index-dividend", tags=["index-dividend"])

_INDEX_CODE = "H30269"
_INDEX_NAME = "中证红利低波动指数"
# 暴露给前端的指标 → derived_index_daily 列
_METRIC_COLS = {"dy": "dy", "pe": "pe", "pb": "pb", "spread": "spread", "cgb": "cgb_10y"}

_SOURCE_LABELS = {
    "idx_price_daily": "中证行情（价格/全收益）",
    "idx_valuation_official": "中证官方估值锚",
    "idx_valuation_dj": "蛋卷 PB/PE",
    "bond_yield_daily": "中债 10Y（日频）",
}


def _load_series():
    """derived_index_daily → {metric: 以 date 为索引的 Series}（空表 → 空 dict）。"""
    df = db.load("derived_index_daily")
    if df.empty:
        return df, {}
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
    series = {}
    for metric, col in _METRIC_COLS.items():
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(s):
                series[metric] = s
    return df, series


@router.get("/series", response_model=DerivedFrame)
def index_dividend_series(
    start: date | None = Query(None),
    end: date | None = Query(None),
):
    """图表用日频序列（px/tr/pe/pb/dy/spread/cgb + 口径与校准系数）。"""
    df = db.load("derived_index_daily", start, end)
    return DerivedFrame(table="derived_index_daily",
                        columns=list(df.columns), records=df_to_records(df))


def _official_current() -> dict:
    """中证官方锚的最新一行（pe2/dp2）——当前水平的权威来源。缺表/空表返回 {}。"""
    try:
        off = db.load("idx_valuation_official")
    except Exception:
        return {}
    if off.empty:
        return {}
    off["date"] = pd.to_datetime(off["date"])
    row = off.sort_values("date").iloc[-1]
    return {"date": row["date"].strftime("%Y-%m-%d"),
            "pe2": row.get("pe2"), "dp2": row.get("dp2")}


@router.get("/summary", response_model=IndexDividendSummary)
def index_dividend_summary(
    pct_start: date | None = Query(None, description="自定义分位窗口起点"),
    pct_end: date | None = Query(None, description="自定义分位窗口终点"),
):
    """当前值 + 各指标预设窗口分位；给 pct_start/pct_end 时附自定义窗口分位。

    当前值优先取官方锚（权威水平；重建序列在除息密集期有 ±2-3% 单日噪音），
    分位在重建/校准历史窗口上计算（校准已均值对齐，跨源偏差 <1%）；
    官方锚缺失时回退重建序列末端值，value_source 如实标注。
    """
    df, series = _load_series()
    off = _official_current()

    # 当前值覆盖表：指标 → (官方值, 来源)。官方锚缺失/字段为 NaN 时回退序列末端。
    overrides: dict[str, tuple] = {}
    if off:
        if pd.notna(off.get("dp2")):
            overrides["dy"] = (float(off["dp2"]), off["date"], "official")
        if pd.notna(off.get("pe2")):
            overrides["pe"] = (float(off["pe2"]), off["date"], "official")
        if pd.notna(off.get("dp2")) and "cgb" in series and len(series["cgb"]):
            overrides["spread"] = (float(off["dp2"] - series["cgb"].iloc[-1]),
                                   off["date"], "official")

    metrics: dict[str, MetricSummary] = {}
    for metric, s in series.items():
        value, cur_date, src = float(s.iloc[-1]), s.index[-1].strftime("%Y-%m-%d"), "derived"
        if metric == "pb":
            src = "danjuan"
        if metric in ("dy", "pe", "spread") and src == "derived":
            src = "recon"
        if metric in overrides:
            value, cur_date, src = overrides[metric]
        presets = {label: PercentileResult(**r)
                   for label, r in preset_percentiles(s, value).items()}
        custom = None
        if pct_start is not None or pct_end is not None:
            custom = PercentileResult(**percentile_in_window(
                s, value, pct_start, pct_end))
        metrics[metric] = MetricSummary(
            value=round(value, 4), date=cur_date, value_source=src,
            direction=METRIC_DIRECTION.get(metric, "neutral"),
            presets=presets, custom=custom)

    latest = df.index.max().strftime("%Y-%m-%d") if not df.empty else None
    caliber = None
    k_dy = None
    if not df.empty:
        if "caliber" in df.columns and df["caliber"].notna().any():
            caliber = str(df["caliber"].dropna().iloc[-1])
        if "k_dy" in df.columns and df["k_dy"].notna().any():
            k_dy = float(df["k_dy"].dropna().iloc[-1])
    return IndexDividendSummary(index_code=_INDEX_CODE, index_name=_INDEX_NAME,
                                latest_date=latest, caliber=caliber, k_dy=k_dy,
                                metrics=metrics)


@router.get("/health", response_model=IndexDividendHealth)
def index_dividend_health():
    """质量门状态（最近一次采集写入）+ 各源表新鲜度。"""
    gates: list[GateStatus] = []
    try:
        g = db.load("idx_gate_status")
        if not g.empty:
            gates = [GateStatus(**r) for r in
                     g.fillna("").to_dict("records")]
    except Exception:
        pass
    sources: list[SourceFreshness] = []
    for table, label in _SOURCE_LABELS.items():
        try:
            df = db.load(table)
            latest = (pd.to_datetime(df["date"]).max().strftime("%Y-%m-%d")
                      if not df.empty and "date" in df.columns else None)
            sources.append(SourceFreshness(table=table, label=label,
                                           latest_date=latest, rows=len(df)))
        except Exception:
            sources.append(SourceFreshness(table=table, label=label))
    return IndexDividendHealth(gates=gates, sources=sources)
