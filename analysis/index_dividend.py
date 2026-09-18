# -*- coding: utf-8 -*-
"""红利低波(H30269)估值加工 —— TR/PR 股息率重建 + 口径判定 + 校准 + 利差 + 质量门。

依据《指数股息率与PE数据获取及加工规范 v1.0》（SCHD_analysis/，2026-09-18 实测），
配套取数手册 docs/data-supplement-runbook.md。

核心算法（TR/PR 法，精确还原非近似）：
    R(t)  = TR(t) / P(t)              全收益/价格比，只反映分红累积效应
    a(t)  = R(t)/R(t-1) - 1           非除息日=0，除息日 = d_i/P(t)
    d_i   = a(t) × P(t)               逐笔还原分红（指数点位计）
    D(t)  = Σ d_i（过去 252 个交易日）  TTM 分红
    DY(t) = D(t) / P(t) × 100         重建股息率

口径判定：官方同时发布两套股本口径（PE1/DP1 总股本、PE2/DP2 计算用股本），
重建序列与官方锚的 dp1/dp2 各算 ratio+corr 自动判定配对（实测复现 DP2）。
校准：k = mean(官方)/mean(重建)（重叠区均值匹配）——常数倍数不改变排序，
所以分位用未校准序列、展示水平用校准序列。

质量门（规范 §5，本文档实现 G1/G2/G5/G6/G7）：
    G1 重建 vs 官方锚：ratio∈[0.97,1.03] 且 corr>0.75，不过 → 阻断（不落库）
    G2 派息率 PE×DP 相对前 20 日均值跳变 >1pp → 告警（上游口径可能变更）
    G5 peg 字段 vs 官方 PE 重叠区比值 ∉[0.95,1.05] → 告警（推定口径漂移）
    G6 股息率绝对值 ∉[1,12]% → 阻断；G7 全收益<价格 → 阻断
    12 月年度调样窗口内 G1 天然偏弱 → 降级为告警（规范 §4.1.4 口径断点）

百分位：查询时计算（本模块 percentile_in_window，后端直接复用），
窗口定义随结果返回；样本不足返回 None，绝不静默降级（规范门 G11）。
"""

import numpy as np
import pandas as pd

# ── 常量 ─────────────────────────────────────────────────────────────────────
# TTM 分红窗口：252 个交易日。曾假设 365 自然日更贴官方「过去 12 个月」口径，
# 2026-09-18 实测否决：252TD 对官方 DP2 锚 ratio=1.008/corr=0.852（过 G1），
# 365D ratio=0.987/corr=0.649（不过 G1）。结论：官方口径实际跟踪 252 交易日窗。
TTM_WINDOW = 252        # 交易日数
A_FLOOR = 1e-4          # a(t) 去噪阈值：小于此视为浮点噪声（规范 §4.1.4 实测）
CALIBER_RATIO_TOL = 0.03     # G1: 重建/官方均值比值容差
CALIBER_CORR_MIN = 0.75      # G1: 日变动相关系数下限
PAYOUT_JUMP_PP = 1.0         # G2: 派息率跳变阈值（pp）
PEG_RATIO_TOL = 0.05         # G5: peg vs 官方 PE 比值容差
DY_RANGE = (1.0, 12.0)       # G6: 股息率绝对值合理区间

# 指标方向（前端渲染硬编码依据）：PE/PB 分位越高越贵；股息率/利差越高越便宜
METRIC_DIRECTION = {"pe": "higher_is_expensive", "pb": "higher_is_expensive",
                    "dy": "higher_is_cheap", "spread": "higher_is_cheap",
                    "cgb": "neutral"}

# 分位预设窗口（交易日数）；inception = 成立以来（扩张窗口）
PRESET_WINDOWS = {"inception": None, "1y": 252, "3y": 756, "5y": 1260, "10y": 2520}
# 自定义窗口最少样本数：低于此返回 None（G11：不许静默用不足的数据算分位）
MIN_OBS_FOR_PERCENTILE = 60


