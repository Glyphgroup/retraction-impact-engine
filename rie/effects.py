"""Per-study effect sizes and variances, with RevMan's zero-cell handling.

Formulae follow "Statistical methods programmed in RevMan" (Deeks & Higgins,
Cochrane Statistical Methods Group) and Cochrane Handbook ch. 10.

Zero-cell policy (Handbook 10.4.4, RevMan stats doc "Empty cells"):

  * Where a zero count would break the effect or its standard error, 0.5 is
    added to ALL FOUR cells of that study's table -- not only the zero cell.
  * A study with no events in both arms, or no non-events in both arms, leaves
    OR and RR undefined; such studies are dropped from OR/RR analyses.
  * Risk difference is defined when counts are zero, so double-zero studies are
    retained. Under inverse-variance a double-zero table has zero variance and
    therefore infinite weight, so the correction is applied there.
  * Peto / O-E-and-variance needs no continuity correction at all.

The trigger is therefore method-dependent, and every applied correction or
exclusion is recorded on the StudyEffect so the reproduce-gate can report it.
"""
from __future__ import annotations

from math import log, sqrt

from .types import (
    AnalysisConfig,
    Continuous,
    Dichotomous,
    EffectMeasure,
    ExclusionReason,
    Generic,
    Method,
    OEVariance,
    StudyData,
    StudyEffect,
)

CONTINUITY = 0.5


def _corrected(cells: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(x + CONTINUITY for x in cells)  # type: ignore[return-value]


def _has_zero(cells) -> bool:
    return any(x == 0 for x in cells)


def _no_information(cells) -> bool:
    """No events in both arms, or no non-events in both arms."""
    a, b, c, d = cells
    return (a == 0 and c == 0) or (b == 0 and d == 0)


# --------------------------------------------------------------------------
# Dichotomous measures
# --------------------------------------------------------------------------

def _log_or(cells):
    a, b, c, d = cells
    return log(a * d / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d


def _log_rr(cells):
    a, b, c, d = cells
    n1, n2 = a + b, c + d
    return log((a / n1) / (c / n2)), 1 / a + 1 / c - 1 / n1 - 1 / n2


def _risk_difference(cells):
    a, b, c, d = cells
    n1, n2 = a + b, c + d
    return a / n1 - c / n2, a * b / n1 ** 3 + c * d / n2 ** 3


_DICHOTOMOUS = {
    EffectMeasure.OR: _log_or,
    EffectMeasure.RR: _log_rr,
    EffectMeasure.RD: _risk_difference,
}


def _peto(cells) -> tuple[float, float]:
    """Returns (O - E, hypergeometric variance V).

    E  = n1 (a + c) / N
    V  = n1 n2 (a + c)(b + d) / (N^2 (N - 1))
    """
    a, b, c, d = cells
    n1, n2 = a + b, c + d
    n = n1 + n2
    expected = n1 * (a + c) / n
    variance = n1 * n2 * (a + c) * (b + d) / (n * n * (n - 1)) if n > 1 else 0.0
    return a - expected, variance


def _dichotomous_effect(study: Dichotomous, config: AnalysisConfig) -> StudyEffect:
    cells = study.cells
    if config.swap_events:
        a, b, c, d = cells
        cells = (b, a, d, c)

    measure = config.effect_measure

    if config.method in (Method.PETO, Method.EXP_O_E_VAR):
        # No continuity correction, by design.
        oe, v = _peto(cells)
        if v <= 0:
            return StudyEffect(study.study_id, None, None,
                               ExclusionReason.NO_INFORMATION, cells=cells)
        return StudyEffect(study.study_id, yi=oe / v, vi=1 / v, cells=cells,
                           oe=oe, oe_variance=v)

    if measure in (EffectMeasure.OR, EffectMeasure.RR):
        if _no_information(cells):
            return StudyEffect(study.study_id, None, None,
                               ExclusionReason.NO_INFORMATION, cells=cells)
        applied = _has_zero(cells)
        used = _corrected(cells) if applied else cells
        yi, vi = _DICHOTOMOUS[measure](used)
        return StudyEffect(study.study_id, yi, vi, correction_applied=applied, cells=used)

    if measure is EffectMeasure.RD:
        yi, vi = _risk_difference(cells)
        # Mantel-Haenszel weights RD by n1*n2/N and never divides by the
        # study variance, so zeros are harmless there. Inverse-variance needs
        # a non-zero variance.
        if config.method is Method.IV and vi <= 0:
            used = _corrected(cells)
            yi, vi = _risk_difference(used)
            return StudyEffect(study.study_id, yi, vi, correction_applied=True, cells=used)
        return StudyEffect(study.study_id, yi, vi, cells=cells)

    raise ValueError("measure %s is not a dichotomous measure" % measure.value)


# --------------------------------------------------------------------------
# Continuous measures
# --------------------------------------------------------------------------

def _pooled_sd(study: Continuous) -> float:
    n1, n2 = study.n1, study.n2
    num = (n1 - 1) * study.sd1 ** 2 + (n2 - 1) * study.sd2 ** 2
    return sqrt(num / (n1 + n2 - 2))


def _continuous_effect(study: Continuous, config: AnalysisConfig) -> StudyEffect:
    n1, n2 = study.n1, study.n2
    diff = study.mean1 - study.mean2

    if config.effect_measure is EffectMeasure.MD:
        vi = study.sd1 ** 2 / n1 + study.sd2 ** 2 / n2
        if vi <= 0:
            return StudyEffect(study.study_id, None, None, ExclusionReason.DEGENERATE_VARIANCE)
        return StudyEffect(study.study_id, diff, vi)

    if config.effect_measure is EffectMeasure.SMD:
        n = n1 + n2
        if n <= 2:
            return StudyEffect(study.study_id, None, None, ExclusionReason.MISSING_DATA)
        s = _pooled_sd(study)
        if s <= 0:
            return StudyEffect(study.study_id, None, None, ExclusionReason.DEGENERATE_VARIANCE)
        # Hedges' g with RevMan's linear small-sample correction.
        g = (diff / s) * (1 - 3 / (4 * n - 9))
        vi = n / (n1 * n2) + g ** 2 / (2 * (n - 3.94))
        return StudyEffect(study.study_id, g, vi)

    raise ValueError("measure %s is not a continuous measure" % config.effect_measure.value)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def study_effect(study: StudyData, config: AnalysisConfig) -> StudyEffect:
    """Compute one study's effect size and variance on the analysis scale."""
    if isinstance(study, Dichotomous):
        return _dichotomous_effect(study, config)
    if isinstance(study, Continuous):
        return _continuous_effect(study, config)
    if isinstance(study, Generic):
        if study.se is None or study.se <= 0:
            return StudyEffect(study.study_id, None, None, ExclusionReason.DEGENERATE_VARIANCE)
        return StudyEffect(study.study_id, study.estimate, study.se ** 2)
    if isinstance(study, OEVariance):
        if study.variance is None or study.variance <= 0:
            return StudyEffect(study.study_id, None, None, ExclusionReason.DEGENERATE_VARIANCE)
        return StudyEffect(study.study_id, study.oe / study.variance, 1 / study.variance,
                           oe=study.oe, oe_variance=study.variance)
    raise TypeError("unsupported study data type: %r" % type(study).__name__)


def study_effects(studies, config: AnalysisConfig) -> list[StudyEffect]:
    return [study_effect(s, config) for s in studies]
