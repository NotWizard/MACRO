"""AI commentary service — structured generation over a per-section snapshot.

Architecture (M4b generation layer; config via core/ai_config + keychain):
    build_section_snapshot() → per-section scalars + data_as_of + missing
    DEFAULT_TEMPLATES / get_templates() / template_hash() → prompts + version hash
    generate()        → structured compact call (validate → retry → fallback)
                        + optional long report call → persist 7–8 rows at one ts

Triggers:
    - lifespan startup: fire-and-forget generate if DB empty
    - refresh success:  mark_stale + async regenerate (refresh-as-rerun)
    - config change:    ai_config._save hook → mark_stale only (no auto-regen)
    - manual POST:      sync generate (caller awaits)

Merged onto the post-1.2.0 baseline: connections come from the one factory in
``core/db.py`` (WAL + busy_timeout, A-M1), and busy-ness is derived from
``_gen_lock.locked()`` — a separate threading.Event flag could be left set
forever when two generations raced (F10), pinning the UI on an endless poll.
While busy, get_current returns the PREVIOUS batch with ``regenerating: True``
instead of blanking the card.
"""

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime
from functools import lru_cache

from analysis.cycle_credit import classify_credit
from analysis.cycle_debt import classify_debt
from analysis.cycle_inventory import classify_inventory
from analysis.cycle_merrill import classify_merrill
from analysis.real_estate import analyze_real_estate
from analysis.signals import compute_signals
from backend.app.core import ai_client, ai_config
from backend.app.core.db import DB_PATH, _db_version, _load_full, connect

COMMENTARY_TABLE = "commentary"
REPORT_SECTION = "report"
REPORT_TITLE = "# 中国宏观分析报告"
REPORT_MIN_CHARS = 1200
REPORT_MAX_CHARS = 3200
REPORT_PART_TIMEOUT_S = 420.0
REPORT_SECTION_MAX_CHARS = 185
REPORT_PARTS = (
    ("diagnosis", ("核心结论", "数据与置信度", "增长与需求", "价格与利润传导")),
    ("balance_sheet", ("信用、货币与政策", "债务与居民资产负债表", "房地产", "财政与外需")),
    ("outlook", ("结构与长期约束", "矛盾与交叉验证", "情景与触发条件", "观察清单")),
)
REPORT_HEADINGS = tuple(heading for _, headings in REPORT_PARTS for heading in headings)

_log = logging.getLogger(__name__)

SECTIONS = ("merrill", "credit", "inventory", "debt", "real_estate", "fiscal_external")

# Lock so concurrent generate() calls (startup + manual + refresh) never race.
# Busy-ness is DERIVED from the lock (`_gen_lock.locked()`) — a separate flag
# (the old threading.Event `_busy`) could be left set forever when thread A's
# `finally: clear()` raced thread B's `set()` after a failed acquire (F10).
_gen_lock = threading.Lock()
# Set True after _ensure_table first succeeds; get_current skips the per-poll
# CREATE TABLE IF NOT EXISTS + commit (table provably exists for process lifetime).
_table_ready = False


def _ts() -> str:
    # 毫秒精度：ts 兼作批次键，秒级在同秒两次 persist 会静默并批
    return datetime.now().isoformat(timespec="milliseconds")


def _connect() -> sqlite3.Connection:
    """Macro-DB connection from the ONE factory in ``core/db.py`` (A-M1):
    WAL + busy_timeout（裸 sqlite3.connect 遇到并发写会立刻 database is locked）。
    ``DB_PATH`` 在调用时读取，测试 monkeypatch 依旧生效。"""
    return connect(DB_PATH, row_factory=sqlite3.Row)


# ── Migration: new provenance columns (additive, idempotent) ────────────────
# ts doubles as generated_at and batch key; composite_score stays NULL for new
# rows (kept for legacy rows, SQLite cannot drop columns cheaply).
_NEW_COLS = {
    "section":       "TEXT DEFAULT 'overall'",
    "endpoint":      "TEXT",
    "template_hash": "TEXT",
    "profile":       "TEXT",
}


def _ensure_table(conn: sqlite3.Connection) -> None:
    global _table_ready
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {COMMENTARY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            data_as_of TEXT NOT NULL,
            composite_score INTEGER,
            phase_snapshot TEXT NOT NULL,
            text TEXT NOT NULL,
            model TEXT,
            stale INTEGER DEFAULT 0,
            section TEXT DEFAULT 'overall',
            endpoint TEXT,
            template_hash TEXT,
            profile TEXT
        )"""
    )
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({COMMENTARY_TABLE})")}
    for col, ddl in _NEW_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE {COMMENTARY_TABLE} ADD COLUMN {col} {ddl}")
    conn.commit()
    _table_ready = True


# ── snapshot v2: per-section scalars only, missing explicit ──────────────────

def _r(x):
    """float → round(2); None/NaN → None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, 2)


def _latest_observation(table: str, period_key: str, anchor: str, *fields: str) -> dict:
    frame = _load_full(table).reindex(columns=["date", anchor, *fields])
    data = frame.dropna(subset=["date", anchor])
    if data.empty:
        return {}
    row = data.iloc[-1]
    return {period_key: str(row["date"])[:7], **{k: _r(row[k]) for k in (anchor, *fields)}}


def _sec_merrill() -> dict:
    df = classify_merrill(str(DB_PATH))   # one visible observation per year
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    prices = _latest_observation("derived_monthly", "price_mom_month", "cpi_mom", "ppi_mom", "cpi_yoy", "ppi_yoy")
    prices["cpi_month_yoy"] = prices.pop("cpi_yoy", None)
    return {"phase": str(last["phase"]),
            "clock_period": str(last["date"])[:7],
            "clock_prev_period": None if prev is None else str(prev["date"])[:7],
            "gdp_yoy": _r(last["gdp_yoy"]),
            "gdp_trend": _r(last["gdp_trend"]),
            "gdp_yoy_prev": None if prev is None else _r(prev["gdp_yoy"]),
            "cpi_yoy": _r(last["cpi_yoy"]),
            "cpi_yoy_prev": None if prev is None else _r(prev["cpi_yoy"]),
            **_latest_observation("domestic_demand", "demand_month", "retail_yoy", "fai_yoy"),
            **_latest_observation("domestic_demand", "employment_month", "unemployment_rate", "unemployment_31_city"),
            **prices}