# ── TR/PR 股息率重建 ─────────────────────────────────────────────────────────
def reconstruct_dividend_yield(px: pd.Series, tr: pd.Series,
                               ttm_window: int = TTM_WINDOW) -> pd.DataFrame:
    """TR/PR 法精确重建股息率。入参为以日期为索引的收盘序列（ISO 字符串可解析）。"""
    d = pd.concat({"px": px, "tr": tr}, axis=1).dropna()
    d.index = pd.to_datetime(d.index)
    d = d.sort_index()
    if (d["tr"] < d["px"]).any():
        raise ValueError("门G7: 全收益指数必须 >= 价格指数")
    d["R"] = d["tr"] / d["px"]
    d["a"] = (d["R"] / d["R"].shift(1) - 1).clip(lower=0)   # 负值是浮点噪声
    d.loc[d["a"] < A_FLOOR, "a"] = 0.0
    d["d_i"] = d["a"] * d["px"]
    d["D"] = d["d_i"].rolling(ttm_window, min_periods=ttm_window).sum()
    d["dy_recon"] = d["D"] / d["px"] * 100
    return d.dropna(subset=["dy_recon"])


# ── 口径判定与校准 ───────────────────────────────────────────────────────────
def _overlap(dy_recon: pd.Series, official: pd.DataFrame, caliber: str) -> pd.DataFrame:
    j = official[[caliber]].join(dy_recon.rename("dy"), how="inner")
    j.index = pd.to_datetime(j.index)
    return j.dropna()


def calibrate(dy_recon: pd.Series, official: pd.DataFrame, caliber: str = "dp2"):
    """常数倍数校准（重叠区均值匹配）。返回 (校准序列, k, {ratio, corr, n})。"""
    j = _overlap(dy_recon, official, caliber)
    if len(j) < 10:
        raise ValueError(f"重叠区样本不足（{len(j)}），无法校准")
    k = j[caliber].mean() / j["dy"].mean()
    return dy_recon * k, k, {"ratio": j["dy"].mean() / j[caliber].mean(),
                             "corr": j["dy"].corr(j[caliber]), "n": len(j)}


def detect_caliber(dy_recon: pd.Series, official: pd.DataFrame):
    """自动判定重建序列配对 dp1 还是 dp2。返回 (caliber, verdicts)。"""
    verdicts = {}
    for cal in ("dp1", "dp2"):
        try:
            _, _, m = calibrate(dy_recon, official, cal)
            verdicts[cal] = m
        except ValueError:
            verdicts[cal] = {"ratio": np.nan, "corr": np.nan, "n": 0}
    def _score(c):
        v = verdicts[c]
        in_tol = abs(1 - v["ratio"]) <= CALIBER_RATIO_TOL
        return (in_tol, v["corr"] if v["corr"] == v["corr"] else -1)
    return max(("dp1", "dp2"), key=_score), verdicts


