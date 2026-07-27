"""Regression: reproduce RevMan's own pooled results for a real Cochrane review.

The fixture holds 16 analyses from a Cochrane practice review, each with the
study-level numbers and the pooled result RevMan computed from them. This is the
strongest validation available to us: not a textbook example, not another Python
library, but the reference implementation the field actually uses.

Two analyses are expected to fail the gate, and that is the point. Both are
subgrouped by a covariate, and RevMan drops a study with no value for that
covariate from the overall total. We pool it, our estimate moves by more than
tolerance, and the gate refuses to emit. The test pins that behaviour so a
future change cannot quietly turn a known-bad case into a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rie.gate import Verdict, check
from rie.sources import cochrane

FIXTURE = Path(__file__).parent / "data" / "cochrane_asthma.json"

#: Analyses where RevMan and our extraction disagree on study membership.
KNOWN_MEMBERSHIP_MISMATCHES = {11, 12}

#: Every analysis in the fixture reproduces to at least this precision. RevMan
#: and our engine agree to double-precision rounding, far inside the 0.01 gate
#: tolerance, so passes are exact rather than marginal.
EXACT_TOLERANCE = 1e-12


def cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build(case):
    analysis = case["analysis"]
    config = cochrane.parse_config(analysis)
    source = analysis.get("dataSource") or cochrane.ARM_ONLY
    studies, unusable = cochrane.parse_data_rows(
        {"PairwiseDataRows": case["dataRows"]}, config, source)
    published = cochrane.parse_results({
        "result": case["published"] | {"mean": case["published"]["mean"]},
        "dataRows": [], "subgroups": [],
    })
    # The fixture stores the study count RevMan used, since the subgroup rows
    # it was derived from are not carried into the fixture.
    return config, studies, published, case["published"]["k"], unusable


def ids():
    return ["%02d-%s" % (c["analysis"]["number"], c["analysis"]["method"]) for c in cases()]


@pytest.mark.parametrize("case", cases(), ids=ids())
def test_pooled_estimate_matches_revman(case):
    """Our pooled estimate must equal RevMan's, regardless of gate verdict.

    Study membership is a separate question from arithmetic, so this checks the
    arithmetic on exactly the studies we extracted.
    """
    config, studies, published, _, _ = build(case)
    from rie import pool
    ours = pool(studies, config)
    number = case["analysis"]["number"]
    if number in KNOWN_MEMBERSHIP_MISMATCHES:
        pytest.skip("study membership differs; covered by the gate verdict test")
    assert ours.estimate == pytest.approx(published.estimate, abs=EXACT_TOLERANCE)
    assert ours.se == pytest.approx(published.se, abs=EXACT_TOLERANCE)
    assert ours.ci_low == pytest.approx(published.ci_low, abs=EXACT_TOLERANCE)
    assert ours.ci_high == pytest.approx(published.ci_high, abs=EXACT_TOLERANCE)


@pytest.mark.parametrize("case", cases(), ids=ids())
def test_heterogeneity_matches_revman(case):
    config, studies, published, _, _ = build(case)
    if case["analysis"]["number"] in KNOWN_MEMBERSHIP_MISMATCHES:
        pytest.skip("study membership differs; heterogeneity is not comparable")
    from rie import pool
    ours = pool(studies, config)
    if published.q is not None:
        assert ours.heterogeneity.q == pytest.approx(published.q, abs=1e-9)
    if published.df is not None:
        assert ours.heterogeneity.df == published.df
    if published.tau_squared is not None:
        assert ours.heterogeneity.tau_squared == pytest.approx(published.tau_squared, abs=1e-12)
    # RevMan reports I-squared as 100 for a single-study analysis, where the
    # statistic is undefined. We report 0. Only compare where df > 0.
    if published.i_squared is not None and (published.df or 0) > 0:
        assert ours.heterogeneity.i_squared == pytest.approx(published.i_squared, abs=1e-9)


@pytest.mark.parametrize("case", cases(), ids=ids())
def test_gate_verdict_is_stable(case):
    config, studies, published, k, _ = build(case)
    report = check(studies, config, published, expected_k=k)
    number = case["analysis"]["number"]
    if number in KNOWN_MEMBERSHIP_MISMATCHES:
        assert report.verdict is Verdict.MISMATCH
        assert not report.passed
    else:
        assert report.verdict is Verdict.REPRODUCED, report.reason
        assert abs(report.difference) <= EXACT_TOLERANCE


def test_every_row_yielded_usable_numbers():
    """Extraction from structured RevMan data should lose nothing."""
    for case in cases():
        _, studies, _, _, unusable = build(case)
        assert unusable == []
        assert studies


def test_reproduce_rate_is_recorded():
    """The headline measurement, pinned so a regression is visible."""
    total = passed = 0
    for case in cases():
        config, studies, published, k, _ = build(case)
        report = check(studies, config, published, expected_k=k)
        total += 1
        passed += report.passed
    assert total == 16
    assert passed == 14


def test_all_four_pooling_methods_are_exercised():
    methods = {c["analysis"]["method"] for c in cases()}
    measures = {c["analysis"]["effectMeasure"] for c in cases()}
    models = {c["analysis"].get("model", "FIXED") for c in cases()}
    assert {"IV", "MH", "PETO"} <= methods
    assert {"OR", "MD", "PETO_OR"} <= measures
    assert {"FIXED", "RANDOM"} <= models
