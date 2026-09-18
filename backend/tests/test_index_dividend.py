"""红利低波(H30269)估值端点与 TR/PR 重建算法测试。

两类测试：
1. 算法纯函数（合成数据，CI 可跑）：TR/PR 重建的精确性、口径判定、校准的
   排序不变性、分位窗口语义（G11 不足返回 None）、反模式1伪恒等式反证。
2. API 形状（依赖真实/fixture DB；无 derived_index_daily 表时端点须优雅
   降级为 200 空载荷而非 500——与 db.load 缺表语义一致）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from analysis import index_dividend as aid  # noqa: E402
from backend.app.main import app  # noqa: E402

client = TestClient(app)


# ── 合成数据工厂 ─────────────────────────────────────────────────────────────
def _synthetic_px_tr(days=800, n_divs_per_year=12, dy_level=0.05, seed=7):
    """合成价格/全收益序列：价格随机游走，全收益 = 价格 + 定期分红再投入。

    每年 n_divs_per_year 次除息（密散如 50 只成分股的合效应，TTM 窗口内始终
    有 ~12 笔，避免稀疏除息导致 TTM 在 1 笔/2 笔间振荡），每次分红 = 当时点位
    × dy_level/n_divs_per_year。分红从价格指数中自然扣除（价格除数不调整），
    全收益把分红加回——与中证口径同构。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rets = rng.normal(0.0003, 0.01, days)
    px = np.empty(days)
    px[0] = 1000.0
    # 均匀撒除息日
    div_idx = set(np.linspace(60, days - 1, int(days / 240 * n_divs_per_year),
                              dtype=int).tolist())
    div_cash = {}
    for i in range(1, days):
        px[i] = px[i - 1] * (1 + rets[i])
        if i in div_idx:
            d = px[i] * dy_level / n_divs_per_year
            px[i] -= d                       # 除息：价格指数自然回落
            div_cash[dates[i]] = d
    tr = px.copy()
    tr_factor = np.ones(days)
    for i in range(1, days):
        if dates[i] in div_cash:
            tr_factor[i] = tr_factor[i - 1] * (1 + div_cash[dates[i]] / px[i])
        else:
            tr_factor[i] = tr_factor[i - 1]
    tr = px * tr_factor / tr_factor[0] * (1000.0 / px[0])
    px_s = pd.Series(px, index=dates)
    tr_s = pd.Series(px * (tr_factor / tr_factor[0]), index=dates)
    return px_s, tr_s


# ── 1. 算法纯函数 ────────────────────────────────────────────────────────────
def test_reconstruct_recovers_synthetic_dividend_yield():
    """TR/PR 重建应近似还原合成序列的真实股息率（±20% 内，含价格漂移）。"""
    px, tr = _synthetic_px_tr(dy_level=0.05)
    dv = aid.reconstruct_dividend_yield(px, tr)
    assert len(dv) > 200
    # 稳态段（排除窗口建立初期）的重建均值应接近 5%
    tail = dv["dy_recon"].iloc[-100:]
    assert 4.5 < tail.mean() < 5.5


def test_reconstruct_g7_violation_raises():
    """全收益 < 价格指数 → 门 G7 阻断（序列取错是最危险的数据事故）。"""
    dates = pd.bdate_range("2024-01-01", periods=30)
    px = pd.Series(np.linspace(1000, 1010, 30), index=dates)
    tr = px * 0.99                      # 全收益低于价格 = 数据取错
    with pytest.raises(ValueError, match="G7"):
        aid.reconstruct_dividend_yield(px, tr)


def test_detect_caliber_prefers_matching_series():
    """口径判定：与官方 dp2 一致的重建序列应判定为 dp2。"""
    px, tr = _synthetic_px_tr()
    dv = aid.reconstruct_dividend_yield(px, tr)
    off = dv["dy_recon"].iloc[-20:].rename("dp2").to_frame()
    off["dp1"] = off["dp2"] * 0.88      # dp1 口径系统性低 12%（实测 DP1/DP2 差 13%）
    caliber, verdicts = aid.detect_caliber(dv["dy_recon"], off)
    assert caliber == "dp2"
    assert abs(verdicts["dp2"]["ratio"] - 1) < 0.01


