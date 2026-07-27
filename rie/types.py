"""Core data types for the meta-analysis engine.

Terminology follows the Cochrane Handbook (ch. 10) and "Statistical methods
programmed in RevMan" (Deeks & Higgins, Cochrane Statistical Methods Group).

Arm 1 is the experimental arm, arm 2 is the control arm, matching RevMan's
PairwiseDataRow field naming (events1/total1/events2/total2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EffectMeasure(str, Enum):
    OR = "OR"
    RR = "RR"
    RD = "RD"
    MD = "MD"
    SMD = "SMD"
    PETO_OR = "PETO_OR"
    HR = "HR"
    RATE_RATIO = "RATE_RATIO"


class Method(str, Enum):
    MH = "MH"
    IV = "IV"
    PETO = "PETO"
    EXP_O_E_VAR = "EXP_O_E_VAR"


class Model(str, Enum):
    FIXED = "FIXED"
    RANDOM = "RANDOM"


class TauEstimator(str, Enum):
    DL = "DL"
    REML = "REML"


class CiMethod(str, Enum):
    WALD = "WALD"
    HKSJ = "HKSJ"


class CiLevel(str, Enum):
    CI90 = "CI90"
    CI95 = "CI95"
    CI99 = "CI99"

    @property
    def alpha(self) -> float:
        return {"CI90": 0.10, "CI95": 0.05, "CI99": 0.01}[self.value]


#: Measures reported on the natural-log scale and exponentiated for display.
RATIO_MEASURES = frozenset({
    EffectMeasure.OR, EffectMeasure.RR, EffectMeasure.PETO_OR,
    EffectMeasure.HR, EffectMeasure.RATE_RATIO,
})


class ExclusionReason(str, Enum):
    #: No events in both arms, or no non-events in both arms. OR/RR undefined.
    NO_INFORMATION = "no_information"
    #: Required fields absent for the requested measure.
    MISSING_DATA = "missing_data"
    #: Variance is zero or non-finite even after any permitted correction.
    DEGENERATE_VARIANCE = "degenerate_variance"
    #: Caller removed it (e.g. the study is retracted).
    REMOVED_BY_CALLER = "removed_by_caller"


# --------------------------------------------------------------------------
# Study-level inputs. One class per RevMan data-entry shape.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Dichotomous:
    """A 2x2 table. events1/total1 = experimental, events2/total2 = control."""
    study_id: str
    events1: int
    total1: int
    events2: int
    total2: int

    def __post_init__(self) -> None:
        for name in ("events1", "total1", "events2", "total2"):
            v = getattr(self, name)
            if v is None or v < 0:
                raise ValueError("%s: %s must be a non-negative integer, got %r"
                                 % (self.study_id, name, v))
        if self.events1 > self.total1 or self.events2 > self.total2:
            raise ValueError("%s: events exceed total" % self.study_id)

    @property
    def cells(self) -> tuple[float, float, float, float]:
        """(a, b, c, d) = exp events, exp non-events, ctrl events, ctrl non-events."""
        return (float(self.events1), float(self.total1 - self.events1),
                float(self.events2), float(self.total2 - self.events2))


@dataclass(frozen=True)
class Continuous:
    """Arm means and standard deviations."""
    study_id: str
    n1: int
    mean1: float
    sd1: float
    n2: int
    mean2: float
    sd2: float

    def __post_init__(self) -> None:
        if self.n1 < 1 or self.n2 < 1:
            raise ValueError("%s: arm sizes must be >= 1" % self.study_id)
        if self.sd1 < 0 or self.sd2 < 0:
            raise ValueError("%s: standard deviations must be non-negative" % self.study_id)


@dataclass(frozen=True)
class Generic:
    """A pre-computed effect estimate and its standard error (inverse-variance).

    For ratio measures the estimate must already be on the natural-log scale,
    which is what RevMan stores in PairwiseDataRow.estimate/se.
    """
    study_id: str
    estimate: float
    se: float


@dataclass(frozen=True)
class OEVariance:
    """Log-rank style observed-minus-expected and its variance."""
    study_id: str
    oe: float
    variance: float


StudyData = Dichotomous | Continuous | Generic | OEVariance


# --------------------------------------------------------------------------
# Derived per-study effect
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StudyEffect:
    """A study's effect size on the analysis scale, plus its sampling variance.

    ``yi`` is on the log scale for ratio measures. ``vi`` is the variance of
    ``yi``. Studies that cannot contribute carry ``excluded`` and have yi/vi
    of None.
    """
    study_id: str
    yi: Optional[float]
    vi: Optional[float]
    excluded: Optional[ExclusionReason] = None
    #: True when a 0.5 continuity correction was added to all four cells.
    correction_applied: bool = False
    #: Retained for Mantel-Haenszel and Peto pooling, which need the table.
    cells: Optional[tuple[float, float, float, float]] = None
    #: Peto / O-E-and-variance inputs (z = O - E, v = hypergeometric variance).
    oe: Optional[float] = None
    oe_variance: Optional[float] = None

    @property
    def usable(self) -> bool:
        return self.excluded is None

    @property
    def se(self) -> Optional[float]:
        return None if self.vi is None else self.vi ** 0.5


# --------------------------------------------------------------------------
# Analysis configuration and results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalysisConfig:
    """Mirrors the RevMan PairwiseAnalysis fields that change the arithmetic."""
    effect_measure: EffectMeasure
    method: Method
    model: Model = Model.FIXED
    tau_estimator: TauEstimator = TauEstimator.DL
    ci_method: CiMethod = CiMethod.WALD
    ci_level: CiLevel = CiLevel.CI95
    #: RevMan's "swap events and non-events" toggle.
    swap_events: bool = False

    def __post_init__(self) -> None:
        m, meas = self.method, self.effect_measure
        if meas in (EffectMeasure.MD, EffectMeasure.SMD) and m is not Method.IV:
            raise ValueError("%s supports inverse-variance only, got %s" % (meas.value, m.value))
        if meas is EffectMeasure.PETO_OR and m not in (Method.PETO, Method.EXP_O_E_VAR):
            raise ValueError("PETO_OR requires method PETO or EXP_O_E_VAR")
        if m in (Method.PETO, Method.EXP_O_E_VAR) and self.model is Model.RANDOM:
            raise ValueError("RevMan provides no random-effects Peto / O-E-and-variance model")
        if m is Method.MH and meas not in (EffectMeasure.OR, EffectMeasure.RR, EffectMeasure.RD):
            raise ValueError("Mantel-Haenszel applies to OR, RR and RD only")

    @property
    def is_ratio(self) -> bool:
        return self.effect_measure in RATIO_MEASURES


@dataclass(frozen=True)
class Heterogeneity:
    q: float
    df: int
    p: float
    i_squared: float
    tau_squared: float


@dataclass
class PooledResult:
    """A pooled estimate on the analysis scale, with display-scale conveniences."""
    config: AnalysisConfig
    #: Pooled estimate on the analysis (log for ratio measures) scale.
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    #: Wald Z, or Student t when ci_method is HKSJ.
    statistic: float
    p_value: float
    #: Degrees of freedom for the overall-effect test; None when normal (Z).
    test_df: Optional[int]
    heterogeneity: Heterogeneity
    #: Per-study weights as percentages, keyed by study id, in RevMan's sense.
    weights: dict[str, float] = field(default_factory=dict)
    k: int = 0
    excluded: dict[str, ExclusionReason] = field(default_factory=dict)
    corrected: tuple[str, ...] = ()

    def _display(self, x: float) -> float:
        from math import exp
        return exp(x) if self.config.is_ratio else x

    @property
    def effect(self) -> float:
        """Point estimate on the scale RevMan prints (exponentiated if ratio)."""
        return self._display(self.estimate)

    @property
    def ci(self) -> tuple[float, float]:
        return (self._display(self.ci_low), self._display(self.ci_high))

    @property
    def crosses_null(self) -> bool:
        """Whether the confidence interval includes the no-effect value."""
        null = 1.0 if self.config.is_ratio else 0.0
        lo, hi = self.ci
        return lo <= null <= hi