# ── 质量门 ───────────────────────────────────────────────────────────────────
def run_gates(dy: pd.Series, dy_recon: pd.Series, pe_raw: pd.Series,
              official: pd.DataFrame, caliber: str, verdict: dict) -> list[dict]:
    """返回门报告 [{gate, status, detail}]；status ∈ ok/warn/block。
    12 月年度调样窗口内 G1 失败降级为 warn（规范 §4.1.4 口径断点）。"""
    gates = []
    best = verdict[caliber]
    g1_ok = (abs(1 - best["ratio"]) <= CALIBER_RATIO_TOL
             and best["corr"] > CALIBER_CORR_MIN)
    g1_status = "ok" if g1_ok else "block"
    # 调样窗口（每年 12 月调样，影响其后数周的重叠区）：最新官方锚落在
    # 12 月或次年 1 月时 G1 降级为告警
    latest_off = pd.to_datetime(official.index).max()
    if not g1_ok and latest_off.month in (12, 1):
        g1_status = "warn"
    gates.append({"gate": "G1_recon_vs_official", "status": g1_status,
                  "detail": f"口径={caliber} ratio={best['ratio']:.4f} "
                            f"corr={best['corr']:.4f} n={best['n']}"})

    pe_col = f"pe{caliber[-1]}"
    if pe_col in official.columns and len(official) >= 5:
        payout = official[pe_col] * official[caliber]
        p_now, p_ref = payout.iloc[-1], payout.iloc[:-1].mean()
        jump = abs(p_now - p_ref)
        gates.append({"gate": "G2_payout_probe",
                      "status": "warn" if jump > PAYOUT_JUMP_PP else "ok",
                      "detail": f"payout={p_now:.2f} vs 20日均{p_ref:.2f} "
                                f"(跳变{jump:.2f}pp)"})

    j = official[[pe_col]].join(pe_raw.rename("peg"), how="inner").dropna() \
        if pe_col in official.columns else pd.DataFrame()
    if len(j) >= 10:
        ratio = j["peg"].mean() / j[pe_col].mean()
        gates.append({"gate": "G5_peg_caliber_drift",
                      "status": "warn" if abs(1 - ratio) > PEG_RATIO_TOL else "ok",
                      "detail": f"peg/{pe_col}={ratio:.4f} n={len(j)}"})

    dy_now = dy.iloc[-1]
    gates.append({"gate": "G6_dy_range",
                  "status": "ok" if DY_RANGE[0] <= dy_now <= DY_RANGE[1] else "block",
                  "detail": f"最新股息率={dy_now:.2f}% ∈ [{DY_RANGE[0]},{DY_RANGE[1]}]"})
    return gates


# ── 衍生表加工（02_compute_derived 调用入口）─────────────────────────────────
def compute_index_dividend(conn) -> "pd.DataFrame | None":
    """读 4 张原始表 → 重建/校准/利差/门 → 写 derived_index_daily + idx_gate_status。

    任一原始表缺失（如老库未采集过）→ 记日志返回 None，不影响其他衍生表。
    阻断门（G1非调样期/G6/G7）失败 → 抛异常，由 02 的调用方决定整轮语义。
    """
    import logging
    logger = logging.getLogger(__name__)

    def _load(table):
        try:
            df = pd.read_sql(f"SELECT * FROM {table}", conn)
            return df.drop_duplicates("date", keep="last") if not df.empty else df
        except Exception:
            return pd.DataFrame()

    price = _load("idx_price_daily")
    official = _load("idx_valuation_official")
    dj = _load("idx_valuation_dj")
    bond = _load("bond_yield_daily")
    if price.empty or official.empty:
        logger.warning("index_dividend: 原始表缺失（idx_price_daily/idx_valuation_official），跳过")
        return None

    price = price.set_index(pd.to_datetime(price["date"])).sort_index()
    official = official.set_index(pd.to_datetime(official["date"])).sort_index()

    # 重建 + 口径判定 + 校准
    dv = reconstruct_dividend_yield(price["px_close"], price["tr_close"])
    caliber, verdicts = detect_caliber(dv["dy_recon"], official)
    dy, k_dy, _ = calibrate(dv["dy_recon"], official, caliber)

    # PE：peg 长历史，按官方 PE 重叠区做同款常数校准（展示水平用；分位不受影响）
    pe_col = f"pe{caliber[-1]}"
    pe_raw = price["peg_pe"].dropna()
    pe = pe_raw.copy()
    j = official[[pe_col]].join(pe_raw.rename("peg"), how="inner").dropna()
    if len(j) >= 10:
        pe = pe_raw * (j[pe_col].mean() / j["peg"].mean())

    # 主表：以重建股息率的日期为骨架，对齐国债（ffill）与蛋卷 PB（ffill）
    df = pd.DataFrame({"dy_recon": dv["dy_recon"], "dy": dy})
    df["px_close"] = price["px_close"]
    df["tr_close"] = price["tr_close"]
    df["pe_raw"] = price["peg_pe"]
    df["pe"] = pe
    if not bond.empty:
        cgb = bond.set_index(pd.to_datetime(bond["date"]))["y_10y"].sort_index()
        df["cgb_10y"] = cgb.reindex(df.index).ffill()
    if not dj.empty:
        # 蛋卷 PB/PE 是周频采样：不 ffill——ffill 会把每个周观测膨胀成 ~5 个日度行，
        # 分位的 n_obs 虚增 5 倍（样本充足性检查 G11 会被骗过）。保留原始采样点，
        # 图表端 connectNulls 视觉续接，分位用 dropna 后的真实观测数。
        dj_idx = dj.set_index(pd.to_datetime(dj["date"])).sort_index()
        df["pb"] = dj_idx["pb"].reindex(df.index)
        df["pe_dj"] = dj_idx["pe_dj"].reindex(df.index)
    df["spread"] = df["dy"] - df["cgb_10y"]
    df["caliber"] = caliber
    df["k_dy"] = round(k_dy, 6)

    # 质量门 → idx_gate_status
    gates = run_gates(df["dy"], df["dy_recon"], pe_raw, official, caliber, verdicts)
    blocked = [g for g in gates if g["status"] == "block"]
    gate_df = pd.DataFrame(gates)
    gate_df["checked_at"] = pd.Timestamp.today().strftime("%Y-%m-%d")
    gate_df.to_sql("idx_gate_status", conn, if_exists="replace", index=False)
    if blocked:
        raise ValueError(f"阻断门未过: {[(g['gate'], g['detail']) for g in blocked]}")

    out = df.reset_index().rename(columns={"index": "date"})
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


