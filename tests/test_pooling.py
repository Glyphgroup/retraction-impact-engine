"""Validation of the pooling engine against independent implementations."""
from __future__ import annotations

import math

import pytest

from rie import (
    AnalysisConfig,
    CiLevel,
    CiMethod,
    EffectMeasure,
    Generic,
    Method,
    Model,
    TauEstimator,
    pool,
    study_effects,
)

from . import fixtures, reference

TIGHT = 1e-10
LOOSE = 1e-8


def cfg(measure, method, model=Model.FIXED, **kw):
    return AnalysisConfig(effect_measure=measure, method=method, model=model, **kw)


def _yi_vi(studies, config):
    effects = [e for e in study_effects(studies, config) if e.usable]
    return [e.yi for e in effects], [e.vi for e in effects]


# --------------------------------------------------------------------------
# Mantel-Haenszel odds ratio against statsmodels' RBG implementation
# --------------------------------------------------------------------------

def test_mh_or_matches_statsmodels_on_bcg():
    studies = fixtures.bcg()
    config = cfg(EffectMeasure.OR, Method.MH)
    result = pool(studies, config)
    tables = [s.cells for s in studies]
    ref_est, ref_se = reference.mh_odds_ratio(tables)
    assert result.estimate == pytest.approx(ref_est, abs=TIGHT)
    assert result.se == pytest.approx(ref_se, abs=TIGHT)


def test_mh_or_matches_statsmodels_with_zero_cells():
    """Zero-cell handling must agree once both sides see the same tables.

    Our engine adds 0.5 to all four cells of any table containing a zero and
    drops tables with no information, per RevMan. statsmodels does no such
    thing, so the reference is fed our post-correction tables. This checks the
    MH algebra, not the correction policy, which is tested separately.
    """
    studies = fixtures.zero_cell_studies()
    config = cfg(EffectMeasure.OR, Method.MH)
    effects = [e for e in study_effects(studies, config) if e.usable]
    result = pool(studies, config)
    ref_est, ref_se = reference.mh_odds_ratio([e.cells for e in effects])
    assert result.estimate == pytest.approx(ref_est, abs=TIGHT)
    assert result.se == pytest.approx(ref_se, abs=TIGHT)


# --------------------------------------------------------------------------
# Inverse-variance fixed and DerSimonian-Laird random
# --------------------------------------------------------------------------

@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR, EffectMeasure.RD])
def test_iv_fixed_matches_statsmodels(measure):
    studies = fixtures.bcg()
    config = cfg(measure, Method.IV)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)
    ref_est, ref_se = reference.iv_fixed(yi, vi)
    assert result.estimate == pytest.approx(ref_est, abs=TIGHT)
    assert result.se == pytest.approx(ref_se, abs=TIGHT)


@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR, EffectMeasure.RD])
def test_iv_random_dl_matches_statsmodels(measure):
    studies = fixtures.bcg()
    config = cfg(measure, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.DL)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)
    ref_est, ref_se, ref_tau2, ref_q, ref_i2 = reference.iv_random_dl(yi, vi)
    assert ref_tau2 > 0, "reference tau^2 must be positive for a valid comparison"
    assert result.estimate == pytest.approx(ref_est, abs=TIGHT)
    assert result.se == pytest.approx(ref_se, abs=TIGHT)
    assert result.heterogeneity.tau_squared == pytest.approx(ref_tau2, abs=TIGHT)
    assert result.heterogeneity.q == pytest.approx(ref_q, abs=LOOSE)
    assert result.heterogeneity.i_squared == pytest.approx(ref_i2, abs=LOOSE)


@pytest.mark.parametrize("measure", [EffectMeasure.MD, EffectMeasure.SMD])
def test_continuous_iv_fixed_matches_statsmodels(measure):
    studies = fixtures.continuous()
    config = cfg(measure, Method.IV)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)
    ref_est, ref_se = reference.iv_fixed(yi, vi)
    assert result.estimate == pytest.approx(ref_est, abs=TIGHT)
    assert result.se == pytest.approx(ref_se, abs=TIGHT)