def _sec_credit() -> dict:
    df = classify_credit(str(DB_PATH))
    last = df.iloc[-1]
    loan = _latest_observation("derived_monthly", "loan_month", "new_rmb_loan", "loan_yoy", "loan_yoy_change")
    if loan and loan["loan_yoy_change"] is not None:
        loan["loan_yoy_base"] = _r(loan["new_rmb_loan"] - loan["loan_yoy_change"])
    return {"phase": str(last["phase"]),
            "credit_month": str(last["date"])[:7],
            "m2_yoy": _r(last["m2_yoy"]),
            "m2_yoy_delta": _r(df["m2_yoy"].diff(1).iloc[-1]),
            "credit_impulse": _r(last["credit_impulse"]),
            "impulse_smooth": _r(last["impulse_smooth"]),
            "impulse_smooth_delta": _r(df["impulse_smooth"].diff(1).iloc[-1]),
            **loan,
            **_latest_observation("derived_monthly", "social_finance_month", "sf_stock_yoy")}


def _sec_inventory() -> dict:
    last = classify_inventory(str(DB_PATH)).iloc[-1]
    return {"phase": str(last["phase"]),
            "inventory_month": str(last["date"])[:7],
            "pmi_official": _r(last["pmi_official"]),
            "pmi_used": _r(last["pmi_used"]), "pmi_stale_months": _r(last["pmi_stale_months"]),
            "ip_yoy": _r(last["ip_yoy"]), "ip_trend": _r(last["ip_trend"]),
            **_latest_observation("pmi", "ratingdog_month", "pmi_caixin", "pmi_caixin_svc"),
            **_latest_observation("pmi_detail", "orders_month", "new_orders", "production",
                                  "raw_material_inventory", "finished_goods_inventory")}


def _sec_debt() -> dict:
    df = classify_debt(str(DB_PATH))
    last = df.iloc[-1]
    return {"phase": str(last["overall_phase"]),
            "debt_month": str(last["date"])[:7],
            "household": _r(last["household"]),
            "non_fin_corp": _r(last["non_fin_corp"]),
            "gov_total": _r(last["gov_total"]),
            "net_change_4q": _r(last["net_change"]),
            # classify_debt 不返回 4 季差分列 → 对返回 df 现算，analysis/ 零 diff
            "household_change_4q": _r(df["household"].diff(4).iloc[-1]),
            "non_fin_corp_change_4q": _r(df["non_fin_corp"].diff(4).iloc[-1]),
            "gov_change_4q": _r(df["gov_total"].diff(4).iloc[-1])}


def _sec_real_estate() -> dict:
    a = analyze_real_estate(str(DB_PATH))["assessment"]
    values = {k: _r(a[k]) for k in ("composite_score", "leverage_space_score",
                                   "price_momentum_score", "rate_env_score",
                                   "household_leverage", "leverage_space_pp",
                                   "price_mom_12m", "lpr_5y", "rate_deviation_bp")}
    for measure in ("leverage_space", "price_momentum", "rate_env"):
        if not a[f"{measure}_available"]:
            values[f"{measure}_score"] = None
    return {**values, **{k: a[k] for k in ("as_of_leverage", "as_of_price", "as_of_lpr")},
            **_latest_observation("real_estate_activity", "property_month", "sales_area_yoy", "investment_yoy")}


def _sec_fiscal_external() -> dict:
    ism = _latest_observation("external_demand", "ism_month", "us_ism_pmi")
    return {**_latest_observation("fiscal", "fiscal_revenue_month", "revenue_cum_yoy"),
            **_latest_observation("fiscal", "fiscal_expenditure_month", "expenditure_cum_yoy"),
            **_latest_observation("external_demand", "trade_month", "exports_yoy", "imports_yoy"),
            "ism_month": ism.get("ism_month"), "ism": ism.get("us_ism_pmi")}


_BUILDERS = {"merrill": _sec_merrill, "credit": _sec_credit,
             "inventory": _sec_inventory, "debt": _sec_debt,
             "real_estate": _sec_real_estate, "fiscal_external": _sec_fiscal_external}

_FIELDS = {
    "merrill": ("phase", "clock_period", "clock_prev_period", "gdp_yoy", "gdp_trend", "gdp_yoy_prev", "cpi_yoy", "cpi_yoy_prev",
                "demand_month", "retail_yoy", "fai_yoy", "employment_month", "unemployment_rate", "unemployment_31_city",
                "price_mom_month", "cpi_month_yoy", "ppi_yoy", "cpi_mom", "ppi_mom"),
    "credit": ("phase", "credit_month", "m2_yoy", "m2_yoy_delta", "credit_impulse", "impulse_smooth", "impulse_smooth_delta",
               "loan_month", "new_rmb_loan", "loan_yoy", "loan_yoy_base", "loan_yoy_change", "social_finance_month", "sf_stock_yoy"),
    "inventory": ("phase", "inventory_month", "pmi_official", "pmi_used", "pmi_stale_months", "ip_yoy", "ip_trend", "orders_month",
                  "new_orders", "production", "raw_material_inventory", "finished_goods_inventory",
                  "ratingdog_month", "pmi_caixin", "pmi_caixin_svc"),
    "debt": ("phase", "debt_month", "household", "non_fin_corp", "gov_total", "net_change_4q",
             "household_change_4q", "non_fin_corp_change_4q", "gov_change_4q"),
    "real_estate": ("composite_score", "leverage_space_score", "price_momentum_score",
                    "rate_env_score", "household_leverage", "leverage_space_pp",
                    "price_mom_12m", "lpr_5y", "rate_deviation_bp", "as_of_leverage", "as_of_price", "as_of_lpr",
                    "property_month", "sales_area_yoy", "investment_yoy"),
    "fiscal_external": ("fiscal_revenue_month", "revenue_cum_yoy", "fiscal_expenditure_month", "expenditure_cum_yoy",
                        "trade_month", "exports_yoy", "imports_yoy", "ism_month", "ism"),
}

