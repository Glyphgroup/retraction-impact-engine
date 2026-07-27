"""Regression: reproduce RevMan's own pooled results for a real Cochrane review.

The fixture holds 16 analyses from a Cochrane calibration review, each with the
study-level numbers and the pooled result RevMan computed from them. This is the
strongest validation available: not a textbook example, not another Python
library, but the reference implementation the field actually uses.

All 16 reproduce, to double-precision agreement, across inverse-variance,
Mantel-Haenszel and Peto, fixed and random effects, arm-level and contrast-level
data, and odds ratio, mean difference and Peto odds ratio.

Study membership in a subgrouped analysis is derived from covariate assignments,
which are inputs. Deriving it from RevMan's own output would make the gate
circular, so that is tested explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rie import pool
from rie.gate import Verdict, check
from rie.sources import cochrane

FIXTURE = Path(__file__).parent / "data" / "cochrane_asthma.json"

#: Every analysis agrees with RevMan to at least this precision, against a gate
#: tolerance of 1e-2. Passes are exact rather than marginal.
EXACT_TOLERANCE = 1e-12


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assignments() -> dict[str, set[str]]:
    return {k: set(v) for k, v in fixture()["studyCovariateAssignments"].items()}


def cases() -> list[dict]:
    return fixture()["analyses"]


def ids() -> list[str]:
    return ["%02d-%s-%s" % (c["analysis"]["number"], c["analysis"]["method"],
                            c["analysis"]["effectMeasure"]) for c in cases()]


def build(case: dict):
    analysis = case["analysis"]
    config = cochrane.parse_config(analysis)
    eligible = cochrane.eligible_study_ids(analysis, assignments())
    studies, unusable = cochrane.parse_data_rows(
        {"PairwiseDataRows": case["dataRows"]}, config,
        analysis.get("dataSource") or cochrane.ARM_ONLY, eligible)
    published = cochrane.parse_results(
        {"result": case["published"], "dataRows": [], "subgroups": []})
    return config, studies, published, case["published"]["k"], unusable


@pytest.mark.parametrize("case", cases(), ids=ids())
def test_pooled_estimate_matches_revman(case):
    config, studies, published, _, _ = build(case)
    ours = pool(studies, config)
    assert ours.estimate == pytest.approx(published.estimate, abs=EXACT_TOLERANCE)
    assert ours.se == pytest.approx(published.se, abs=EXACT_TOLERANCE)
    assert ours.ci_low == pytest.approx(published.ci_low, abs=EXACT_TOLERANCE)
    assert ours.ci_high == pytest.approx(published.ci_high, abs=EXACT_TOLERANCE)


@pytest.mark.parametrize("case", cases(), ids=ids())
def test_heterogeneity_matches_revman(case):
    config, studies, published, _, _ = build(case)
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
def test_gate_reproduces_every_analysis(case):
    config, studies, published, k, _ = build(case)
    report = check(studies, config, published, expected_k=k)
    assert report.verdict is Verdict.REPRODUCED, report.reason
    assert abs(report.difference) <= EXACT_TOLERANCE
    assert report.recomputed_k == k


def test_reproduce_rate_is_total():
    """The headline measurement, pinned so any regression is visible."""
    passed = 0
    for case in cases():
        config, studies, published, k, _ = build(case)
        passed += check(studies, config, published, expected_k=k).passed
    assert (passed, len(cases())) == (16, 16)


def test_every_row_yielded_usable_numbers():
    for case in cases():
        _, studies, _, _, unusable = build(case)
        assert unusable == []
        assert studies


# --------------------------------------------------------------------------
# Study membership must come from inputs, not from RevMan's output
# --------------------------------------------------------------------------

def covariate_subgrouped() -> list[dict]:
    return [c for c in cases() if c["analysis"].get("subgroupType") == "COVARIATE"]


def test_covariate_subgrouping_restricts_membership():
    """A study with no value for the subgrouping covariate is not pooled.

    Two analyses in this review are affected: both drop one study. Without the
    rule our pooled estimate is off by more than tolerance and the gate refuses,
    so this is the difference between 14 of 16 and 16 of 16.
    """
    restricted = []
    for case in covariate_subgrouped():
        analysis = case["analysis"]
        eligible = cochrane.eligible_study_ids(analysis, assignments())
        assert eligible is not None
        row_ids = {str(r["studyId"]) for r in case["dataRows"]}
        dropped = row_ids - eligible
        if dropped:
            restricted.append((analysis["number"], len(dropped)))
    assert restricted == [(11, 1), (12, 1)]


def test_ignoring_the_covariate_rule_makes_the_gate_refuse():
    """Confirms the rule is load-bearing rather than decorative."""
    refused = 0
    for case in covariate_subgrouped():
        analysis = case["analysis"]
        config = cochrane.parse_config(analysis)
        studies, _ = cochrane.parse_data_rows(
            {"PairwiseDataRows": case["dataRows"]}, config,
            analysis.get("dataSource") or cochrane.ARM_ONLY, None)
        published = cochrane.parse_results(
            {"result": case["published"], "dataRows": [], "subgroups": []})
        report = check(studies, config, published, expected_k=case["published"]["k"])
        if not report.passed:
            refused += 1
    assert refused == 2


def test_non_subgrouped_analyses_have_no_membership_restriction():
    for case in cases():
        analysis = case["analysis"]
        if analysis.get("subgroupType") == "COVARIATE":
            continue
        assert cochrane.eligible_study_ids(analysis, assignments()) is None


def test_membership_is_not_read_from_the_published_result():
    """The eligible set is computable with no access to RevMan's output at all."""
    for case in covariate_subgrouped():
        eligible = cochrane.eligible_study_ids(case["analysis"], assignments())
        assert eligible is not None and eligible


def test_all_method_combinations_are_exercised():
    methods = {c["analysis"]["method"] for c in cases()}
    measures = {c["analysis"]["effectMeasure"] for c in cases()}
    models = {c["analysis"].get("model", "FIXED") for c in cases()}
    sources = {c["analysis"].get("dataSource") for c in cases()}
    assert {"IV", "MH", "PETO"} <= methods
    assert {"OR", "MD", "PETO_OR"} <= measures
    assert {"FIXED", "RANDOM"} <= models
    assert {"ONLY_ARM_LEVEL", "ONLY_CONTRAST_LEVEL"} <= sources
