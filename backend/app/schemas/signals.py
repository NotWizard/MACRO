"""Composite macro-signal schema (mirrors analysis.signals.compute_signals)."""

from pydantic import BaseModel


class MacroScorePart(BaseModel):
    name: str
    label: str
    value: float | None = None
    score: float | None = None
    weight: float
    unit: str
    as_of: str | None = None


class MacroScoreComponent(BaseModel):
    key: str
    label: str
    score: float | None = None
    weight: float
    contribution: float
    available: bool
    parts: list[MacroScorePart] = []
    as_of: str | None = None


class MacroScore(BaseModel):
    version: str
    score: float | None = None
    label: str
    pressure: float | None = None
    pressure_components: dict[str, float | None] = {}
    confidence: float
    freshness: float
    agreement: float
    coverage: float
    as_of: str | None = None
    components: dict[str, MacroScoreComponent]
    positives: list[str] = []
    negatives: list[str] = []
    warnings: list[str] = []
    weights: dict[str, float]
    method: str


class SignalSummary(BaseModel):
    merrill: dict
    credit: dict
    inventory: dict
    debt: dict
    macro_score: MacroScore | None = None
    cross_lags: dict
    # ── G23 coverage / as-of transparency ───────────────────────────────────
    # compute_signals() excludes a phase with no data and marks stale frames.
    as_of: str | None = None        # newest observation across frameworks
    included: list[str] = []        # frameworks with a current phase
    excluded: list[str] = []        # frameworks with no usable phase
    stale: list[str] = []           # frameworks kept at half weight


class PhaseFlip(BaseModel):
    framework: str            # merrill | credit | inventory | debt
    prev: str | None = None
    curr: str | None = None


class SignalHistoryRow(BaseModel):
    ts: str
    data_as_of: str | None = None
    macro_score: float | None = None
    macro_label: str | None = None
    macro_pressure: float | None = None
    macro_confidence: float | None = None
    score_version: str | None = None
    merrill: str | None = None
    credit: str | None = None
    inventory: str | None = None
    debt: str | None = None
    flips: list[PhaseFlip] = []


class SignalHistory(BaseModel):
    items: list[SignalHistoryRow]