_AS_OF_TABLES = ("derived_monthly", "derived_quarterly", "leverage",
                 "house_price", "lpr", "fiscal", "external_demand")


def _data_as_of() -> dict:
    """7 张来源表各自最新日期（YYYY-MM）；表空/缺 date → 该键 null。"""
    import pandas as pd
    out = {}
    for t in _AS_OF_TABLES:
        try:
            df = _load_full(t)
            d = df["date"].max() if not df.empty and "date" in df.columns else None
            out[t] = str(d)[:7] if pd.notna(d) else None
        except Exception:
            out[t] = None
    return out


def build_section_snapshot() -> dict:
    """Assemble the per-section scalar snapshot fed to the model. Never raises.

    Builder 抛异常（空表等）→ 该板块全部字段 None + missing 记全名；
    长序列禁入 payload，缺失显式标记（模型知道「没有」而不是「不说」）。
    """
    sections = {}
    for name in SECTIONS:
        try:
            raw = _BUILDERS[name]()
        except Exception:
            raw = {}
        sec = {k: raw.get(k) for k in _FIELDS[name]}
        sec["missing"] = [k for k, v in sec.items() if v is None]
        sections[name] = sec
    try:
        macro_score = compute_signals(str(DB_PATH)).get("macro_score")
    except Exception:
        macro_score = None
    try:
        population = _latest_observation("demographics", "year", "population", "urbanization_rate",
                                         "natural_growth_rate", "age_65plus_pct", "dependency_ratio")
        if population:
            population["year"] = population["year"][:4]
    except Exception:
        population = {}
    return {"data_as_of": _data_as_of(), "macro_score": macro_score, "sections": sections,
            "demographics": population}


# ── Templates: defaults + ai_config overrides + version hash ─────────────────

# Default research brief. Formatting is enforced separately by the output
# contract, so each editable template can focus on the economics.
DEFAULT_TEMPLATES = {
    "system": (
        "你负责中国宏观研究。读者要知道当前是扩张、放缓还是分化，哪些数据支持判断，什么变化会让你改判。"
        "先给判断，再给最能区分解释的证据，最后落到可观察的下一步。数值必须来自快照，写清观测期、单位、比较基期；"
        "同月、季末、年内累计和上一年末不是同一口径。百分比与百分点分开。"
        "现象、机制推断与未知分开：同步变化不能写成因果，单月不能写成趋势，缺数不能写成零、持平或没有风险。"
        "不硬凑结论或反证，不复述框架定义，不列数字清单，不给投资建议。"
        "用简洁、可核对的中文；不用字段名、英文相位代号或模型生成过程代替解释。"
    ),
    "merrill": (
        "要判断的不是美林时钟叫什么，而是增长离趋势多远、低通胀更像需求不足还是价格传导滞后。"
        "以 phase 为模型标签。clock_period 的 GDP 是该年最新累计同比，gdp_trend 是五个年度观察的滚动中位数代理；"
        "cpi_yoy 是同一统计年已发布月份 CPI 同比均值，不是最新月同比；2% 是模型分界，不是政策目标。"
        "当前期和上年可见点的累计长度不同时，不说同比加快或环比放缓。"
        "用当月 CPI/PPI、消费/固投、全国与 31 城失业率中的有用观测交叉检验；点出增长与价格、就业是否一致。"
        "收束到下一次什么组合会确认或推翻结论，例如需求指标改善同时价格回暖，而不是只看一个象限。"
    ),
    "credit": (
        "判断货币边际是否放松、宽松有没有传到实体融资。先看 credit_month 的 M2 和 3 月平滑脉冲及其环比；"
        "credit_impulse 是 M2 同比减其 12 月均值（百分点），不等于新增信贷或社融。沿用代码 phase，"
        "只凭单月原始脉冲不能解释平滑阶段的方向或持续性。再比较 loan_month 的贷款净增量及近 12 月社融累计增量同比，"
        "后者不是官方社融存量增速。贷款优先写同比多增或少增（亿元）；loan_yoy = 金额差÷去年同月绝对值，"
        "负或很小基数时百分比只作辅助，不写成官方增长率；零基数不写百分比。"
        "给出下一期货币与融资是否同向改善的判据，不把放贷、偿还或季节性归因写成已证实。"
    ),
    "inventory": (
        "判断生产、需求和库存是否同步。用 inventory_month 的工业同比与六期均线，以及判定实际使用的 PMI 和 50 比较；"
        "PMI 若沿用旧观测，说明滞后月数。phase 是景气代理，不是企业库存余额；工业 1–2 月合报不是单月实绩。"
        "用同一期新订单、生产、原料库存与产成品库存分项区分需求拉动和被动积压。分项是扩散指数，"
        "低于 50 不是库存绝对为负。若 RatingDog 制造业（pmi_caixin）与官方制造业同月分歧，"
        "可指出样本分化，不能用民间数代替官方相位。结尾给下一期验证条件。"
    ),
    "debt": (
        "判断谁在加杠杆、谁在收缩、是否只是部门对冲。引用 debt_month 和近四季居民、企业、政府杠杆率变化"
        "（占 GDP 的百分点），用 net_change_4q 核对总体方向；phase 是模型分类，不等同于经济质量或违约概率。"
        "区分债务余额、杠杆率和融资流量；只有比率变动不能判断究竟是债务变化还是名义 GDP 分母变化。"
        "如果政府加杠杆而居民去杠杆，解释这支持什么、不能证明什么，给出下一季哪个部门或比率改变会动摇判断。"
    ),
    "real_estate": (
        "判断成交和价格是否企稳，融资条件宽松是否传到需求。优先用带日期的全国销售面积、投资累计同比和"
        "10 城新房环比指数 12 期平均（100=环比持平；不是全国 70 城，也不是过去 12 个月累计涨幅）。"
        "再用利率与居民杠杆率解释融资条件，不把 LPR 当实际按揭利率、把模型杠杆余量当真实购房能力。"
        "分清销售、投资和价格；若方向分化，点明哪一项未验证，给出成交与价格怎样配合才算改善。"
    ),
    "fiscal_external": (
        "分别回答财政力度与外需是否改善，再讨论二者有没有共同支撑增长。财政收入、支出是全国一般公共预算累计同比，"
        "只有同统计月才能直接比较；两条增速之差不等于赤字或财政脉冲。贸易引用当月美元口径，出口额增长不是出口量增长；"
        "美国 ISM 是另一个月份可能不同的调查，只用于印证，不代表全部海外需求。"
        "说清可验证的同步或背离，以及下一期应观察支出、出口与 ISM 中的哪两项。"
    ),
    "report": (
        "写一份中国宏观研究报告，站在当前数据截面的投资研究视角，不给交易建议。"
        "用 macro_score 说明整体状态、压力、置信度和贡献；用六板块及 demographics 核对机制与不确定性，"
        "同批短评只作提纲，若它与快照冲突以快照为准。人口是历年统计，不解释当月波动。"
        "固定 12 节：核心结论、数据与置信度、增长与需求、价格与利润传导、信用与政策、债务与居民资产负债表、"
        "房地产、财政与外需、结构与长期约束、矛盾与交叉验证、情景与触发条件、观察清单；以本轮指定二级标题为准。"
        "每节围绕一个问题展开，不在每节重复打分。写清哪项能改变主结论，哪项只是边际变化；"
        "至少解释两个真实分歧（证据不够就说明），基准、上行、下行情景都给能回读到数据的触发条件。"
        "避免把样本、方向或历史相关性当作已验证的传导。"
    ),
    "overall": (
        "用五行说明当下经济最像什么，以及为什么。"
        "「**总判定**」给扩张度、压力、置信度与一句宏观状态；三者不是上涨概率。"
        "「**一致点**」用两条同向的实物或融资观测说明内生动力，不必逐节复述。"
        "「**背离点**」点出相反的两项贡献或经济证据及其期次，说明谁支撑谁拖累。"
        "「**演化方向**」说明主要支撑能否扩散，给出一项依赖的条件，不预测具体点位。"
        "「**风险信号**」提出下一期一项确认与一项证伪指标及观察方向。"
    ),
}


