"""Pooling: Mantel-Haenszel, Peto, and inverse-variance fixed and random effects.

Formulae follow "Statistical methods programmed in RevMan" (Deeks & Higgins,
Cochrane Statistical Methods Group) and Cochrane Handbook ch. 10.10.

Two facts drive the structure here:

  * Random-effects analyses always pool with inverse-variance weights
    1 / (v_i + tau^2). Choosing "Mantel-Haenszel" as the random-effects method
    in RevMan changes only which fixed-effect estimate Q is measured against.
  * Heterogeneity Q always uses inverse-variance weights 1 / v_i, including for
    Mantel-Haenszel analyses.
"""
from __future__ import annotations

from math import inf, isfinite, log, sqrt

from scipy.stats import chi2, norm, t as student_t

from .effects import study_effects
from .types import (
    AnalysisConfig,
    CiMethod,
    EffectMeasure,
    ExclusionReason,
    Heterogeneity,
    Method,
    Model,
    PooledResult,
    StudyEffect,
    TauEstimator,
)

REML_MAX_ITER = 200
REML_TOL = 1e-10


class InsufficientData(ValueError):
    """Raised when no study can contribute to the pooled estimate."""


# --------------------------------------------------------------------------
# Fixed-effect estimators
# --------------------------------------------------------------------------

def _iv_fixed(effects: list[StudyEffect]) -> tuple[float, float, list[float]]:
    """Inverse-variance fixed effect. Returns (estimate, se, weights)."""
    w = [1 / e.vi for e in effects]
    total = sum(w)
    est = sum(wi * e.yi for wi, e in zip(w, effects)) / total
    return est, sqrt(1 / total), w


def _mh_or(effects: list[StudyEffect]) -> tuple[float, float, list[float]]:
    """Mantel-Haenszel odds ratio with Robins-Breslow-Greenland variance.

    Returns the estimate on the log scale.
    """
    r = s = e_ = f_ = g_ = h_ = 0.0
    weights = []
    for eff in effects:
        a, b, c, d = eff.cells
        n = a + b + c + d
        ad, bc = a * d / n, b * c / n
        r += ad
        s += bc
        e_ += (a + d) * ad / n
        f_ += (a + d) * bc / n
        g_ += (b + c) * ad / n
        h_ += (b + c) * bc / n
        weights.append(bc)
    if r <= 0 or s <= 0:
        raise InsufficientData("Mantel-Haenszel odds ratio is undefined: R=%g S=%g" % (r, s))
    est = log(r / s)
    var = e_ / (2 * r * r) + (f_ + g_) / (2 * r * s) + h_ / (2 * s * s)
    return est, sqrt(var), weights


def _mh_rr(effects: list[StudyEffect]) -> tuple[float, float, list[float]]:
    """Mantel-Haenszel risk ratio, on the log scale."""
    r = s = p = 0.0
    weights = []
    for eff in effects:
        a, b, c, d = eff.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        r += a * n2 / n
        s += c * n1 / n
        p += (n1 * n2 * (a + c) - a * c * n) / (n * n)
        weights.append(c * n1 / n)
    if r <= 0 or s <= 0:
        raise InsufficientData("Mantel-Haenszel risk ratio is undefined: R=%g S=%g" % (r, s))
    est = log(r / s)
    return est, sqrt(p / (r * s)), weights


def _mh_rd(effects: list[StudyEffect]) -> tuple[float, float, list[float]]:
    """Mantel-Haenszel risk difference."""
    j = k = num = 0.0
    weights = []
    for eff in effects:
        a, b, c, d = eff.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        w = n1 * n2 / n
        weights.append(w)
        k += w
        num += w * eff.yi
        j += (a * b * n2 ** 3 + c * d * n1 ** 3) / (n1 * n2 * n * n)
    if k <= 0:
        raise InsufficientData("Mantel-Haenszel risk difference is undefined")
    return num / k, sqrt(j / (k * k)), weights


def _peto_pooled(effects: list[StudyEffect]) -> tuple[float, float, list[float]]:
    """Peto / O-E-and-variance: estimate = sum(O-E) / sum(V), SE = 1/sqrt(sum V)."""
    total_v = sum(e.oe_variance for e in effects)
    total_z = sum(e.oe for e in effects)
    if total_v <= 0:
        raise InsufficientData("Peto method is undefined: sum of variances is zero")
    return total_z / total_v, sqrt(1 / total_v), [e.oe_variance for e in effects]


_MH = {
    EffectMeasure.OR: _mh_or,
    EffectMeasure.RR: _mh_rr,
    EffectMeasure.RD: _mh_rd,
}


def _fixed_effect(effects: list[StudyEffect], config: AnalysisConfig):
    if config.method in (Method.PETO, Method.EXP_O_E_VAR):
        return _peto_pooled(effects)
    if config.method is Method.MH:
        return _MH[config.effect_measure](effects)
    return _iv_fixed(effects)


# --------------------------------------------------------------------------
# Heterogeneity
# --------------------------------------------------------------------------

def _cochran_q(effects: list[StudyEffect], centre: float) -> float:
    """Q = sum w_i (y_i - centre)^2 with inverse-variance weights."""
    return sum((e.yi - centre) ** 2 / e.vi for e in effects)


def _typical_within_variance(effects: list[StudyEffect]) -> float:
    """Higgins & Thompson's typical within-study variance s^2.

    s^2 = (k - 1) * sum(w) / ( sum(w)^2 - sum(w^2) )
    """
    k = len(effects)
    if k < 2:
        return inf
    w = [1 / e.vi for e in effects]
    sw, sw2 = sum(w), sum(x * x for x in w)
    denom = sw * sw - sw2
    return (k - 1) * sw / denom if denom > 0 else inf


