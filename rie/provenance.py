"""Inclusion evidence and the publication state machine.

Citing a retracted study is not the same as pooling it.

A systematic review can cite a retracted trial in at least four ways, and only
one of them can support a finding:

  1. Included in the meta-analysis, contributing weight to a pooled estimate.
     This is the only case that licenses a finding.
  2. Listed in the excluded studies table, with a stated reason. Contributes
     nothing. Citing it is correct practice.
  3. Discussed in the narrative -- background, limitations, or explicitly noting
     the retraction. Contributes nothing.
  4. Cited in the methods, for instance to borrow a measurement approach.
     Contributes nothing.

Cases 2, 3 and 4 are the most likely source of a false finding in this project.
Reporting that a review's pooled estimate is contaminated when the trial sat in
its excluded studies table would not merely be wrong; it would be wrong in a way
that discredits every other finding. So inclusion must be established from the
forest plot or the analysis data table, never from the reference list.

The licensed chain is:

    retracted DOI
      -> cited by synthesis            (citation walk: candidate generation only)
      -> appears as a row in a specific analysis's data
      -> contributes weight to a pooled estimate
      -> removing it changes that estimate

Only the third step licenses a finding.

Separately, a review that already excluded the retracted study, or has already
been updated to remove it, is doing the right thing and must never be flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from .gate import GateReport, ImpactAssessment

#: How long review authors get to respond before a finding may be published.
NOTIFICATION_RESPONSE_DAYS = 30


class InclusionEvidence(str, Enum):
    """How we know a retracted study relates to a synthesis.

    Only ANALYSIS_DATA_ROW and FOREST_PLOT_ROW can support a finding.
    """
    #: The study appears as a row in a named analysis's study-level data.
    ANALYSIS_DATA_ROW = "analysis_data_row"
    #: The study appears as a row in a forest plot, with a weight.
    FOREST_PLOT_ROW = "forest_plot_row"

    #: Everything below is explicitly insufficient.
    REFERENCE_LIST_ONLY = "reference_list_only"
    EXCLUDED_STUDIES_TABLE = "excluded_studies_table"
    NARRATIVE_MENTION = "narrative_mention"
    METHODS_CITATION = "methods_citation"
    CHARACTERISTICS_TABLE_ONLY = "characteristics_table_only"
    UNKNOWN = "unknown"


#: The only evidence kinds that establish a study contributed to a pooled result.
POOLING_EVIDENCE = frozenset({
    InclusionEvidence.ANALYSIS_DATA_ROW,
    InclusionEvidence.FOREST_PLOT_ROW,
})

#: Evidence that the review handled the retraction correctly. Never flag these.
CORRECT_PRACTICE_EVIDENCE = frozenset({
    InclusionEvidence.EXCLUDED_STUDIES_TABLE,
    InclusionEvidence.NARRATIVE_MENTION,
    InclusionEvidence.METHODS_CITATION,
})


def licenses_finding(evidence: InclusionEvidence) -> bool:
    """Whether this evidence kind can support a published finding at all."""
    return evidence in POOLING_EVIDENCE


@dataclass(frozen=True)
class InclusionLink:
    """A claim that one retracted study relates to one analysis, and the proof.

    ``locator`` records where in the document the evidence was found, so a
    reviewer can check it without rerunning anything. ``weight`` is the study's
    percentage weight in the pooled estimate, when known: a row present at zero
    weight contributes nothing and must not license a finding.
    """
    retracted_doi: str
    synthesis_id: str
    analysis_id: str | None
    study_label: str
    evidence: InclusionEvidence
    locator: str = ""
    weight: float | None = None

    @property
    def contributes(self) -> bool:
        """Whether this study actually fed the pooled estimate."""
        if not licenses_finding(self.evidence):
            return False
        if self.analysis_id is None:
            # Pooling evidence must name the analysis it belongs to; a review can
            # contain many, and a delta is meaningless without knowing which.
            return False
        return self.weight is None or self.weight > 0

    @property
    def is_correct_practice(self) -> bool:
        return self.evidence in CORRECT_PRACTICE_EVIDENCE


@dataclass(frozen=True)
class InclusionCheck:
    """The verdict on whether a synthesis pooled any retracted study."""
    links: tuple[InclusionLink, ...]

    @property
    def contributing(self) -> tuple[InclusionLink, ...]:
        return tuple(link for link in self.links if link.contributes)

    @property
    def confirmed(self) -> bool:
        return bool(self.contributing)

    @property
    def handled_correctly(self) -> bool:
        """The review cited retracted studies but pooled none of them."""
        return not self.confirmed and any(link.is_correct_practice for link in self.links)

    @property
    def study_ids(self) -> set[str]:
        return {link.study_label for link in self.contributing}

    @property
    def reason(self) -> str:
        if self.confirmed:
            return "%d retracted study/studies contribute weight to the pooled estimate" \
                % len(self.contributing)
        if self.handled_correctly:
            kinds = sorted({link.evidence.value for link in self.links})
            return "retracted studies are cited but not pooled (%s); the review handled " \
                   "this correctly and must not be flagged" % ", ".join(kinds)
        if not self.links:
            return "no link between any retracted study and this synthesis was established"
        return "no evidence that any retracted study contributed to a pooled estimate"


class FindingState(str, Enum):
    """Distinct states, kept distinct in the data model from the start.

    Internally we may generate as many candidates as we like. Publication is a
    separate state reached only through every gate.
    """
    #: The citation walk suggested this synthesis. Proves nothing.
    CANDIDATE = "candidate"
    #: Inclusion confirmed from analysis-level data.
    INCLUSION_CONFIRMED = "inclusion_confirmed"
    #: Reproduce-gate passed and a delta was computed.
    VERIFIED = "verified"
    #: An epidemiologist or systematic reviewer has assessed the batch.
    EXPERT_REVIEWED = "expert_reviewed"
    #: Authors, and any implicated guideline committee, contacted with workings.
    AUTHORS_NOTIFIED = "authors_notified"
    #: All gates cleared.
    PUBLISHED = "published"

    #: Terminal states that are not failures of the pipeline.
    REJECTED_NOT_POOLED = "rejected_not_pooled"
    REJECTED_GATE_FAILED = "rejected_gate_failed"
    REJECTED_NO_MATERIAL_CHANGE = "rejected_no_material_change"


@dataclass
class ExpertReview:
    reviewer: str
    reviewed_on: date
    clinically_material: bool
    comment: str = ""


@dataclass
class AuthorNotification:
    recipients: tuple[str, ...]
    sent_on: date
    workings_url: str = ""
    response_received: bool = False
    response_summary: str = ""

    def window_elapsed(self, as_of: date | None = None,
                       days: int = NOTIFICATION_RESPONSE_DAYS) -> bool:
        return (as_of or date.today()) >= self.sent_on + timedelta(days=days)


@dataclass
class Finding:
    """A candidate finding and everything that must be true before publication.

    ``state`` is derived rather than set, so no code path can mark something
    published without the underlying evidence being present.
    """
    synthesis_id: str
    synthesis_title: str
    analysis_id: str | None
    retracted_dois: tuple[str, ...]
    inclusion: InclusionCheck | None = None
    impact: ImpactAssessment | None = None
    expert_review: ExpertReview | None = None
    notification: AuthorNotification | None = None
    #: Set when the review has already been updated to remove the retracted
    #: study. Such reviews are doing the right thing.
    already_corrected: bool = False
    published: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def gate(self) -> GateReport | None:
        return self.impact.gate if self.impact else None

    @property
    def state(self) -> FindingState:
        if self.already_corrected:
            return FindingState.REJECTED_NOT_POOLED
        if self.inclusion is None:
            return FindingState.CANDIDATE
        if not self.inclusion.confirmed:
            return FindingState.REJECTED_NOT_POOLED
        if self.impact is None:
            return FindingState.INCLUSION_CONFIRMED
        if not self.impact.gate.passed:
            return FindingState.REJECTED_GATE_FAILED
        if not self.impact.materially_different:
            return FindingState.REJECTED_NO_MATERIAL_CHANGE
        if self.expert_review is None:
            return FindingState.VERIFIED
        if self.notification is None:
            return FindingState.EXPERT_REVIEWED
        if not self.published:
            return FindingState.AUTHORS_NOTIFIED
        return FindingState.PUBLISHED

    @property
    def verified(self) -> bool:
        """Arithmetic and inclusion both established. Internal use only."""
        return self.state in (FindingState.VERIFIED, FindingState.EXPERT_REVIEWED,
                              FindingState.AUTHORS_NOTIFIED, FindingState.PUBLISHED)

    def publication_blockers(self, as_of: date | None = None) -> list[str]:
        """Everything still standing between this finding and publication.

        All four conditions must hold: the gate passed, inclusion was confirmed
        from analysis-level data, an expert assessed it, and the authors were
        contacted and given time to respond.
        """
        blockers: list[str] = []

        if self.already_corrected:
            blockers.append("the review has already been corrected to remove the retracted study")
            return blockers

        if self.inclusion is None:
            blockers.append("inclusion has not been checked; the citation walk alone proves nothing")
        elif not self.inclusion.confirmed:
            blockers.append("inclusion not confirmed: " + self.inclusion.reason)

        if self.impact is None:
            blockers.append("no recomputation has been attempted")
        else:
            if not self.impact.gate.passed:
                blockers.append("reproduce-gate did not pass: " + self.impact.gate.reason)
            elif not self.impact.materially_different:
                blockers.append("removing the retracted studies does not materially change "
                                "the conclusion")

        if self.expert_review is None:
            blockers.append("no epidemiologist or systematic reviewer has assessed this")
        elif not self.expert_review.clinically_material:
            blockers.append("the reviewing expert judged the change not clinically material")

        if self.notification is None:
            blockers.append("the review authors have not been contacted")
        elif not (self.notification.response_received
                  or self.notification.window_elapsed(as_of)):
            blockers.append("the authors' response window has not elapsed")

        return blockers

    def publishable(self, as_of: date | None = None) -> bool:
        return not self.publication_blockers(as_of)

    def summary(self) -> str:
        """One-line human summary. Never asserts more than the evidence supports."""
        if not self.verified or self.impact is None:
            return "%s: %s" % (self.synthesis_title[:70], self.state.value)
        before, after = self.impact.effect_before, self.impact.effect_after
        if self.impact.collapsed:
            return ("%s: removing %d retracted study/studies leaves nothing to pool"
                    % (self.synthesis_title[:70], len(self.impact.removed)))
        moved = " and the confidence interval now crosses the null" if self.impact.crossed_null else ""
        return ("%s: pooled estimate moves from %.4g to %.4g after removing %d retracted "
                "study/studies%s" % (self.synthesis_title[:70], before, after,
                                     len(self.impact.removed), moved))