def get_templates() -> dict:
    """默认模板 + ai_config.json templates{} 覆盖（仅接受非空字符串、仅已知键）。"""
    overrides = ai_config.load().get("templates") or {}
    return {**DEFAULT_TEMPLATES,
            **{k: v for k, v in overrides.items()
               if k in DEFAULT_TEMPLATES and isinstance(v, str) and v.strip()}}


def template_hash(tpls: dict | None = None) -> str:
    """sha256(全模板确定性序列化)，64 hex；出处行展示前 8 位。"""
    t = tpls if tpls is not None else get_templates()
    return hashlib.sha256(
        json.dumps(t, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ── Generation: one structured call → validate → retry once → fallback ───────

def _build_messages(snapshot: dict, tpls: dict) -> list[dict]:
    req = "\n".join(f"- sections.{k}：{tpls[k]}" for k in SECTIONS)
    user = ("数据快照（JSON）：\n" + json.dumps(snapshot, ensure_ascii=False)
            + "\n\n写作要求（每条对应输出 JSON 的一个字段）：\n" + req
            + f"\n- overall：{tpls['overall']}"
            # 格式契约在末尾全局重申（模型对末尾指令最敏感）；示例锚定预期形态。
            + '\n\n输出格式（对所有文本字段强制，优先级高于「连贯成段」的默认写法和上方各写作要求的默认结构）：'
              '\n① 每个板块写 3 行，依次回答当前判断、证据或矛盾、下一期看什么；'
              '每行以 **短结论** 开头（不超过 15 字），后接简短展开，板块总长 180–320 字；'
              '② overall 固定 5 行，共 280–500 字，依次以 **总判定** **一致点** **背离点** **演化方向** **风险信号** 开头；'
              '③ 字段值内的换行用 \\n 转义；'
              '\n示例（credit 字段的值）："**货币条件中性偏紧** M2 同比 7.7%，较上期回落 0.3 个百分点，'
              '信贷脉冲 -0.74 个百分点处于负区间。\\n**宽松确认尚未出现** 脉冲方向仍向下，'
              '实体融资需求未见明显改善。"'
            + '\n\n只输出一个 JSON 对象，形如 {"sections": {"merrill": "…", "credit": "…", '
              '"inventory": "…", "debt": "…", "real_estate": "…", "fiscal_external": "…"}, '
              '"overall": "…"}，不要输出任何其他文字。')
    return [{"role": "system", "content": tpls["system"]},
            {"role": "user", "content": user}]


def _section_messages(name: str, snapshot: dict, tpls: dict) -> list[dict]:
    layout = ("固定五行，依次以 **总判定** **一致点** **背离点** **演化方向** **风险信号** 开头，总长 280–500 字。"
              if name == "overall" else
              "三行，每行以 **短结论** 开头，依次回答当前判断、证据或矛盾、下一期看什么，总长 180–320 字。")
    user = ("数据快照（JSON）：\n" + json.dumps(snapshot, ensure_ascii=False)
            + f"\n\n写作要求：{tpls[name]}"
            + "\n" + layout + "请直接输出中文文本，不要 JSON、总标题或代码围栏。")
    return [{"role": "system", "content": tpls["system"]},
            {"role": "user", "content": user}]


def _report_messages(snapshot: dict, parts: dict, tpls: dict,
                     headings: tuple[str, ...]) -> list[dict]:
    """Prompt for the cached long-form report attached to the same batch."""
    compact = {
        "overall": parts.get("overall", ""),
        "sections": {k: parts.get(k, "") for k in SECTIONS},
    }
    user = (
        "数据快照（JSON）：\n" + json.dumps(snapshot, ensure_ascii=False)
        + "\n\n同一批次短评（JSON）：\n" + json.dumps(compact, ensure_ascii=False)
        + "\n\n完整报告写作 Skill：\n" + tpls["report"]
        + "\n\n本次分段任务：只写以下章节，必须逐字使用给定的二级标题，"
          "不要写报告总标题、其他章节、前言或代码围栏：\n"
        + "\n".join(f"## {heading}" for heading in headings)
        + "\n每节约 120–175 字；本段合计约 500–700 字。不重复其他章节判断。"
        + "\n\n本次是完整报告任务：忽略系统提示中“只输出 JSON”的格式要求，"
          "直接输出中文 Markdown。不要输出代码围栏、前言或生成过程。"
    )
    system = (
        tpls["system"]
        + "\n本次完整报告任务例外：最终输出必须是中文 Markdown，而不是 JSON。"
          "其余关于只使用快照、不得编造、不得投资建议的约束全部保留。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _clean_report(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()
    obj = _extract_json(raw)
    if isinstance(obj, dict):
        for key in ("report", "markdown", "content"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return raw


def _valid_report_part(text: str, headings: tuple[str, ...]) -> bool:
    body = text.strip()
    return (
        200 <= len(body) <= 1800
        and all(f"## {heading}" in body for heading in headings)
    )


def _trim_section(text: str, limit: int = REPORT_SECTION_MAX_CHARS) -> str:
    """Keep complete arguments; never leave a dangling list item or ellipsis."""
    body = text.strip()
    if len(body) <= limit:
        return body
    lines = body.splitlines()
    if any(line.startswith(("- ", "* ")) for line in lines):
        kept: list[str] = []
        used = 0
        for line in lines:
            cost = len(line) + (1 if kept else 0)
            if used + cost > limit:
                break
            kept.append(line)
            used += cost
        if kept:
            return "\n".join(kept).rstrip()
    cut = max(body.rfind(mark, 0, limit) for mark in ("。", "！", "？"))
    if cut >= limit // 2:
        return body[:cut + 1].rstrip()
    return body[:limit].rstrip()


def _fit_report_part(text: str, headings: tuple[str, ...]) -> str:
    """Bound each generated section while retaining every required heading."""
    chunks: list[str] = []
    current: str | None = None
    body: list[str] = []
    wanted = {f"## {heading}": heading for heading in headings}

    def flush() -> None:
        if current is not None:
            chunks.append(f"{current}\n{_trim_section(chr(10).join(body))}")

    for raw in text.replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if line in wanted:
            flush()
            current = line
            body = []
        elif current is not None and line:
            body.append(line)
    flush()
    return "\n\n".join(chunks)


def _valid_report(text: str) -> bool:
    body = text.strip()
    return (
        REPORT_MIN_CHARS <= len(body) <= REPORT_MAX_CHARS
        and all(f"## {heading}" in body for heading in REPORT_HEADINGS)
    )


def _call_report_part(profile: dict, key: str, snapshot: dict, parts: dict,
                      tpls: dict, name: str, headings: tuple[str, ...]) -> str:
    """Generate one bounded report segment, retrying one malformed response."""
    messages = _report_messages(snapshot, parts, tpls, headings)
    last_format_error = ""
    for attempt in range(2):
        if attempt:
            messages = messages + [
                {"role": "assistant", "content": last_format_error},
                {
                    "role": "user",
                    "content": "上一段未通过格式校验。请只输出指定章节，并逐字保留 "
                               "指定的 ## 二级标题，不要输出总标题或其他内容。",
                },
            ]
        raw = ai_client.call_chat(
            profile, key, messages, timeout=REPORT_PART_TIMEOUT_S)
        text = _clean_report(raw)
        if _valid_report_part(text, headings):
            return _fit_report_part(text, headings)
        last_format_error = f"分段 {name} 未包含全部指定标题或长度不合格"
        _log.warning("commentary 报告分段校验失败（%s）：len=%s",
                     name, len(text))
    raise ValueError(last_format_error)


def _call_report(profile: dict, key: str, snapshot: dict,
                 parts: dict, tpls: dict) -> tuple[str | None, str | None]:
    """Generate the optional long report without invalidating the short batch.

    Three bounded sections run sequentially, then concatenate in fixed order.
    A provider/format failure leaves the compact commentary intact and marks
    only the full-report action missing.
    """
    try:
        chunks = [
            _call_report_part(profile, key, snapshot, parts, tpls, name, headings)
            for name, headings in REPORT_PARTS
        ]
    except ai_client.AiError as e:
        _log.warning("commentary 完整报告调用失败（stage=%s）：%s", e.stage, e)
        return None, f"全文报告生成失败（{e.stage}）"
    except Exception as e:
        _log.exception("commentary 完整报告生成异常")
        return None, f"全文报告生成异常（{type(e).__name__}）"
    report = REPORT_TITLE + "\n\n" + "\n\n".join(chunks)
    if not _valid_report(report):
        _log.warning("commentary 完整报告校验失败：len=%s", len(report))
        return None, "全文报告未通过长度/格式校验"
    return report, None


def _extract_json(text: str) -> dict | None:
    """提取第一个可解析的平衡 {...}：容忍前后散文、markdown 围栏、嵌套大括号。

    从每个 '{' 起做深度计数（识别字符串内大括号/转义），平衡时 json.loads；
    失败则取下一个 '{' 重来。
    """
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:
                        break                       # 该平衡块非 JSON → 下一个 '{'
                    return obj if isinstance(obj, dict) else None
    return None


def _valid_text(v, max_chars: int = 600) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= max_chars


def _compact_overall(v) -> str:
    """Keep the homepage overview genuinely compact even when the model rambles.

    The long-form output belongs in the separate report call. For the five-line
    overview we retain at most five non-empty lines and trim to 900 characters at
    a sentence boundary rather than discarding the whole otherwise-valid batch.
    """
    if not isinstance(v, str):
        return ""
    lines = [line.strip() for line in v.splitlines() if line.strip()][:5]
    text = "\n".join(lines)
    if len(text) <= 900:
        return text
    cut = text.rfind("。", 0, 900)
    if cut < 500:
        return text[:899].rstrip("。 ") + "…"
    return text[:cut].rstrip("。 ") + "…"


def _unwrap_section(text: str, name: str) -> str:
    """补调防御：强对齐模型若服从 system 的 JSON 规则而返回包裹 → 取本板块值。

    依次试 obj[name]、obj.sections[name]、单键包装的唯一字符串；
    取不出 → 空串（校验失败，走 last-good），绝不让带大括号的原始 JSON 过校验。
    """
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return text                                 # 纯文本：正常路径原样返回
    for cand in (obj, obj.get("sections")):
        if isinstance(cand, dict) and _valid_text(cand.get(name)):
            return cand[name]
    strs = [v for v in obj.values() if isinstance(v, str)]
    return strs[0] if len(strs) == 1 and len(obj) == 1 else ""


def _validate_structured(raw: str) -> tuple[dict, list[str]]:
    """返回 (通过校验的 parts, problems)。

    校验：sections 是 dict 且 6 板块键全；每值为非空字符串且 ≤600 字；overall 同。
    """
    parts: dict = {}
    problems: list[str] = []
    obj = _extract_json(raw)
    if obj is None:
        return parts, ["输出不是合法 JSON 对象"]
    secs = obj.get("sections")
    if not isinstance(secs, dict):
        problems.append("sections 不是对象")
    else:
        for k in SECTIONS:
            if k not in secs:
                problems.append(f"sections.{k} 缺失")
            elif not _valid_text(secs[k]):
                problems.append(f"sections.{k} 为空或超 600 字")
            else:
                parts[k] = secs[k].strip()
    overall = _compact_overall(obj.get("overall"))
    if not _valid_text(overall, 900):
        problems.append("overall 为空或超 900 字")
    else:
        parts["overall"] = overall
    return parts, problems


def _call_structured_with_fallback(profile: dict, key: str,
                                   snapshot: dict, tpls: dict) -> dict | None:
    """结构化一次 → 校验 → 带错误反馈重试一次 → 逐板块补调。全败返回 None。"""
    messages = _build_messages(snapshot, tpls)
    best: dict = {}
    transport_failed = False
    for _ in range(2):                              # 首次 + 带错误反馈重试一次
        try:
            raw = ai_client.call_chat(profile, key, messages, timeout=600.0)   # 推理模型（kimi-k3）结构化整轮实测 4-6 分钟
        except ai_client.AiError as e:
            transport_failed = True                 # 网络/http 错误：换格式重试与补调均无意义
            _log.warning("commentary 结构化调用失败（stage=%s）：%s", e.stage, e)
            break
        parts, problems = _validate_structured(raw)
        if not problems:
            return parts
        _log.warning("commentary 结构化校验未通过：%s（输出 %s 字）",
                     "；".join(problems), len(raw))
        best.update(parts)                          # 保留已合格的部分
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"上次输出不合格：{'；'.join(problems)}。请只输出符合要求的 JSON。"}]
    # 降级：逐板块补调——只补缺失/不合格的键（最多 7 次补调），纯文本输出更易通过。
    # transport_failed 时跳过：死网络上补调只是每次白吃 60s timeout
    if not transport_failed:
        for name in (*SECTIONS, "overall"):
            limit = 900 if name == "overall" else 600
            if _valid_text(best.get(name), limit):
                continue
            try:
                text = ai_client.call_chat(profile, key, _section_messages(name, snapshot, tpls), timeout=300.0)
            except ai_client.AiError as e:
                _log.warning("commentary 板块补调失败（%s，stage=%s）：%s", name, e.stage, e)
                break                               # 同结构化循环：网络错误换板块重试无意义
            text = _unwrap_section(text, name)
            if name == "overall":
                text = _compact_overall(text)
            if _valid_text(text, limit):
                best[name] = text.strip()
            else:
                _log.warning("commentary 板块补调未通过：%s（输出 %s 字）", name, len(text))
    return (
        best if all(_valid_text(best.get(k), 900 if k == "overall" else 600)
                    for k in (*SECTIONS, "overall"))
        else None
    )


def generate(blocking: bool = True) -> dict:
    """Snapshot → compact call → optional report call → persist one batch.

    blocking=True  → caller waits for the model call（测试/脚本直调）。
    blocking=False → fire-and-forget on a worker thread (startup/refresh/手动 POST)。
    锁在调用方线程获取：返回时 busy 信号已成立，紧跟其后的 get_current() 必然
    看到 generating（旧实现线程内才抢锁，调用方先读会竞态出「旧 ok 态」）。
    """
    if not _gen_lock.acquire(blocking=False):
        # 已有生成在飞——不叠加第二个。锁被持有本身就是 busy 信号。
        return {"status": "generating", "msg": "已有生成在进行中…"}
    if not blocking:
        try:
            threading.Thread(target=_generate_locked_and_release, daemon=True).start()
        except Exception:
            _gen_lock.release()   # 线程起不来必须还锁，否则 busy 卡死（F10 同类）
            raise
        return {"status": "generating", "msg": "评论生成中…"}
    try:
        return _generate_locked()
    finally:
        _gen_lock.release()


def _configured() -> tuple[dict | None, str | None]:
    """resolve_active() 唯一配置入口 + key_for() 取密钥。"""
    profile = ai_config.resolve_active()
    key = ai_config.key_for(profile["name"]) if profile else None
    return profile, key or None


def _generate_impl() -> dict:
    """锁自获取版（测试直调入口；正常链路请走 generate()）。"""
    if not _gen_lock.acquire(blocking=False):
        # Another generation is in flight — don't stack a second one. The lock
        # being held IS the busy signal, so there is nothing to flag here.
        return {"status": "generating", "msg": "已有生成在进行中…"}
    try:
        return _generate_locked()
    finally:
        _gen_lock.release()


def _generate_locked_and_release() -> None:
    """worker 线程入口：generate() 已在调用方持锁，这里只负责执行+释放。"""
    try:
        out = _generate_locked()
        # 非阻塞路径的返回䛤无人接收——至少落日志，否则生成失败完全不可观测
        if out.get("status") == "error":
            _log.warning("commentary 后台生成失败：%s", out.get("msg"))
    except Exception:
        _log.exception("commentary 后台生成抛出未捕获异常")
    finally:
        _gen_lock.release()


def _generate_locked() -> dict:
    """前提：调用方已持有 _gen_lock（释放由 generate()/_generate_impl 的 finally 负责）。"""
    try:
        profile, key = _configured()
        if profile is None:
            return {"status": "empty", "msg": "未配置 AI 模型", "hint": "/ai-settings"}
        if not key:
            return {"status": "empty", "msg": f"profile「{profile['name']}」未配置密钥",
                    "hint": "/ai-settings"}
        snapshot = build_section_snapshot()
        tpls = get_templates()
        parts = _call_structured_with_fallback(profile, key, snapshot, tpls)
        if parts is None:                      # 全败 → 不写库，保留 last-good
            return {"status": "error",
                    "msg": "生成失败：重试与逐板块补调均未通过校验（已保留上一版评论）"}
        # Persist the compact batch first. The overview can receive the new
        # summary while the slower long-form call is still running; report
        # failure can no longer discard an otherwise valid summary.
        out = _persist_batch(snapshot, parts, profile, tpls)
        report_text, report_msg = _call_report(profile, key, snapshot, parts, tpls)
        if report_text:
            out = _append_report(
                out["provenance"]["generated_at"], snapshot, report_text, profile, tpls)
        elif report_msg:
            out["report_msg"] = report_msg
        return out
    except Exception as e:
        return {"status": "error", "msg": f"生成失败：{type(e).__name__}: {e}"}


# ── Persistence: 7 compact rows + optional long report (shared batch ts) ─────

KEEP_BATCHES = 10   # 轮转：保留最近 N 个 generated_at 批次（M4c）


def _prune(conn: sqlite3.Connection) -> None:
    """保留最近 KEEP_BATCHES 个 ts 批次，更旧批次整体删除（与 insert 同事务）。"""
    old = conn.execute(
        f"SELECT DISTINCT ts FROM {COMMENTARY_TABLE} "
        f"ORDER BY ts DESC LIMIT -1 OFFSET {KEEP_BATCHES}").fetchall()
    if old:
        conn.executemany(f"DELETE FROM {COMMENTARY_TABLE} WHERE ts = ?",
                         [(r[0],) for r in old])


def _persist_batch(snapshot: dict, parts: dict, profile: dict, tpls: dict,
                   report_text: str | None = None) -> dict:
    ts = _ts()
    rows = [(ts, json.dumps(snapshot["data_as_of"], ensure_ascii=False),
             json.dumps(snapshot, ensure_ascii=False),   # phase_snapshot 复用：模型看到的原始输入
             parts[name], profile["model"], name,
             profile.get("endpoint", "chat_completions"),
             template_hash(tpls), profile["name"])
            for name in (*SECTIONS, "overall")]
    if report_text:
        rows.append((
            ts, json.dumps(snapshot["data_as_of"], ensure_ascii=False),
            json.dumps(snapshot, ensure_ascii=False), report_text, profile["model"],
            REPORT_SECTION, profile.get("endpoint", "chat_completions"),
            template_hash(tpls), profile["name"],
        ))
    conn = _connect()
    try:
        _ensure_table(conn)
        conn.executemany(
            f"INSERT INTO {COMMENTARY_TABLE} "
            "(ts, data_as_of, phase_snapshot, text, model, section, "
            "endpoint, template_hash, profile, stale) VALUES (?,?,?,?,?,?,?,?,?,0)",
            rows)
        _prune(conn)
        conn.commit()
        batch = _latest_batch(conn)
    finally:
        conn.close()
    return batch


def _latest_batch(conn: sqlite3.Connection) -> dict:
    ts = conn.execute(f"SELECT MAX(ts) FROM {COMMENTARY_TABLE}").fetchone()[0]
    if ts is None:
        return _empty()
    rows = conn.execute(
        f"SELECT * FROM {COMMENTARY_TABLE} WHERE ts = ?", (ts,)).fetchall()
    batch = _batch_to_dict(rows)
    if not batch["stale"]:
        # Compare what the model actually saw. A refresh can revise the same
        # observation month, and CLI/supplement swaps don't run the API hook.
        saved = _saved_snapshot(rows[0])
        try:
            batch["stale"] = not saved or saved != _live_snapshot(str(DB_PATH), _db_version())
        except Exception:
            _log.exception("无法核对历史评论的数据快照")
            batch["stale"] = True
    return batch


@lru_cache(maxsize=4)
def _live_snapshot(_path: str, _version: tuple) -> dict:
    """Reuse the input snapshot between polls, keyed by the live DB version."""
    return build_section_snapshot()


def _saved_snapshot(row: sqlite3.Row) -> dict:
    try:
        snapshot = json.loads(row["phase_snapshot"])
        return snapshot if isinstance(snapshot, dict) else {}
    except (ValueError, TypeError):
        return {}


def _append_report(ts: str, snapshot: dict, report_text: str,
                   profile: dict, tpls: dict) -> dict:
    """Attach the long report to an already-persisted compact batch."""
    conn = _connect()
    try:
        _ensure_table(conn)
        conn.execute(
            f"INSERT INTO {COMMENTARY_TABLE} "
            "(ts, data_as_of, phase_snapshot, text, model, section, "
            "endpoint, template_hash, profile, stale) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                ts, json.dumps(snapshot["data_as_of"], ensure_ascii=False),
                json.dumps(snapshot, ensure_ascii=False), report_text,
                profile["model"], REPORT_SECTION,
                profile.get("endpoint", "chat_completions"),
                template_hash(tpls), profile["name"],
            ),
        )
        conn.commit()
        return _latest_batch(conn)
    finally:
        conn.close()


def _batch_to_dict(rows: list[sqlite3.Row]) -> dict:
    first = rows[0]
    sections: dict = {}
    overall = ""
    report = ""
    stale = False
    for row in rows:
        section = row["section"] or "overall"
        if section == "overall":
            overall = row["text"]
        elif section == REPORT_SECTION:
            report = row["text"]
        else:
            sections[section] = row["text"]
        stale = stale or bool(row["stale"])
    # data_as_of：新批次存 JSON dict；legacy 单行是裸 "YYYY-MM" → 兜底 dict
    try:
        as_of = json.loads(first["data_as_of"])
        if not isinstance(as_of, dict):
            raise ValueError
    except Exception:
        as_of = {"derived_monthly": first["data_as_of"]}
    score = _saved_snapshot(first).get("macro_score")
    if isinstance(score, dict) and isinstance(score.get("as_of"), str):
        as_of["macro_score"] = score["as_of"][:7]
    return {"status": "ok", "stale": stale, "overall": overall, "sections": sections,
            "report": report, "report_status": "ready" if report else "missing",
            "report_msg": None if report else "本批次暂无全文报告",
            "provenance": {"model": first["model"], "endpoint": first["endpoint"],
                           "template_hash": first["template_hash"], "data_as_of": as_of,
                           "profile": first["profile"], "generated_at": first["ts"]}}


def _empty() -> dict:
    d = {"status": "empty", "msg": "暂无评论"}
    profile, key = _configured()
    if profile is None or not key:
        d["hint"] = "/ai-settings"
    return d


def get_current() -> dict:
    """Return the latest commentary batch, or a generating/empty status.

    生成在途（锁被持有）时返回上一批 + ``regenerating: True``——而不是把卡片
    清空成「生成中」（旧行为会在每次模型调用期间扔掉一整版完好的评论）。
    读取只豁免「no such table」（fresh install）；其余错误冒泡，不把读取失败
    静默伪装成「暂无评论」（与 F13/signal_history 同一口径）。
    """
    conn = _connect()
    try:
        if not _table_ready:
            _ensure_table(conn)
        batch = _latest_batch(conn)
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
        return _empty()
    finally:
        conn.close()
    if _gen_lock.locked():
        if batch.get("status") != "ok":
            return {"status": "generating", "msg": "评论生成中…", "regenerating": False}
        return {
            **batch,
            "status": "generating",
            "msg": "评论重新生成中…（以下为上一版）",
            "regenerating": True,
        }
    return batch


# ── History: read-only batch index + single-batch detail (M4c 呈现层) ────────

def history_index() -> list[dict]:
    """批次索引：ts 倒序；每批一行（GROUP BY ts），overall 行取前 80 字作预览。"""
    conn = _connect()
    try:
        if not _table_ready:
            _ensure_table(conn)
        rows = conn.execute(
            f"SELECT ts, model, profile, template_hash, MAX(stale) AS stale, "
            f"MAX(CASE WHEN COALESCE(section,'overall')='overall' THEN text END) AS ov, "
            f"MAX(CASE WHEN section='{REPORT_SECTION}' THEN 1 ELSE 0 END) AS report_available "
            f"FROM {COMMENTARY_TABLE} GROUP BY ts ORDER BY ts DESC").fetchall()
    finally:
        conn.close()
    return [{"generated_at": r["ts"], "model": r["model"], "profile": r["profile"],
             "template_hash": r["template_hash"], "status": "ok",
             "stale": bool(r["stale"]),
             "report_available": bool(r["report_available"]),
             "overall_preview": (r["ov"][:80] + "…") if r["ov"] and len(r["ov"]) > 80
                                else (r["ov"] or "")}
            for r in rows]


def get_batch(ts: str) -> dict | None:
    """单批详情：复用 _batch_to_dict（含 legacy 兜底），无此批次 → None。"""
    conn = _connect()
    try:
        if not _table_ready:
            _ensure_table(conn)
        rows = conn.execute(
            f"SELECT * FROM {COMMENTARY_TABLE} WHERE ts = ?", (ts,)).fetchall()
    finally:
        conn.close()
    return _batch_to_dict(rows) if rows else None


# ── stale wiring ──────────────────────────────────────────────────────────────

def mark_stale() -> None:
    """所有未 stale 行置 stale=1（数据/配置已变）。失败静默——评论非关键路径。"""
    try:
        conn = _connect()
        try:
            _ensure_table(conn)
            conn.execute(f"UPDATE {COMMENTARY_TABLE} SET stale = 1 WHERE stale = 0")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def mark_stale_and_regenerate() -> dict:
    """Called after a successful data refresh: mark old rows stale + trigger
    a fresh generation (refresh-as-rerun policy)."""
    mark_stale()
    return generate(blocking=False)


def ensure_on_startup() -> None:
    """lifespan hook: generate if no commentary exists yet (fire-and-forget)."""
    try:
        conn = _connect()
        _ensure_table(conn)
        n = conn.execute(f"SELECT COUNT(*) FROM {COMMENTARY_TABLE}").fetchone()[0]
        conn.close()
        if n == 0:
            generate(blocking=False)
    except Exception:
        pass  # startup must never crash on commentary


if __name__ == "__main__":
    # manual smoke test: python -m backend.app.core.commentary
    print(json.dumps(build_section_snapshot(), ensure_ascii=False, indent=2))