@pytest.mark.parametrize("measure", [EffectMeasure.MD, EffectMeasure.SMD])
def test_continuous_random_effects_q_matches_statsmodels(measure):
    studies = fixtures.continuous()
    config = cfg(measure, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.DL)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)
    _, _, _, ref_q, _ = reference.iv_random_dl(yi, vi)
    assert result.heterogeneity.q == pytest.approx(ref_q, abs=LOOSE)


@pytest.mark.parametrize("measure", [EffectMeasure.MD, EffectMeasure.SMD])
def test_underdispersed_data_floors_tau_squared_and_falls_back_to_fixed(measure):
    """When Q <= k - 1 the DerSimonian-Laird tau^2 is zero, so RE equals FE.

    This dataset is where statsmodels returns a negative tau^2 and a NaN
    standard error. The floor is part of the estimator's definition, so our
    result is compared against the fixed-effect result instead.
    """
    studies = fixtures.continuous()
    config = cfg(measure, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.DL)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)

    _, _, ref_tau2, _, _ = reference.iv_random_dl(yi, vi)
    assert ref_tau2 < 0, "fixture no longer exercises the tau^2 floor"

    assert result.heterogeneity.tau_squared == 0.0
    assert result.heterogeneity.i_squared == 0.0
    fe_est, fe_se = reference.iv_fixed(yi, vi)
    assert result.estimate == pytest.approx(fe_est, abs=TIGHT)
    assert result.se == pytest.approx(fe_se, abs=TIGHT)


# --------------------------------------------------------------------------
# REML against numerical maximisation of the restricted log-likelihood
# --------------------------------------------------------------------------

@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR, EffectMeasure.RD])
def test_reml_tau2_is_the_restricted_likelihood_maximum(measure):
    studies = fixtures.bcg()
    config = cfg(measure, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.REML)
    result = pool(studies, config)
    yi, vi = _yi_vi(studies, config)
    ref = reference.reml_tau2_by_optimisation(yi, vi)
    assert result.heterogeneity.tau_squared == pytest.approx(ref, rel=1e-6, abs=1e-10)


def test_reml_tau2_floors_at_zero_for_homogeneous_data():
    """Identical estimates carry no between-study variance."""
    studies = [Generic("s%d" % i, estimate=0.25, se=0.2) for i in range(5)]
    config = cfg(EffectMeasure.OR, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.REML)
    result = pool(studies, config)
    assert result.heterogeneity.tau_squared == 0.0
    assert result.heterogeneity.q == pytest.approx(0.0, abs=TIGHT)
    assert result.heterogeneity.i_squared == 0.0


def test_reml_differs_from_dl_but_stays_close_on_bcg():
    studies = fixtures.bcg()
    dl = pool(studies, cfg(EffectMeasure.RR, Method.IV, Model.RANDOM,
                           tau_estimator=TauEstimator.DL))
    reml = pool(studies, cfg(EffectMeasure.RR, Method.IV, Model.RANDOM,
                             tau_estimator=TauEstimator.REML))
    assert dl.heterogeneity.tau_squared != reml.heterogeneity.tau_squared
    assert reml.heterogeneity.tau_squared == pytest.approx(
        dl.heterogeneity.tau_squared, rel=0.5)


# --------------------------------------------------------------------------
# Hartung-Knapp-Sidik-Jonkman
# --------------------------------------------------------------------------

def test_hksj_matches_statsmodels_and_leaves_point_estimate_alone():
    studies = fixtures.bcg()
    base = cfg(EffectMeasure.RR, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.DL)
    hksj = cfg(EffectMeasure.RR, Method.IV, Model.RANDOM, tau_estimator=TauEstimator.DL,
               ci_method=CiMethod.HKSJ)
    wald_result, hksj_result = pool(studies, base), pool(studies, hksj)

    assert hksj_result.estimate == pytest.approx(wald_result.estimate, abs=TIGHT)

    yi, vi = _yi_vi(studies, base)
    assert hksj_result.se == pytest.approx(reference.hksj_se_random_dl(yi, vi), abs=TIGHT)


