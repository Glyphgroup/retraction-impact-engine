"""Validation of per-study effect sizes and the zero-cell policy."""
from __future__ import annotations

import math

import pytest

from rie import (
    AnalysisConfig,
    Continuous,
    Dichotomous,
    EffectMeasure,
    ExclusionReason,
    Generic,
    Method,
    Model,
    OEVariance,
    study_effect,
)

TIGHT = 1e-12


def cfg(measure, method=Method.IV, **kw):
    return AnalysisConfig(effect_measure=measure, method=method, **kw)


# --------------------------------------------------------------------------
# Closed-form per-study values
# --------------------------------------------------------------------------

def test_log_or_and_variance():
    s = Dichotomous("t", events1=15, total1=100, events2=25, total2=100)
    a, b, c, d = 15.0, 85.0, 25.0, 75.0
    e = study_effect(s, cfg(EffectMeasure.OR))
    assert e.yi == pytest.approx(math.log(a * d / (b * c)), abs=TIGHT)
    assert e.vi == pytest.approx(1 / a + 1 / b + 1 / c + 1 / d, abs=TIGHT)
    assert not e.correction_applied


def test_log_rr_and_variance():
    s = Dichotomous("t", events1=15, total1=100, events2=25, total2=100)
    e = study_effect(s, cfg(EffectMeasure.RR))
    assert e.yi == pytest.approx(math.log(0.15 / 0.25), abs=TIGHT)
    assert e.vi == pytest.approx(1 / 15 + 1 / 25 - 1 / 100 - 1 / 100, abs=TIGHT)


def test_risk_difference_and_variance():
    s = Dichotomous("t", events1=15, total1=100, events2=25, total2=100)
    e = study_effect(s, cfg(EffectMeasure.RD))
    assert e.yi == pytest.approx(-0.10, abs=TIGHT)
    expected = 15 * 85 / 100 ** 3 + 25 * 75 / 100 ** 3
    assert e.vi == pytest.approx(expected, abs=TIGHT)


def test_mean_difference_and_variance():
    s = Continuous("t", n1=30, mean1=12.4, sd1=3.1, n2=28, mean2=14.9, sd2=3.6)
    e = study_effect(s, cfg(EffectMeasure.MD))
    assert e.yi == pytest.approx(-2.5, abs=1e-10)
    assert e.vi == pytest.approx(3.1 ** 2 / 30 + 3.6 ** 2 / 28, abs=TIGHT)


def test_smd_uses_hedges_g_with_revman_constants():
    """Pooled SD on N-2 df, bias factor 1 - 3/(4N-9), variance uses N - 3.94."""
    s = Continuous("t", n1=30, mean1=12.4, sd1=3.1, n2=28, mean2=14.9, sd2=3.6)
    n1, n2 = 30, 28
    n = n1 + n2
    pooled = math.sqrt(((n1 - 1) * 3.1 ** 2 + (n2 - 1) * 3.6 ** 2) / (n - 2))
    g = (-2.5 / pooled) * (1 - 3 / (4 * n - 9))
    e = study_effect(s, cfg(EffectMeasure.SMD))
    assert e.yi == pytest.approx(g, abs=TIGHT)
    assert e.vi == pytest.approx(n / (n1 * n2) + g ** 2 / (2 * (n - 3.94)), abs=TIGHT)


def test_smd_bias_correction_shrinks_towards_zero():
    small = Continuous("small", n1=5, mean1=10, sd1=2, n2=5, mean2=13, sd2=2)
    large = Continuous("large", n1=500, mean1=10, sd1=2, n2=500, mean2=13, sd2=2)
    g_small = study_effect(small, cfg(EffectMeasure.SMD)).yi
    g_large = study_effect(large, cfg(EffectMeasure.SMD)).yi
    # Same standardised difference; the small trial is pulled further towards 0.
    assert abs(g_small) < abs(g_large)


def test_peto_oe_and_hypergeometric_variance():
    s = Dichotomous("t", events1=4, total1=123, events2=11, total2=139)
    a, b, c, d = 4.0, 119.0, 11.0, 128.0
    n1, n2 = 123.0, 139.0
    n = n1 + n2
    oe = a - n1 * (a + c) / n
    v = n1 * n2 * (a + c) * (b + d) / (n * n * (n - 1))
    e = study_effect(s, cfg(EffectMeasure.PETO_OR, Method.PETO))
    assert e.oe == pytest.approx(oe, abs=TIGHT)
    assert e.oe_variance == pytest.approx(v, abs=TIGHT)
    assert e.yi == pytest.approx(oe / v, abs=TIGHT)
    assert e.vi == pytest.approx(1 / v, abs=TIGHT)


def test_generic_and_oe_variance_inputs_pass_through():
    g = study_effect(Generic("g", estimate=-0.34, se=0.12), cfg(EffectMeasure.HR))
    assert g.yi == pytest.approx(-0.34, abs=TIGHT)
    assert g.vi == pytest.approx(0.12 ** 2, abs=TIGHT)

    o = study_effect(OEVariance("o", oe=-4.2, variance=8.5),
                     cfg(EffectMeasure.PETO_OR, Method.EXP_O_E_VAR))
    assert o.yi == pytest.approx(-4.2 / 8.5, abs=TIGHT)
    assert o.vi == pytest.approx(1 / 8.5, abs=TIGHT)


