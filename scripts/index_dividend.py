# -*- coding: utf-8 -*-
"""红利低波(H30269)估值数据采集 —— 中证官方 + 蛋卷辅助 + 中债日频。

数据源（口径与实测陷阱见 docs/data-sources-guide.md）：

1. 中证 `index-perf`：价格指数 H30269 / 全收益 H20269 日频行情。
   - `peg` 字段实为 PE（官方无文档，推定口径，由 analysis 层 G5 门持续监控）；
   - 两指数均有 2006 年起的基日回填历史（2026-09-18 实测）；
   - 日期参数必须 YYYYMMDD；单次请求跨度约 1 年 → 按年分页；
   - WAF：必带 Referer，请求间隔 ≥1.2s，连续 ~40 次封禁 10 分钟以上 →
     bootstrap 用磁盘缓存（data/cache/csindex/）断点续抓，历史年不变永久缓存。
2. 中证 `{code}indicator.xls`：官方 PE1/PE2/DP1/DP2 真值锚，仅滚动 20 个
   交易日 → 每日抓取并 upsert 攒历史。文件是 OLE2 老式 Excel，必须 read_excel。
3. 蛋卷 `index_eva`：PB 唯一可自建的免费历史源（pb_history 周频 2016-09 起，
   detail 当日值）。口径未文档化，标注推定；ts 为北京时间零点毫秒戳。
4. 中债 historyQuery：10Y 国债日频（复用 fetch_bond_yield 同款端点，保留日频
   粒度；较 akshare bond_zh_us_rate 更稳，后者实测限流抛 JSONDecodeError）。

抓取模式：库内无表 → bootstrap 按年全量；有表 → 增量补最近窗口后按 key 合并。
任一源失败抛异常 → run_fetcher 记失败、闸门保留旧表，绝不让差数据覆盖好好数据。
"""

import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

# ── 常量 ─────────────────────────────────────────────────────────────────────
PRICE_CODE = "H30269"          # 中证红利低波动（价格指数）
TR_CODE = "H20269"             # 红利低波全收益（人工确认过的映射，见规范 §2.1.4）
DJ_CODE = "CSIH30269"          # 蛋卷代码 = CSI + 指数代码
HISTORY_START_YEAR = 2006      # 指数基日 2005-12-30，行情自 2006 起

CSI = "https://www.csindex.com.cn/csindex-home"
CSI_OSS = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads"
           "/file/autofile/indicator")
DJ = "https://danjuanfunds.com/djapi/index_eva"
CB_URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "csindex"

# WAF 纪律（规范 §3.2/反模式6：连续 ~40 次请求封禁 10 分钟以上，实测验证）：
# 间隔 2s + 每 10 次请求喘息 15s，把 bootstrap ~42 次请求的持续速率稀释到阈值以下；
# 403 退避 30/60/120/240s（封禁以分钟计，短退避无意义）；历史年磁盘缓存永久有效，
# 当年缓存当日有效——被封后下一轮从断点续抓，已抓年份零重试成本。
REQ_SLEEP_S = 2.0
BREATHE_EVERY = 10
BREATHE_S = 15.0
MAX_RETRY = 4

_req_counter = 0


def log(msg):
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── 中证 index-perf ──────────────────────────────────────────────────────────
def _csi_headers(code):
    return {"User-Agent": UA,
            "Referer": f"https://www.csindex.com.cn/zh-CN/indices/index-detail/{code}"}


def _csi_get(url, params, code):
    """带 WAF 退避与喘息的 GET。返回解析后的 JSON。"""
    global _req_counter
    last = ""
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(url, params=params, headers=_csi_headers(code), timeout=60)
            if r.status_code == 403:
                last = "403 WAF"
                log(f"  ⚠️ csindex 403（WAF），退避 {30 * (attempt + 1)}s 后重试 "
                    f"({attempt + 1}/{MAX_RETRY})")
                time.sleep(30 * (attempt + 1))          # 30s/60s/90s/120s
                continue
            r.raise_for_status()
            js = r.json()
            if js.get("code") != "200":
                raise RuntimeError(f"csindex error: {js.get('msg')}")
            _req_counter += 1
            if _req_counter % BREATHE_EVERY == 0:
                time.sleep(BREATHE_S)
            else:
                time.sleep(REQ_SLEEP_S)
            return js
        except (requests.RequestException, ValueError, RuntimeError) as e:
            last = repr(e)[:120]
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"csindex 请求失败（{MAX_RETRY} 次）: {url} {params} — {last}")