def test_calibration_is_rank_invariant():
    """常数校准不改变排序 → 分位不变（规范 §4.1.3 的关键性质）。"""
    s = pd.Series(np.random.default_rng(1).normal(5, 1, 500),
                  index=pd.bdate_range("2023-01-01", periods=500))
    cur = s.iloc[-1]
    p1 = aid.preset_percentiles(s, cur)["1y"]["percentile"]
    p2 = aid.preset_percentiles(s * 0.962, cur * 0.962)["1y"]["percentile"]
    assert p1 == pytest.approx(p2, abs=1e-9)


def test_percentile_insufficient_window_returns_none():
    """门 G11：样本不足必须返回 None，不许静默降级算出假分位。"""
    s = pd.Series(np.arange(100.0), index=pd.bdate_range("2024-01-01", periods=100))
    # 10y 窗口需覆盖 ≥90% 的 10 年跨度：100 个交易日 ≈ 140 自然日 → None
    assert aid.preset_percentiles(s, 99.0)["10y"]["percentile"] is None
    # 但 inception 窗口（扩张窗）可用
    assert aid.preset_percentiles(s, 99.0)["inception"]["percentile"] == pytest.approx(100.0)


def test_weekly_series_10y_window_allowed_when_span_covered():
    """周频 PB 的 10 年窗口：512 行 < 2520 行但跨度覆盖 99% → 有效（G11 按跨度判定）。"""
    s = pd.Series(np.random.default_rng(3).normal(1.0, 0.15, 512),
                  index=pd.date_range("2016-09-19", periods=512, freq="7D"))
    r = aid.preset_percentiles(s, float(s.iloc[-1]))["10y"]
    assert r["percentile"] is not None and r["n_obs"] == 512


def test_percentile_window_meta_always_present():
    """没有窗口定义的分位数是无效数据：window_start/end/n_obs 必须随结果返回。"""
    s = pd.Series(np.arange(600.0), index=pd.bdate_range("2022-01-01", periods=600))
    r = aid.preset_percentiles(s, 599.0)["1y"]
    assert r["n_obs"] >= 240 and r["window_start"] and r["window_end"]


def test_antipattern_payout_over_pe_is_pseudo_identity():
    """反模式 1 反证：DY=k/PE 时 股息率分位 ≡ 100−PE分位（假信号复读机）。"""
    pe = pd.Series(np.random.default_rng(2).lognormal(2, 0.2, 800),
                   index=pd.bdate_range("2020-01-01", periods=800))
    fake_dy = 0.38 / pe * 100
    p_dy = aid.preset_percentiles(fake_dy, fake_dy.iloc[-1])["3y"]["percentile"]
    p_pe = aid.preset_percentiles(pe, pe.iloc[-1])["3y"]["percentile"]
    assert p_dy == pytest.approx(100 - p_pe, abs=0.5)


# ── 2. API 形状（缺表时优雅降级）──────────────────────────────────────────────
def test_series_endpoint_shape():
    r = client.get("/api/v1/index-dividend/series")
    assert r.status_code == 200
    body = r.json()
    assert body["table"] == "derived_index_daily"
    assert isinstance(body["records"], list)


def test_summary_endpoint_shape():
    r = client.get("/api/v1/index-dividend/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["index_code"] == "H30269"
    assert "metrics" in body
    for m in body["metrics"].values():
        assert m["direction"] in ("higher_is_expensive", "higher_is_cheap", "neutral")
        assert set(m["presets"].keys()) == {"inception", "1y", "3y", "5y", "10y"}


def test_summary_custom_window_params():
    r = client.get("/api/v1/index-dividend/summary?pct_start=2020-01-01&pct_end=2024-12-31")
    assert r.status_code == 200
    # 非法日期在边界被 422 拦截（与 test_date_params 同一契约）
    assert client.get("/api/v1/index-dividend/summary?pct_start=bad").status_code == 422


def test_health_endpoint_shape():
    r = client.get("/api/v1/index-dividend/health")
    assert r.status_code == 200
    body = r.json()
    assert "gates" in body and "sources" in body
    for s in body["sources"]:
        assert {"table", "label", "latest_date", "rows"} <= set(s.keys())
