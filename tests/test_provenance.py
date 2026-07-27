"""Tests for inclusion evidence and the publication state machine.

The dangerous failure mode this guards against: reporting a review as
contaminated when the retracted trial was cited but never pooled.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from rie import AnalysisConfig, Dichotomous, EffectMeasure, Method, Model
from rie.gate import assess_impact
from rie.provenance import (
    AuthorNotification,
    ExpertReview,
    Finding,
    FindingState,
    InclusionCheck,
    InclusionEvidence,
    InclusionLink,
    licenses_finding,
)

TODAY = date(2026, 7, 27)


def link(evidence, *, analysis_id="A1", weight=42.0, doi="10.1/retracted"):
    return InclusionLink(retracted_doi=doi, synthesis_id="S1", analysis_id=analysis_id,
                         study_label="Boldt 2005", evidence=evidence,
                         locator="Analysis 1.1, row 3", weight=weight)


# --------------------------------------------------------------------------
# Only pooling evidence licenses a finding
# --------------------------------------------------------------------------

@pytest.mark.parametrize("evidence", [
    InclusionEvidence.ANALYSIS_DATA_ROW,
    InclusionEvidence.FOREST_PLOT_ROW,
])
def test_pooling_evidence_licenses_a_finding(evidence):
    assert licenses_finding(evidence)
    assert link(evidence).contributes


@pytest.mark.parametrize("evidence", [
    InclusionEvidence.REFERENCE_LIST_ONLY,
    InclusionEvidence.EXCLUDED_STUDIES_TABLE,
    InclusionEvidence.NARRATIVE_MENTION,
    InclusionEvidence.METHODS_CITATION,
    InclusionEvidence.CHARACTERISTICS_TABLE_ONLY,
    InclusionEvidence.UNKNOWN,
])
def test_non_pooling_evidence_never_licenses_a_finding(evidence):
    assert not licenses_finding(evidence)
    assert not link(evidence).contributes


def test_a_row_at_zero_weight_contributes_nothing():
    assert not link(InclusionEvidence.FOREST_PLOT_ROW, weight=0.0).contributes


def test_pooling_evidence_must_name_the_analysis():
    """A review holds many analyses; a delta without one is meaningless."""
    assert not link(InclusionEvidence.ANALYSIS_DATA_ROW, analysis_id=None).contributes


def test_unknown_weight_is_allowed_when_the_row_is_evidenced():
    assert link(InclusionEvidence.ANALYSIS_DATA_ROW, weight=None).contributes


# --------------------------------------------------------------------------
# The excluded-studies case, which is the one that would discredit the project
# --------------------------------------------------------------------------

def test_review_that_excluded_the_retracted_study_is_recognised_as_correct_practice():
    check = InclusionCheck((link(InclusionEvidence.EXCLUDED_STUDIES_TABLE),))
    assert not check.confirmed
    assert check.handled_correctly
    assert "handled" in check.reason and "must not be flagged" in check.reason


def test_reference_list_alone_is_neither_confirmed_nor_correct_practice():
    check = InclusionCheck((link(InclusionEvidence.REFERENCE_LIST_ONLY),))
    assert not check.confirmed
    assert not check.handled_correctly


def test_no_links_at_all_is_reported_plainly():
    check = InclusionCheck(())
    assert not check.confirmed
    assert "no link" in check.reason


def test_mixed_evidence_confirms_only_on_the_pooled_row():
    check = InclusionCheck((
        link(InclusionEvidence.NARRATIVE_MENTION, weight=None),
        link(InclusionEvidence.ANALYSIS_DATA_ROW, doi="10.1/other"),
    ))
    assert check.confirmed
    assert len(check.contributing) == 1
    assert check.contributing[0].retracted_doi == "10.1/other"


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------

def contaminated_analysis():
    """Studies where removing the retracted ones flips the conclusion."""
    studies = [
        Dichotomous("clean-1", events1=20, total1=100, events2=28, total2=100),
        Dichotomous("clean-2", events1=18, total1=100, events2=24, total2=100),
        Dichotomous("retracted-1", events1=5, total1=100, events2=30, total2=100),
        Dichotomous("retracted-2", events1=6, total1=100, events2=32, total2=100),
    ]
    config = AnalysisConfig(effect_measure=EffectMeasure.OR, method=Method.IV,
                            model=Model.FIXED)
    from rie import pool
    truth = pool(studies, config)

    class Published:
        estimable = True
        log_scale = True
        estimate = truth.estimate
        se = truth.se
        k = truth.k

    return studies, config, Published()


def finding_with_impact(**kw) -> Finding:
    studies, config, published = contaminated_analysis()
    impact = assess_impact(studies, config, published, {"retracted-1", "retracted-2"})
    return Finding(
        synthesis_id="S1", synthesis_title="Colloids for fluid resuscitation",
        analysis_id="A1", retracted_dois=("10.1/r1", "10.1/r2"),
        inclusion=InclusionCheck((link(InclusionEvidence.ANALYSIS_DATA_ROW),)),
        impact=impact, **kw)


def test_a_bare_candidate_is_only_a_candidate():
    f = Finding(synthesis_id="S1", synthesis_title="t", analysis_id=None,
                retracted_dois=("10.1/r",))
    assert f.state is FindingState.CANDIDATE
    assert not f.verified
    assert not f.publishable(TODAY)
    assert any("citation walk alone proves nothing" in b for b in f.publication_blockers(TODAY))


def test_citation_without_pooling_is_rejected_not_merely_incomplete():
    f = Finding(synthesis_id="S1", synthesis_title="t", analysis_id="A1",
                retracted_dois=("10.1/r",),
                inclusion=InclusionCheck((link(InclusionEvidence.EXCLUDED_STUDIES_TABLE),)))
    assert f.state is FindingState.REJECTED_NOT_POOLED
    assert not f.publishable(TODAY)


def test_an_already_corrected_review_is_never_flagged():
    f = finding_with_impact(already_corrected=True)
    assert f.state is FindingState.REJECTED_NOT_POOLED
    assert not f.publishable(TODAY)
    assert any("already been corrected" in b for b in f.publication_blockers(TODAY))


def test_gate_failure_rejects_the_finding():
    studies, config, published = contaminated_analysis()

    class Wrong:
        estimable = True
        log_scale = True
        estimate = published.estimate + 5.0
        se = published.se
        k = published.k

    impact = assess_impact(studies, config, Wrong(), {"retracted-1"})
    f = Finding(synthesis_id="S1", synthesis_title="t", analysis_id="A1",
                retracted_dois=("10.1/r",),
                inclusion=InclusionCheck((link(InclusionEvidence.ANALYSIS_DATA_ROW),)),
                impact=impact)
    assert f.state is FindingState.REJECTED_GATE_FAILED
    assert not f.publishable(TODAY)


def test_verified_requires_gate_and_inclusion_but_is_not_publishable():
    f = finding_with_impact()
    assert f.state is FindingState.VERIFIED
    assert f.verified
    assert not f.publishable(TODAY)
    blockers = f.publication_blockers(TODAY)
    assert any("epidemiologist" in b for b in blockers)
    assert any("authors have not been contacted" in b for b in blockers)


def test_expert_review_alone_does_not_publish():
    f = finding_with_impact(expert_review=ExpertReview("Dr Reviewer", TODAY, True))
    assert f.state is FindingState.EXPERT_REVIEWED
    assert not f.publishable(TODAY)


def test_expert_judging_the_change_immaterial_blocks_publication():
    f = finding_with_impact(
        expert_review=ExpertReview("Dr Reviewer", TODAY, clinically_material=False),
        notification=AuthorNotification(("author@example.org",), TODAY - timedelta(days=60)))
    assert not f.publishable(TODAY)
    assert any("not clinically material" in b for b in f.publication_blockers(TODAY))


def test_response_window_must_elapse_before_publication():
    f = finding_with_impact(
        expert_review=ExpertReview("Dr Reviewer", TODAY, True),
        notification=AuthorNotification(("author@example.org",), TODAY - timedelta(days=3)))
    assert f.state is FindingState.AUTHORS_NOTIFIED
    assert not f.publishable(TODAY)
    assert any("response window" in b for b in f.publication_blockers(TODAY))


def test_an_author_response_satisfies_the_window_immediately():
    f = finding_with_impact(
        expert_review=ExpertReview("Dr Reviewer", TODAY, True),
        notification=AuthorNotification(("author@example.org",), TODAY - timedelta(days=1),
                                        response_received=True))
    assert f.publishable(TODAY)


def test_all_four_conditions_together_permit_publication():
    f = finding_with_impact(
        expert_review=ExpertReview("Dr Reviewer", TODAY, True),
        notification=AuthorNotification(("author@example.org",), TODAY - timedelta(days=31)))
    assert f.publishable(TODAY)
    assert f.publication_blockers(TODAY) == []
    f.published = True
    assert f.state is FindingState.PUBLISHED


def test_published_state_cannot_be_reached_without_the_evidence():
    """Setting the flag is not enough; state is derived from evidence."""
    f = Finding(synthesis_id="S1", synthesis_title="t", analysis_id="A1",
                retracted_dois=("10.1/r",), published=True)
    assert f.state is FindingState.CANDIDATE
    assert not f.publishable(TODAY)


def test_no_material_change_is_rejected_rather_than_published():
    studies, config, published = contaminated_analysis()
    impact = assess_impact(studies, config, published, set())
    f = Finding(synthesis_id="S1", synthesis_title="t", analysis_id="A1",
                retracted_dois=(),
                inclusion=InclusionCheck((link(InclusionEvidence.ANALYSIS_DATA_ROW),)),
                impact=impact)
    assert f.state is FindingState.REJECTED_NO_MATERIAL_CHANGE


def test_summary_reports_the_movement_and_the_null_crossing():
    f = finding_with_impact()
    text = f.summary()
    assert "moves from" in text
    assert f.impact.crossed_null == ("crosses the null" in text)


def test_summary_of_an_unverified_finding_claims_nothing():
    f = Finding(synthesis_id="S1", synthesis_title="Some review", analysis_id=None,
                retracted_dois=("10.1/r",))
    assert f.summary().endswith("candidate")