# --------------------------------------------------------------------------
# Zero-cell policy
# --------------------------------------------------------------------------

@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR])
def test_single_zero_adds_half_to_all_four_cells(measure):
    s = Dichotomous("t", events1=0, total1=50, events2=6, total2=50)
    e = study_effect(s, cfg(measure))
    assert e.correction_applied
    assert e.cells == (0.5, 50.5, 6.5, 44.5)


@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR])
def test_double_zero_events_is_dropped_for_ratio_measures(measure):
    s = Dichotomous("t", events1=0, total1=40, events2=0, total2=40)
    e = study_effect(s, cfg(measure))
    assert not e.usable
    assert e.excluded is ExclusionReason.NO_INFORMATION


@pytest.mark.parametrize("measure", [EffectMeasure.OR, EffectMeasure.RR])
def test_double_zero_non_events_is_dropped_for_ratio_measures(measure):
    """Everyone had the event in both arms, so the ratio is undefined too."""
    s = Dichotomous("t", events1=30, total1=30, events2=30, total2=30)
    e = study_effect(s, cfg(measure))
    assert not e.usable
    assert e.excluded is ExclusionReason.NO_INFORMATION


def test_risk_difference_keeps_double_zero_studies():
    s = Dichotomous("t", events1=0, total1=40, events2=0, total2=40)
    e = study_effect(s, cfg(EffectMeasure.RD, Method.MH))
    assert e.usable
    assert e.yi == 0.0
    assert e.vi == 0.0
    assert not e.correction_applied


def test_risk_difference_under_inverse_variance_corrects_zero_variance():
    """A zero variance would carry infinite weight, so the correction applies."""
    s = Dichotomous("t", events1=0, total1=40, events2=0, total2=40)
    e = study_effect(s, cfg(EffectMeasure.RD, Method.IV))
    assert e.usable
    assert e.correction_applied
    assert e.vi > 0


def test_peto_never_applies_a_continuity_correction():
    s = Dichotomous("t", events1=0, total1=50, events2=6, total2=50)
    e = study_effect(s, cfg(EffectMeasure.PETO_OR, Method.PETO))
    assert e.usable
    assert not e.correction_applied
    assert e.cells == (0.0, 50.0, 6.0, 44.0)


def test_peto_drops_a_study_with_no_events_at_all():
    s = Dichotomous("t", events1=0, total1=40, events2=0, total2=40)
    e = study_effect(s, cfg(EffectMeasure.PETO_OR, Method.PETO))
    assert not e.usable
    assert e.excluded is ExclusionReason.NO_INFORMATION


def test_correction_is_not_applied_when_no_cell_is_zero():
    s = Dichotomous("t", events1=1, total1=50, events2=6, total2=50)
    e = study_effect(s, cfg(EffectMeasure.OR))
    assert not e.correction_applied
    assert e.cells == (1.0, 49.0, 6.0, 44.0)


def test_swap_events_inverts_the_odds_ratio():
    s = Dichotomous("t", events1=15, total1=100, events2=25, total2=100)
    plain = study_effect(s, cfg(EffectMeasure.OR))
    swapped = study_effect(s, cfg(EffectMeasure.OR, swap_events=True))
    assert swapped.yi == pytest.approx(-plain.yi, abs=TIGHT)
    assert swapped.vi == pytest.approx(plain.vi, abs=TIGHT)


# --------------------------------------------------------------------------
# Input validation and configuration guards
# --------------------------------------------------------------------------

def test_events_exceeding_total_is_rejected():
    with pytest.raises(ValueError, match="events exceed total"):
        Dichotomous("t", events1=60, total1=50, events2=6, total2=50)


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        Dichotomous("t", events1=-1, total1=50, events2=6, total2=50)


def test_continuous_measures_reject_mantel_haenszel():
    with pytest.raises(ValueError, match="inverse-variance only"):
        AnalysisConfig(effect_measure=EffectMeasure.MD, method=Method.MH)


def test_mantel_haenszel_rejects_measures_it_does_not_support():
    with pytest.raises(ValueError, match="OR, RR and RD only"):
        AnalysisConfig(effect_measure=EffectMeasure.HR, method=Method.MH)


def test_peto_rejects_a_random_effects_model():
    with pytest.raises(ValueError, match="no random-effects Peto"):
        AnalysisConfig(effect_measure=EffectMeasure.PETO_OR, method=Method.PETO,
                       model=Model.RANDOM)


def test_zero_standard_error_is_excluded_rather_than_dividing_by_zero():
    e = study_effect(Generic("g", estimate=0.4, se=0.0), cfg(EffectMeasure.OR))
    assert not e.usable
    assert e.excluded is ExclusionReason.DEGENERATE_VARIANCE