# ── 查询时分位（后端复用）────────────────────────────────────────────────────
def percentile_in_window(series: pd.Series, current: float,
                         start=None, end=None,
                         min_obs: int = MIN_OBS_FOR_PERCENTILE):
    """current 在 [start, end] 窗口内的经验分位（%）。样本不足返回 None。

    窗口定义必须随结果一同返回（规范：没有窗口定义的分位数是无效数据）。
    返回 dict(percentile, window_start, window_end, n_obs) 或 None 值版本。
    """
    s = series.dropna()
    s.index = pd.to_datetime(s.index)
    if start is not None:
        s = s[s.index >= pd.Timestamp(start)]
    if end is not None:
        s = s[s.index <= pd.Timestamp(end)]
    meta = {"window_start": s.index.min().strftime("%Y-%m-%d") if len(s) else None,
            "window_end": s.index.max().strftime("%Y-%m-%d") if len(s) else None,
            "n_obs": int(len(s))}
    if len(s) < min_obs or pd.isna(current):
        return {"percentile": None, **meta}
    return {"percentile": float((s <= current).mean() * 100), **meta}


def preset_percentiles(series: pd.Series, current: float) -> dict:
    """全预设窗口的分位（inception/1y/3y/5y/10y），各自带窗口定义。

    G11 充足性按**时间跨度覆盖率**判定而非行数：窗口成员仍按交易日数取
    （规范 §4.4：iloc[-N:]），但序列可能比窗口短（周频 PB 全量仅 512 行 < 2520）
    ——此时若实际覆盖跨度 ≥ 窗口跨度的 90%（PB：2016-09 起 ≈ 10 年窗口的 99.9%）
    则为有效分位；只有 3 年数据却标 10 年窗口才是 G11 要拦截的静默降级。
    """
    s = series.dropna()
    s.index = pd.to_datetime(s.index)
    out = {}
    for label, days in PRESET_WINDOWS.items():
        w = s if days is None else s.iloc[-days:]
        meta = {"window_start": w.index.min().strftime("%Y-%m-%d") if len(w) else None,
                "window_end": w.index.max().strftime("%Y-%m-%d") if len(w) else None,
                "n_obs": int(len(w))}
        sufficient = len(w) >= MIN_OBS_FOR_PERCENTILE
        if sufficient and days is not None:
            span = pd.Timedelta(days=days * 365.25 / 252)   # 交易日数 → 自然日跨度
            covered = w.index.max() - w.index.min()
            sufficient = covered >= span * 0.90
        if not sufficient or pd.isna(current):
            out[label] = {"percentile": None, **meta}
        else:
            out[label] = {"percentile": float((w <= current).mean() * 100), **meta}
    return out