def _fetch_perf_year(code, year):
    """单年日频行情。历史年（<今年）磁盘缓存永久有效（指数历史不回改）；
    当年缓存当日有效（同日 bootstrap 断点续抓零成本，跨日自动重抓）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.today()
    cache = CACHE_DIR / f"{code}_{year}.json"
    if year >= today.year:
        cache = CACHE_DIR / f"{code}_{year}_{today.strftime('%Y%m%d')}.json"
    if cache.exists():
        js = json.loads(cache.read_text(encoding="utf-8"))
    else:
        js = _csi_get(f"{CSI}/perf/index-perf",
                      {"indexCode": code, "startDate": f"{year}0101",
                       "endDate": f"{year}1231"}, code)
        cache.write_text(json.dumps(js), encoding="utf-8")
    df = pd.DataFrame(js.get("data") or [])
    if df.empty:
        return pd.DataFrame(columns=["date", "close", "peg"])
    out = df[["tradeDate", "close", "peg"]].copy() if "peg" in df.columns \
        else df[["tradeDate", "close"]].assign(peg=None)
    out["date"] = pd.to_datetime(out["tradeDate"], format="%Y%m%d", errors="coerce")
    out = out.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["peg"] = pd.to_numeric(out["peg"], errors="coerce")
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out[["date", "close", "peg"]]


def fetch_price_series(code, prev_max_date=None):
    """H30269/H20269 日频收盘 + peg。prev_max_date 非空 → 增量（自该日前 40 天起，
    跨年按年拆分）；否则 2006 年起全量 bootstrap。"""
    this_year = pd.Timestamp.today().year
    if prev_max_date:
        start = (pd.Timestamp(prev_max_date) - pd.Timedelta(days=40))
        years = list(range(start.year, this_year + 1))
    else:
        years = list(range(HISTORY_START_YEAR, this_year + 1))
    frames = []
    for y in years:
        df = _fetch_perf_year(code, y)
        if not df.empty:
            frames.append(df)
    if not frames:
        raise RuntimeError(f"{code}: index-perf 无数据")
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def _merge_daily(prev: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """按 date 合并：新值优先，新行为 NaN 的单元格回退旧值（combine_first）。

    不能用 drop_duplicates(keep='last')：若某次新抓的行某列恰为 NaN（如全收益
    晚一天更新），整行替换会把旧表里的好值抹成 NaN。combine_first 只在新值
    缺失时才沿用旧值，且日期取并集。
    """
    if prev.empty:
        return new.sort_values("date").reset_index(drop=True)
    merged = (new.set_index("date")
              .combine_first(prev.set_index("date"))
              .reset_index())
    return merged.sort_values("date").reset_index(drop=True)


def fetch_price_daily(prev: pd.DataFrame) -> pd.DataFrame:
    """价格指数 + 全收益合并为 idx_price_daily(date, px_close, tr_close, peg_pe)。"""
    prev_max = prev["date"].max() if not prev.empty else None
    px = fetch_price_series(PRICE_CODE, prev_max)
    tr = fetch_price_series(TR_CODE, prev_max)
    new = px.merge(tr[["date", "close"]], on="date", how="outer",
                   suffixes=("", "_tr"))
    new = new.rename(columns={"close": "px_close", "close_tr": "tr_close",
                              "peg": "peg_pe"})
    return _merge_daily(prev, new[["date", "px_close", "tr_close", "peg_pe"]])


# ── 中证 indicator.xls（官方估值真值锚，20 日滚动 → upsert 攒历史）────────────
def fetch_official_valuation(prev: pd.DataFrame) -> pd.DataFrame:
    r = requests.get(f"{CSI_OSS}/{PRICE_CODE}indicator.xls",
                     headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content))            # OLE2，不能 read_csv
    df = df.rename(columns={
        "日期Date": "date",
        "市盈率1（总股本）P/E1": "pe1", "市盈率2（计算用股本）P/E2": "pe2",
        "股息率1（总股本）D/P1": "dp1", "股息率2（计算用股本）D/P2": "dp2",
    })
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    new = df[["date", "pe1", "pe2", "dp1", "dp2"]].dropna(subset=["date"])
    for c in ("pe1", "pe2", "dp1", "dp2"):
        new[c] = pd.to_numeric(new[c], errors="coerce")
    new["date"] = new["date"].dt.strftime("%Y-%m-%d")
    return _merge_daily(prev, new)
def _dj_date(ts_ms):
    """蛋卷毫秒戳 = 北京时间零点 → ISO 日期。"""
    return (pd.to_datetime(int(ts_ms), unit="ms", utc=True)
            .tz_convert("Asia/Shanghai").strftime("%Y-%m-%d"))


def _dj_get(path):
    r = requests.get(f"{DJ}{path}", headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_dj_valuation(prev: pd.DataFrame) -> pd.DataFrame:
    """idx_valuation_dj(date, pb, pe_dj)：pb_history/pe_history(day=all，周频)
    全量拉取 + detail 当日点 upsert。"""
    rows = {}
    for hist, col in (("pb_history", "pb"), ("pe_history", "pe_dj")):
        js = _dj_get(f"/{hist}/{DJ_CODE}?day=all")
        key = f"index_eva_{hist.split('_')[0]}_growths"
        for it in js.get("data", {}).get(key, []):
            v = it.get(hist.split("_")[0])
            d = _dj_date(it["ts"])
            if v is not None:
                rows.setdefault(d, {})[col] = float(v)
        time.sleep(REQ_SLEEP_S)
    detail = _dj_get(f"/detail/{DJ_CODE}").get("data") or {}
    if detail.get("ts"):
        d = _dj_date(detail["ts"])
        if detail.get("pb") is not None:
            rows.setdefault(d, {})["pb"] = float(detail["pb"])
        if detail.get("pe") is not None:
            rows.setdefault(d, {})["pe_dj"] = float(detail["pe"])
    new = pd.DataFrame([{"date": d, **v} for d, v in rows.items()])
    if new.empty:
        raise RuntimeError("蛋卷 index_eva 无数据")
    for c in ("pb", "pe_dj"):
        if c not in new.columns:
            new[c] = None
    return _merge_daily(prev, new[["date", "pb", "pe_dj"]])


# ── 中债 10Y 国债日频（复用 fetch_bond_yield 端点，保留日频）──────────────────
def fetch_cgb_daily(prev: pd.DataFrame, to_num, pick_curve_table) -> pd.DataFrame:
    """bond_yield_daily(date, y_10y)。逐年 1 请求；增量重抓去年+今年吸收修订。"""
    from io import StringIO

    this_year = pd.Timestamp.today().year
    if not prev.empty:
        start_year = max(2006, int(pd.to_datetime(prev["date"]).dt.year.max()) - 1)
    else:
        start_year = 2006

    frames = []
    for year in range(start_year, this_year + 1):
        try:
            r = requests.get(CB_URL, params={
                "startDate": f"{year}-01-01", "endDate": f"{year}-12-31",
                "gjqx": "0", "qxId": "ycqx", "locale": "cn_ZH",
            }, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            dfs = pd.read_html(StringIO(r.text.replace("&nbsp", "")), header=0)
            gz = pick_curve_table(dfs)
            gz = gz[gz["曲线名称"] == "中债国债收益率曲线"][["日期", "10年"]].copy()
            gz["y_10y"] = to_num(gz["10年"])
            gz["date"] = pd.to_datetime(gz["日期"]).dt.strftime("%Y-%m-%d")
            frames.append(gz[["date", "y_10y"]].dropna(subset=["y_10y"]))
        except Exception as e:
            log(f"  ⚠️ 国债日频 {year} 年采集失败: {type(e).__name__}: {e}")
        time.sleep(REQ_SLEEP_S)
    if not frames:
        if prev.empty:
            raise RuntimeError("中债日频无数据且无旧表")
        return prev                                  # 整轮失败 → 沿用旧表
    new = pd.concat(frames, ignore_index=True)
    if not prev.empty:
        cutoff = new["date"].min()
        new = pd.concat([prev[prev["date"] < cutoff], new], ignore_index=True)
    return (new.drop_duplicates("date", keep="last")
            .sort_values("date").reset_index(drop=True))
