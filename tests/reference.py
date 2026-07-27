"""Independent reference implementations used only by the test suite.

The value of these is that someone else wrote them. statsmodels'
StratifiedTable computes the Mantel-Haenszel odds ratio with the
Robins-Breslow-Greenland standard error; combine_effects computes
inverse-variance fixed effects, DerSimonian-Laird random effects, I-squared, Q,
and Hartung-Knapp-Sidik-Jonkman variances. Agreement with an unrelated
implementation is much stronger evidence than agreement with our own algebra
restated.

R and metafor are not installed here, so these stand in for metafor on the
paths they cover. statsmodels has no REML estimator (its "iterated" option is
Paule-Mandel), so REML is validated differently: we maximise the restricted
log-likelihood numerically with scipy and check that our iterative fixed point
sits at the maximum. Same objective, different algorithm.

Mantel-Haenszel RR and RD, and Peto, are covered by neither, and are checked
against hand-evaluated closed-form algebra in the tests.
"""
from __future__ import annotations

import warnings

import numpy as np
from scipy.optimize import minimize_scalar
from statsmodels.stats.contingency_tables import StratifiedTable
from statsmodels.stats.meta_analysis import combine_effects


def mh_odds_ratio(tables) -> tuple[float, float]:
    """(log OR, SE of log OR) by Mantel-Haenszel with RBG variance.

    ``tables`` is a sequence of (a, b, c, d) cell counts.
    """
    arrays = [np.array([[a, b], [c, d]], dtype=float) for a, b, c, d in tables]
    st = StratifiedTable(arrays)
    return float(np.log(st.oddsratio_pooled)), float(st.logodds_pooled_se)


def _combine(yi, vi):
    # statsmodels warns when its unfloored tau^2 goes negative; that case is
    # handled explicitly by the callers, so the noise is not useful here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore"):
            return combine_effects(np.asarray(yi, dtype=float),
                                   np.asarray(vi, dtype=float), method_re="dl")


def iv_fixed(yi, vi) -> tuple[float, float]:
    """(estimate, SE) by inverse-variance fixed effect."""
    res = _combine(yi, vi)
    return float(res.mean_effect_fe), float(res.sd_eff_w_fe)


def iv_random_dl(yi, vi) -> tuple[float, float, float, float, float]:
    """(estimate, SE, tau^2, Q, I^2 percent) by DerSimonian-Laird.

    Caution: statsmodels does not floor tau^2 at zero. When Q <= k - 1 it
    returns a negative tau^2, which yields negative weights and a NaN standard
    error. The DerSimonian-Laird estimator is defined as the maximum of the
    moment estimate and zero (RevMan stats doc; Handbook 10.10.4.1), so those
    outputs are unusable as a reference. Callers must check ``tau^2 >= 0``
    before comparing the random-effects fields; see
    ``test_iv_random_dl_matches_statsmodels``.
    """
    res = _combine(yi, vi)
    return (float(res.mean_effect_re), float(res.sd_eff_w_re), float(res.tau2),
            float(res.q), float(res.i2) * 100.0)


def hksj_se_random_dl(yi, vi) -> float:
    """HKSJ standard error for the DerSimonian-Laird random-effects mean."""
    res = _combine(yi, vi)
    return float(res.sd_eff_w_re_hksj)


def reml_tau2_by_optimisation(yi, vi) -> float:
    """tau^2 maximising the restricted log-likelihood, found by scipy.

    ll(t) = -0.5 sum log(v_i + t) - 0.5 log(sum w_i) - 0.5 sum w_i (y_i - mu)^2
    with w_i = 1 / (v_i + t) and mu the weighted mean at that t.
    """
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)

    def neg_ll(tau2: float) -> float:
        if tau2 < 0:
            return np.inf
        w = 1.0 / (v + tau2)
        sw = w.sum()
        mu = (w * y).sum() / sw
        ll = (-0.5 * np.log(v + tau2).sum() - 0.5 * np.log(sw)
              - 0.5 * (w * (y - mu) ** 2).sum())
        return -float(ll)

    upper = float(max(v.max() * 100.0, y.var() * 100.0, 1.0))
    best = minimize_scalar(neg_ll, bounds=(0.0, upper), method="bounded",
                           options={"xatol": 1e-12})
    # A boundary solution at zero means the unconstrained maximum is negative.
    return 0.0 if best.x < 1e-10 else float(best.x)