def test_hksj_uses_t_distribution_on_k_minus_one_df():
    studies = fixtures.bcg()
    result = pool(studies, cfg(EffectMeasure.RR, Method.IV, Model.RANDOM,
                               ci_method=CiMethod.HKSJ))
    assert result.test_df == result.k - 1
    from scipy.stats import t as student_t
    crit = float(student_t.ppf(0.975, result.k - 1))
    assert result.ci_high - result.ci_low == pytest.approx(2 * crit * result.se, abs=TIGHT)


def test_wald_uses_normal_quantiles_at_each_ci_level():
    studies = fixtures.bcg()
    expected = {CiLevel.CI90: 1.6448536269514722,
                CiLevel.CI95: 1.959963984540054,
                CiLevel.CI99: 2.5758293035489004}
    for level, crit in expected.items():
        result = pool(studies, cfg(EffectMeasure.OR, Method.MH, ci_level=level))
        assert result.test_df is None
        assert result.ci_high - result.ci_low == pytest.approx(2 * crit * result.se, rel=1e-12)


# --------------------------------------------------------------------------
# Peto / O-E-and-variance
# --------------------------------------------------------------------------

def test_peto_matches_hand_evaluated_sums():
    """Peto pools sum(O-E) / sum(V) with SE = 1 / sqrt(sum V)."""
    studies = fixtures.bcg()
    config = cfg(EffectMeasure.PETO_OR, Method.PETO)
    result = pool(studies, config)

    total_z = total_v = 0.0
    for s in studies:
        a, b, c, d = s.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        total_z += a - n1 * (a + c) / n
        total_v += n1 * n2 * (a + c) * (b + d) / (n * n * (n - 1))
    assert result.estimate == pytest.approx(total_z / total_v, abs=TIGHT)
    assert result.se == pytest.approx(1 / math.sqrt(total_v), abs=TIGHT)


def test_exp_o_e_var_reproduces_peto_from_supplied_oe_and_variance():
    """RevMan's EXP_O_E_VAR is the Peto estimator fed pre-computed O-E and V."""
    from rie import OEVariance

    studies = fixtures.bcg()
    peto = pool(studies, cfg(EffectMeasure.PETO_OR, Method.PETO))

    rows = []
    for s in studies:
        a, b, c, d = s.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        rows.append(OEVariance(
            study_id=s.study_id,
            oe=a - n1 * (a + c) / n,
            variance=n1 * n2 * (a + c) * (b + d) / (n * n * (n - 1)),
        ))
    oev = pool(rows, cfg(EffectMeasure.PETO_OR, Method.EXP_O_E_VAR))
    assert oev.estimate == pytest.approx(peto.estimate, abs=TIGHT)
    assert oev.se == pytest.approx(peto.se, abs=TIGHT)


# --------------------------------------------------------------------------
# Mantel-Haenszel RR and RD against hand-evaluated closed forms
# --------------------------------------------------------------------------

def test_mh_rr_matches_closed_form():
    studies = fixtures.bcg()
    result = pool(studies, cfg(EffectMeasure.RR, Method.MH))
    r = s = p = 0.0
    for st in studies:
        a, b, c, d = st.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        r += a * n2 / n
        s += c * n1 / n
        p += (n1 * n2 * (a + c) - a * c * n) / (n * n)
    assert result.estimate == pytest.approx(math.log(r / s), abs=TIGHT)
    assert result.se == pytest.approx(math.sqrt(p / (r * s)), abs=TIGHT)


def test_mh_rd_matches_closed_form():
    studies = fixtures.bcg()
    result = pool(studies, cfg(EffectMeasure.RD, Method.MH))
    j = k = num = 0.0
    for st in studies:
        a, b, c, d = st.cells
        n1, n2 = a + b, c + d
        n = n1 + n2
        w = n1 * n2 / n
        k += w
        num += w * (a / n1 - c / n2)
        j += (a * b * n2 ** 3 + c * d * n1 ** 3) / (n1 * n2 * n * n)
    assert result.estimate == pytest.approx(num / k, abs=TIGHT)
    assert result.se == pytest.approx(math.sqrt(j / (k * k)), abs=TIGHT)