def _tau2_dl(effects: list[StudyEffect], q: float) -> float:
    """DerSimonian-Laird tau^2, floored at zero."""
    k = len(effects)
    if k < 2:
        return 0.0
    w = [1 / e.vi for e in effects]
    sw, sw2 = sum(w), sum(x * x for x in w)
    denom = sw - sw2 / sw
    if denom <= 0:
        return 0.0
    return max((q - (k - 1)) / denom, 0.0)


def _tau2_reml(effects: list[StudyEffect], start: float) -> float:
    """Iterative REML tau^2, floored at zero.

    tau2_new = sum w^2 ((y - mu)^2 - v) / sum w^2  +  1 / sum w,
    with w = 1 / (v + tau2) and mu the current weighted mean.
    """
    k = len(effects)
    if k < 2:
        return 0.0
    v = [e.vi for e in effects]
    y = [e.yi for e in effects]
    tau2 = max(start, 0.0)
    for _ in range(REML_MAX_ITER):
        w = [1 / (vi + tau2) for vi in v]
        sw = sum(w)
        mu = sum(wi * yi for wi, yi in zip(w, y)) / sw
        sw2 = sum(wi * wi for wi in w)
        nxt = sum(wi * wi * ((yi - mu) ** 2 - vi)
                  for wi, yi, vi in zip(w, y, v)) / sw2 + 1 / sw
        nxt = max(nxt, 0.0)
        if abs(nxt - tau2) < REML_TOL:
            return nxt
        tau2 = nxt
    return tau2


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def pool_effects(effects: list[StudyEffect], config: AnalysisConfig) -> PooledResult:
    """Pool pre-computed study effects according to ``config``."""
    usable = [e for e in effects if e.usable]
    excluded = {e.study_id: e.excluded for e in effects if not e.usable}
    if not usable:
        raise InsufficientData("no usable studies (%d excluded)" % len(excluded))

    k = len(usable)
    fixed_est, fixed_se, fixed_weights = _fixed_effect(usable, config)

    # Q always uses inverse-variance weights, but is measured against the
    # fixed-effect estimate produced by the selected method. So a
    # Mantel-Haenszel analysis centres Q on the MH estimate (Q_MH) and an
    # inverse-variance analysis centres it on the IV estimate (Q_IV). This is
    # the only arithmetic difference between the two random-effects options.
    q = _cochran_q(usable, fixed_est) if k > 1 else 0.0
    df = k - 1
    q_p = float(chi2.sf(q, df)) if df > 0 else 1.0

    tau2 = 0.0
    if config.model is Model.RANDOM:
        tau2 = _tau2_dl(usable, q)
        if config.tau_estimator is TauEstimator.REML:
            tau2 = _tau2_reml(usable, tau2)

    if config.model is Model.RANDOM:
        w = [1 / (e.vi + tau2) for e in usable]
        sw = sum(w)
        estimate = sum(wi * e.yi for wi, e in zip(w, usable)) / sw
        se = sqrt(1 / sw)
        weights = w
    else:
        estimate, se, weights = fixed_est, fixed_se, fixed_weights

    # I-squared. With DerSimonian-Laird the Q-based and tau-based definitions
    # coincide; REML needs the tau-based form.
    if config.model is Model.RANDOM and config.tau_estimator is TauEstimator.REML:
        s2 = _typical_within_variance(usable)
        i2 = 100.0 * tau2 / (tau2 + s2) if isfinite(s2) and (tau2 + s2) > 0 else 0.0
    else:
        i2 = max(100.0 * (q - df) / q, 0.0) if q > 0 and df > 0 else 0.0

    # Overall effect test and confidence interval.
    alpha = config.ci_level.alpha
    use_hksj = (config.ci_method is CiMethod.HKSJ and config.model is Model.RANDOM and k > 1)
    if use_hksj:
        sw = sum(weights)
        se_test = sqrt(sum(wi * (e.yi - estimate) ** 2
                           for wi, e in zip(weights, usable)) / (df * sw))
        crit = float(student_t.ppf(1 - alpha / 2, df))
        statistic = estimate / se_test if se_test > 0 else inf
        p_value = float(2 * student_t.sf(abs(statistic), df))
        test_df: int | None = df
    else:
        se_test = se
        crit = float(norm.ppf(1 - alpha / 2))
        statistic = estimate / se_test if se_test > 0 else inf
        p_value = float(2 * norm.sf(abs(statistic)))
        test_df = None

    total_w = sum(weights)
    return PooledResult(
        config=config,
        estimate=estimate,
        se=se_test,
        ci_low=estimate - crit * se_test,
        ci_high=estimate + crit * se_test,
        statistic=statistic,
        p_value=p_value,
        test_df=test_df,
        heterogeneity=Heterogeneity(q=q, df=df, p=q_p, i_squared=i2, tau_squared=tau2),
        weights={e.study_id: 100.0 * wi / total_w for wi, e in zip(weights, usable)},
        k=k,
        excluded=excluded,
        corrected=tuple(e.study_id for e in usable if e.correction_applied),
    )


def pool(studies, config: AnalysisConfig, *, exclude: set[str] | None = None) -> PooledResult:
    """Compute study effects then pool them.

    ``exclude`` marks study ids to drop, which is how a retraction is applied.
    """
    effects = study_effects(studies, config)
    if exclude:
        effects = [
            e if e.study_id not in exclude else
            StudyEffect(e.study_id, None, None, ExclusionReason.REMOVED_BY_CALLER)
            for e in effects
        ]
    return pool_effects(effects, config)
