"""CRCL alert engine — evaluates spec rules against collected data.

Rules come from docs/CRCL监控体系.md 决策规则. Each evaluate() returns
(status, message) with status ∈ {ok, triggered, insufficient_data}.
Only status *changes* are written to the alerts table (no log spam).

Data-driven rules use metric_points (auto-collected); judgment rules use
data/crcl_fundamentals.json (human-maintained quarters + flags).
"""

import json
import logging
import re
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, ValidationError, field_validator, model_validator

from backend.app.core import crcl_db

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FUNDAMENTALS_PATH = PROJECT_ROOT / "data" / "crcl_fundamentals.json"


class CrclDataError(ValueError):
    """data/crcl_fundamentals.json failed schema validation (field-located)."""


def format_validation_error(exc: ValidationError) -> str:
    """Collapse a pydantic ValidationError into one field-located line."""
    parts = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def validation_error_details(exc: ValidationError) -> list[dict]:
    """Structured per-field errors for API error responses."""
    return [
        {"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"], "type": e["type"]}
        for e in exc.errors()
    ]


# StrictFloat accepts int *and* float but rejects strings (e.g. "4.7"), which
# is exactly the semantic-typo guard we want for hand-maintained numbers.
class _Flags(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fed_cutting: bool = False
    clarity_act_passed: bool = False


class _Quarter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period: str
    total_revenue_m: StrictFloat | None = None
    total_revenue_yoy_pct: StrictFloat | None = None
    reserve_revenue_m: StrictFloat | None = None
    reserve_revenue_yoy_pct: StrictFloat | None = None
    reserve_return_rate_pct: StrictFloat | None = None
    other_revenue_m: StrictFloat | None = None
    subscription_services_revenue_m: StrictFloat | None = Field(default=None, allow_inf_nan=False)
    transaction_revenue_m: StrictFloat | None = Field(default=None, allow_inf_nan=False)
    revenue_breakdown_source: str | None = None
    recurring_service_revenue_m: StrictFloat | None = Field(default=None, ge=0)
    nonreserve_share_pct: StrictFloat | None = None
    nonreserve_one_off_m: StrictFloat | None = Field(default=None, ge=0)
    eps_actual: StrictFloat | None = None
    eps_consensus: StrictFloat | None = None
    usdc_circ_end_b: StrictFloat | None = None
    usdc_circ_avg_b: StrictFloat | None = None
    usdc_circ_yoy_pct: StrictFloat | None = None
    usdc_onchain_volume_t: StrictFloat | None = None
    distribution_cost_m: StrictFloat | None = None
    distribution_cost_ratio_pct: StrictFloat | None = None
    eurc_circ_m: StrictFloat | None = None
    cpn_tpv_annualized_b: StrictFloat | None = None
    cpn_quarter_tpv_b: StrictFloat | None = Field(default=None, ge=0)
    cpn_usdc_volume_yoy_pct: StrictFloat | None = None
    cpn_institutions: StrictFloat | None = None
    mint_active_enterprises_90d: int | None = Field(default=None, strict=True, ge=0)
    source: str | None = None


class _Annual(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period: str
    distribution_cost_m: StrictFloat | None = None
    coinbase_distribution_m: StrictFloat | None = None
    distribution_cost_ratio_pct: StrictFloat | None = None
    ratio_note: str | None = None
    source: str | None = None


class _Presale(BaseModel):
    model_config = ConfigDict(extra="ignore")
    arc_presale_m: StrictFloat | None = None
    arc_fdv_b: StrictFloat | None = None
    presale_revenue_guidance_m: StrictFloat | None = None
    milestone_linked_pct: StrictFloat | None = None
    source: str | None = None


class _ArcDisclosure(BaseModel):
    as_of: date
    assets_b: StrictFloat | None = Field(default=None, ge=0)
    usdc_volume_min_b: StrictFloat | None = Field(default=None, ge=0)
    daily_transactions_approx: StrictFloat | None = Field(default=None, ge=0)
    source: str


class _ArcGasMonth(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    network_fees_usdc: StrictFloat = Field(ge=0, allow_inf_nan=False)
    circle_fees_usdc: StrictFloat | None = Field(default=None, ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_attribution(self):
        if self.circle_fees_usdc is not None and self.circle_fees_usdc > self.network_fees_usdc:
            raise ValueError("Circle fees cannot exceed network fees in the same month")
        return self


class _ArcGasReference(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    average_daily_usdc: StrictFloat = Field(ge=0, allow_inf_nan=False)
    days_covered: int = Field(strict=True, ge=1, le=31)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_full_month(self):
        year, month = map(int, self.month.split("-"))
        if self.days_covered != monthrange(year, month)[1]:
            raise ValueError("Arc fee reference must cover the whole month")
        return self


class _ArcRwa(BaseModel):
    as_of: date
    balance_m: StrictFloat = Field(ge=0, allow_inf_nan=False)
    scope: Literal["provider_sample", "verified_assets"] = "verified_assets"
    coverage: str = Field(min_length=1)
    source: str = Field(min_length=1)


class _InstitutionEvidence(BaseModel):
    as_of: date
    institution: str = Field(min_length=1)
    product: str = Field(min_length=1)
    stage: Literal["已公告", "试点", "生产可用", "已验证持续使用", "已验证付费"]
    evidence: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_label: str = Field(min_length=1)


class _B2bPaymentsSample(BaseModel):
    period: str = Field(min_length=1)
    verified_b2b_m: StrictFloat = Field(ge=0, allow_inf_nan=False)
    classified_payments_m: StrictFloat = Field(gt=0, allow_inf_nan=False)
    coverage: str = Field(min_length=1)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_scope(self):
        if self.verified_b2b_m > self.classified_payments_m:
            raise ValueError("B2B volume cannot exceed classified sample volume")
        return self


class _SecondCurve(BaseModel):
    arc_gas: list[_ArcGasMonth] = Field(default_factory=list)
    arc_gas_reference: _ArcGasReference | None = None
    arc_rwa: _ArcRwa | None = None
    institutions: list[_InstitutionEvidence] = Field(default_factory=list)
    b2b_payments: _B2bPaymentsSample | None = None

    @field_validator("arc_gas")
    @classmethod
    def check_months(cls, values: list[_ArcGasMonth]):
        months = [row.month for row in values]
        if months != sorted(set(months)):
            raise ValueError("Arc fee months must be unique and in ascending order")
        return values


class FundamentalsFile(BaseModel):
    """Schema for data/crcl_fundamentals.json (unknown keys like _说明 ignored)."""

    model_config = ConfigDict(extra="ignore")
    updated_at: date | None = None
    flags: _Flags = Field(default_factory=_Flags)
    quarters: list[_Quarter] = Field(default_factory=list)
    annual: list[_Annual] = Field(default_factory=list)
    presale: _Presale | None = None
    arc: _ArcDisclosure | None = None
    second_curve: _SecondCurve | None = None


RULES = {
    "y_usdc_growth": ("yellow", "USDC 流通量同比增速 <15%（跑不赢降息）"),
    "y_nonreserve_stagnant": ("yellow", "剔除已标注一次性收入后，非储备占比连续两季停滞（变化 <1pp）"),
    "y_distribution_cost": ("yellow", "分发成本率 >60% 且无下降趋势"),
    "r_thesis_falsified": ("red", "论点证伪：2027 年中 调整后非储备占比 <10% + 流通增速 <10% + 降息持续"),
    "c_thesis_confirmed": ("confirm", "论点确认：调整后非储备占比 >15% + 流通增速 ≥20% + Clarity Act 通过"),
}


def _today():
    return datetime.now(timezone.utc).date()


def _load_fundamentals() -> FundamentalsFile:
    """Load + schema-validate the fundamentals JSON.

    Missing file → empty model (rules degrade to insufficient_data, as before).
    Unreadable / invalid JSON / schema violation → CrclDataError with a
    field-located message, so a stringified number surfaces loudly instead of
    being swallowed into a vague TypeError → "评估异常".
    """
    try:
        raw = json.loads(FUNDAMENTALS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FundamentalsFile()
    except (OSError, json.JSONDecodeError) as e:
        raise CrclDataError(f"读取/解析 crcl_fundamentals.json 失败: {e}") from e
    try:
        return FundamentalsFile.model_validate(raw)
    except ValidationError as e:
        raise CrclDataError(format_validation_error(e)) from e


_QUARTER_RE = re.compile(r"^\s*(\d{4})Q([1-4])\s*$")


def _quarter_ordinal(period: str | None) -> int | None:
    """'2026Q2' → absolute quarter index (year*4 + q-1); None if unparseable."""
    if not period:
        return None
    m = _QUARTER_RE.match(period)
    if not m:
        return None
    return int(m.group(1)) * 4 + (int(m.group(2)) - 1)


def adjusted_nonreserve_share(q: _Quarter) -> float | None:
    """Remove only documented one-offs from both numerator and denominator."""
    one_off, other, total = q.nonreserve_one_off_m, q.other_revenue_m, q.total_revenue_m
    if not one_off:
        return q.nonreserve_share_pct
    if other is None or total is None or not (one_off <= other <= total and total > one_off):
        return None
    return 100 * (other - one_off) / (total - one_off)


def _latest_nonreserve_share(f: FundamentalsFile) -> float | None:
    dated = [(i, q) for q in f.quarters if (i := _quarter_ordinal(q.period)) is not None]
    if not dated:
        return None
    return adjusted_nonreserve_share(max(dated, key=lambda pair: pair[0])[1])


def _usdc_yoy() -> float | None:
    """USDC 流通量同比（%）：最新值 vs ~365 天前的值。"""
    series = crcl_db.get_series("usdc_circ")
    if len(series) < 300:
        return None
    latest = series[-1]
    target = datetime.strptime(latest["date"], "%Y-%m-%d").timestamp() - 365 * 86400
    past = min(series, key=lambda p: abs(datetime.strptime(p["date"], "%Y-%m-%d").timestamp() - target))
    if past["value"] <= 0:
        return None
    return (latest["value"] / past["value"] - 1) * 100


def _eval_y_usdc_growth() -> tuple[str, str]:
    yoy = _usdc_yoy()
    if yoy is None:
        return "insufficient_data", "USDC 历史数据不足 300 天，无法计算同比"
    if yoy < 15:
        return "triggered", f"USDC 流通量同比 {yoy:.1f}% < 15%"
    return "ok", f"USDC 流通量同比 {yoy:.1f}% ≥ 15%"


def _eval_y_nonreserve_stagnant() -> tuple[str, str]:
    f = _load_fundamentals()
    # Compare the latest two dated quarters, not stale observations carried
    # forward when the latest quarter has no usable adjusted share.
    pts = []
    for q in f.quarters:
        ordinal = _quarter_ordinal(q.period)
        if ordinal is None:
            continue
        pts.append((ordinal, adjusted_nonreserve_share(q), q.period))
    if len(pts) < 2:
        return "insufficient_data", "不足两季调整后非储备收入占比（需核对财报）"
    pts.sort(key=lambda t: t[0])
    (prev_o, prev_s, prev_p), (last_o, last_s, last_p) = pts[-2], pts[-1]
    if last_o - prev_o != 1:
        return "insufficient_data", (
            f"最近两个季度（{prev_p} → {last_p}）不相邻，缺中间季度，无法判定连续停滞"
        )
    if last_s is None or prev_s is None:
        return "insufficient_data", f"{prev_p} → {last_p} 缺少可比的调整后非储备收入占比"
    delta = last_s - prev_s
    if abs(delta) < 1.0:
        return "triggered", f"调整后非储备占比 {prev_s:.2f}% → {last_s:.2f}%（变化 {delta:+.1f}pp，停滞）"
    return "ok", f"调整后非储备占比 {prev_s:.2f}% → {last_s:.2f}%（{delta:+.1f}pp）"


def _eval_y_distribution_cost() -> tuple[str, str]:
    f = _load_fundamentals()
    ratios = [
        q.distribution_cost_ratio_pct
        for q in f.quarters
        if q.distribution_cost_ratio_pct is not None
    ] + [
        a.distribution_cost_ratio_pct
        for a in f.annual
        if a.distribution_cost_ratio_pct is not None
    ]
    if not ratios:
        return "insufficient_data", "annual.distribution_cost_ratio_pct 未填写（对照年报后手工维护）"
    if ratios[-1] > 60:
        return "triggered", f"分发成本率 {ratios[-1]}% > 60%"
    return "ok", f"分发成本率 {ratios[-1]}% ≤ 60%"


def _eval_r_thesis_falsified() -> tuple[str, str]:
    f = _load_fundamentals()
    share = _latest_nonreserve_share(f)
    yoy = _usdc_yoy()
    now = _today()
    checkpoint = datetime(2027, 6, 30).date()
    if now < checkpoint:
        return "ok", f"检查点 2027-06-30 未到（当前 {now.isoformat()}）"
    missing = []
    if share is None:
        missing.append("调整后非储备占比")
    if yoy is None:
        missing.append("流通量同比")
    if missing:
        return "insufficient_data", "缺少：" + "、".join(missing)
    conds = share < 10 and yoy < 10 and f.flags.fed_cutting
    if conds:
        return "triggered", (
            f"调整后非储备占比 {share:.2f}% <10%，流通增速 {yoy:.1f}% <10%，降息持续 → 估值锚滑向货币基金侧"
        )
    return "ok", f"未满足证伪组合（调整后非储备 {share:.2f}%，流通同比 {yoy:.1f}%）"


def _eval_c_thesis_confirmed() -> tuple[str, str]:
    f = _load_fundamentals()
    share = _latest_nonreserve_share(f)
    yoy = _usdc_yoy()
    if share is None or yoy is None:
        return "insufficient_data", "缺少调整后非储备占比或流通量同比数据"
    conds = (
        share > 15
        and yoy >= 20
        and f.flags.clarity_act_passed
    )
    detail = f"调整后非储备占比 {share:.2f}%（需 >15%），流通同比 {yoy:.1f}%（需 ≥20%），Clarity Act {'已' if f.flags.clarity_act_passed else '未'}通过"
    return ("triggered", detail) if conds else ("ok", detail)


_EVALUATORS = {
    "y_usdc_growth": _eval_y_usdc_growth,
    "y_nonreserve_stagnant": _eval_y_nonreserve_stagnant,
    "y_distribution_cost": _eval_y_distribution_cost,
    "r_thesis_falsified": _eval_r_thesis_falsified,
    "c_thesis_confirmed": _eval_c_thesis_confirmed,
}


def current_rule_status() -> dict[str, dict]:
    """Evaluate against current data without modifying the alert history."""
    previous = crcl_db.get_rule_status()
    current = {}
    for rule, fn in _EVALUATORS.items():
        level, _desc = RULES[rule]
        try:
            status, message = fn()
        except CrclDataError as e:
            status, message = "insufficient_data", f"数据校验失败: {e}"
            logger.error("[crcl_alerts] rule %s 数据校验失败: %s", rule, e)
        except Exception as e:  # noqa: BLE001
            status, message = "insufficient_data", f"评估异常 {type(e).__name__}: {e}"
        last = previous.get(rule, {})
        current[rule] = {
            "level": level, "status": status, "message": message,
            "ts": last.get("ts") if last.get("status") == status else None,
        }
    return current


def evaluate(run_id: str) -> list[str]:
    """Evaluate all rules; persist only status changes. Returns changed rules."""
    changed: list[str] = []
    for rule, row in current_rule_status().items():
        if row["ts"] is None:
            level, status, message = row["level"], row["status"], row["message"]
            crcl_db.add_alert(rule, level, status, message)
            crcl_db.add_log(
                run_id, f"alert:{rule}",
                "alert" if status == "triggered" else "info",
                f"[{level}] {status}: {message}", 0,
            )
            changed.append(rule)
    return changed