# --------------------------------------------------------------------------
# Structural properties
# --------------------------------------------------------------------------

def test_weights_are_percentages_summing_to_one_hundred():
    studies = fixtures.bcg()
    for config in (cfg(EffectMeasure.OR, Method.MH),
                   cfg(EffectMeasure.OR, Method.IV),
                   cfg(EffectMeasure.OR, Method.IV, Model.RANDOM),
                   cfg(EffectMeasure.PETO_OR, Method.PETO)):
        result = pool(studies, config)
        assert sum(result.weights.values()) == pytest.approx(100.0, abs=1e-9)
        assert len(result.weights) == result.k


def test_random_effects_spreads_weight_more_evenly_than_fixed():
    studies = fixtures.bcg()
    fixed = pool(studies, cfg(EffectMeasure.RR, Method.IV))
    random = pool(studies, cfg(EffectMeasure.RR, Method.IV, Model.RANDOM))
    assert max(random.weights.values()) < max(fixed.weights.values())


def test_ratio_measures_exponentiate_for_display():
    studies = fixtures.bcg()
    result = pool(studies, cfg(EffectMeasure.OR, Method.MH))
    assert result.effect == pytest.approx(math.exp(result.estimate), abs=TIGHT)
    lo, hi = result.ci
    assert lo == pytest.approx(math.exp(result.ci_low), abs=TIGHT)
    assert hi == pytest.approx(math.exp(result.ci_high), abs=TIGHT)


def test_difference_measures_are_not_exponentiated():
    studies = fixtures.bcg()
    result = pool(studies, cfg(EffectMeasure.RD, Method.MH))
    assert result.effect == pytest.approx(result.estimate, abs=TIGHT)


def test_crosses_null_uses_one_for_ratios_and_zero_for_differences():
    protective = [Generic("a", estimate=math.log(0.5), se=0.1),
                  Generic("b", estimate=math.log(0.55), se=0.1)]
    ambiguous = [Generic("a", estimate=math.log(0.98), se=0.3),
                 Generic("b", estimate=math.log(1.05), se=0.3)]
    assert not pool(protective, cfg(EffectMeasure.OR, Method.IV)).crosses_null
    assert pool(ambiguous, cfg(EffectMeasure.OR, Method.IV)).crosses_null

    rd_clear = [Generic("a", estimate=-0.20, se=0.03), Generic("b", estimate=-0.18, se=0.03)]
    rd_null = [Generic("a", estimate=-0.01, se=0.05), Generic("b", estimate=0.02, se=0.05)]
    assert not pool(rd_clear, cfg(EffectMeasure.RD, Method.IV)).crosses_null
    assert pool(rd_null, cfg(EffectMeasure.RD, Method.IV)).crosses_null


def test_single_study_analysis_has_no_heterogeneity():
    studies = [Generic("only", estimate=0.3, se=0.15)]
    result = pool(studies, cfg(EffectMeasure.OR, Method.IV))
    assert result.k == 1
    assert result.heterogeneity.df == 0
    assert result.heterogeneity.q == 0.0
    assert result.heterogeneity.i_squared == 0.0
    assert result.estimate == pytest.approx(0.3, abs=TIGHT)
    assert result.se == pytest.approx(0.15, abs=TIGHT)


def test_exclusion_removes_a_study_and_is_reported():
    studies = fixtures.bcg()
    config = cfg(EffectMeasure.RR, Method.IV, Model.RANDOM)
    full = pool(studies, config)
    reduced = pool(studies, config, exclude={"Stein & Aronson 1953"})
    assert reduced.k == full.k - 1
    assert "Stein & Aronson 1953" in reduced.excluded
    assert "Stein & Aronson 1953" not in reduced.weights
    assert reduced.estimate != full.estimate
