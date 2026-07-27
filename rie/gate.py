"""The reproduce-gate.

The rule this project rests on: never publish a recomputation of a synthesis we
cannot first reproduce. Concretely, before removing anything we recompute the
review's OWN published pooled estimate from its OWN study data. If our number
does not match theirs, our extraction or our method selection is wrong, and the
only honest output is silence.

Why this is the right shape. LLM extraction of meta-analytic tuples is
unreliable and pooling amplifies upstream error, so a naive extract-and-recompute
pipeline produces confident nonsense. Gating on reproduction converts that
failure mode from wrong answers into no answer. It also means the quantity we
report is a difference between two estimates computed the same way from the same
extraction, so systematic extraction error largely cancels. Low coverage is the
accepted price.

Tolerance policy, stated explicitly rather than tuned into a magic number:

  * Ratio measures are compared on the log scale, where the review's own
    arithmetic lives, with an absolute tolerance of 0.01. On a log scale 0.01 is
    roughly a 1 percent difference in the ratio, so an OR of 0.71 would have to
    disagree by more than about 0.007 to fail.
  * Difference measures are compared relatively, at 1 percent, with a small
    absolute floor so that estimates near zero do not fail on noise.
  * The study count must match. A different k means we extracted a different
    set of studies, which invalidates any later delta regardless of how close
    the pooled numbers look.

These thresholds are starting values. They should be revised once we have the
distribution of reproduction error across real reviews, and any change is a
change to a single documented constant here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import exp, isfinite

from .pooling import InsufficientData, pool
from .types import AnalysisConfig, PooledResult, StudyData

#: Absolute tolerance on the log-scale pooled estimate for ratio measures.
LOG_SCALE_TOLERANCE = 0.01
#: Relative tolerance for difference measures (RD, MD, SMD).
RELATIVE_TOLERANCE = 0.01
#: Absolute floor for difference measures, so near-zero estimates stay testable.
ABSOLUTE_FLOOR = 1e-6


class Verdict(str, Enum):
    #: Our recomputation matches the published estimate within tolerance.
    REPRODUCED = "reproduced"
    #: We computed a number and it disagrees. Publish nothing.
    MISMATCH = "mismatch"
    #: We could not compute or compare at all. Publish nothing.
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class GateReport:
    verdict: Verdict
    reason: str
    #: Our recomputation, when we managed one.
    recomputed: PooledResult | None = None
    published_estimate: float | None = None
    recomputed_estimate: float | None = None
    #: Signed difference, ours minus theirs, on the analysis scale.
    difference: float | None = None
    tolerance: float | None = None
    published_k: int | None = None
    recomputed_k: int | None = None
    #: Diagnostics that do not gate but explain a near miss.
    diagnostics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.REPRODUCED


def tolerance_for(config: AnalysisConfig, published_estimate: float) -> float:
    """The permitted absolute difference on the analysis scale."""
    if config.is_ratio:
        return LOG_SCALE_TOLERANCE
    return max(abs(published_estimate) * RELATIVE_TOLERANCE, ABSOLUTE_FLOOR)


def check(studies: list[StudyData], config: AnalysisConfig, published,
          *, expected_k: int | None = None) -> GateReport:
    """Recompute a published analysis and judge whether we reproduced it.

    ``published`` needs ``estimate``, ``se`` and ``estimable``; a
    cochrane.PublishedResult satisfies this, as does any equivalent object from
    another source.
    """
    if not getattr(published, "estimable", True):
        return GateReport(Verdict.UNVERIFIABLE,
                          "the source reports its own analysis as not estimable")

    target = getattr(published, "estimate", None)
    if target is None or not isfinite(target):
        return GateReport(Verdict.UNVERIFIABLE, "no published pooled estimate to compare against")

    if not studies:
        return GateReport(Verdict.UNVERIFIABLE, "no study-level data extracted",
                          published_estimate=target)

    try:
        ours = pool(studies, config)
    except InsufficientData as e:
        return GateReport(Verdict.UNVERIFIABLE, "recomputation failed: %s" % e,
                          published_estimate=target)

    # A scale disagreement would make the comparison meaningless, so confirm it
    # rather than assume it.
    log_scale = getattr(published, "log_scale", None)
    if log_scale is not None and bool(log_scale) != config.is_ratio:
        return GateReport(
            Verdict.UNVERIFIABLE,
            "scale mismatch: source reports logScale=%s but %s is %s"
            % (log_scale, config.effect_measure.value,
               "a ratio measure" if config.is_ratio else "a difference measure"),
            recomputed=ours, published_estimate=target,
            recomputed_estimate=ours.estimate)

    k_expected = expected_k if expected_k is not None else getattr(published, "k", None)
    tol = tolerance_for(config, target)
    difference = ours.estimate - target

    diagnostics: dict[str, float] = {}
    published_se = getattr(published, "se", None)
    if published_se not in (None, 0) and isfinite(published_se):
        diagnostics["se_difference"] = ours.se - published_se
    for name, attr in (("q", "q"), ("i_squared", "i_squared"), ("tau_squared", "tau_squared")):
        theirs = getattr(published, attr, None)
        if theirs is not None and isfinite(theirs):
            diagnostics[name + "_difference"] = getattr(ours.heterogeneity, {
                "q": "q", "i_squared": "i_squared", "tau_squared": "tau_squared"}[name]) - theirs
    if config.is_ratio:
        diagnostics["published_ratio"] = exp(target)
        diagnostics["recomputed_ratio"] = exp(ours.estimate)

    common = dict(recomputed=ours, published_estimate=target,
                  recomputed_estimate=ours.estimate, difference=difference,
                  tolerance=tol, published_k=k_expected, recomputed_k=ours.k,
                  diagnostics=diagnostics)

    if k_expected is not None and k_expected != ours.k:
        return GateReport(Verdict.MISMATCH,
                          "study count differs: source pooled %d, we pooled %d"
                          % (k_expected, ours.k), **common)

    if abs(difference) > tol:
        return GateReport(Verdict.MISMATCH,
                          "pooled estimate differs by %.6g, tolerance %.6g"
                          % (difference, tol), **common)

    return GateReport(Verdict.REPRODUCED,
                      "matched to within %.6g on the analysis scale" % tol, **common)


# --------------------------------------------------------------------------
# The delta, which only exists once the gate has passed
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ImpactAssessment:
    """What removing a set of studies does to a synthesis that reproduced."""
    config: AnalysisConfig
    gate: GateReport
    removed: tuple[str, ...]
    original: PooledResult
    revised: PooledResult | None
    #: Change on the display scale (ratio for OR/RR, natural for RD/MD).
    effect_before: float | None = None
    effect_after: float | None = None
    #: Change on the analysis scale, which is what the arithmetic acts on.
    estimate_shift: float | None = None
    relative_change: float | None = None
    crossed_null: bool = False
    lost_significance: bool = False
    collapsed: bool = False
    note: str = ""

    @property
    def materially_different(self) -> bool:
        """Whether the conclusion, not merely the number, moved.

        A crossed null or a lost significance changes what the synthesis
        supports. A 10 percent shift in the effect is the threshold used by the
        JAMA Internal Medicine 2025 analysis of retraction-affected
        meta-analyses, so it is adopted here for comparability.
        """
        if self.collapsed or self.crossed_null or self.lost_significance:
            return True
        return self.relative_change is not None and abs(self.relative_change) >= 0.10


def assess_impact(studies: list[StudyData], config: AnalysisConfig, published,
                  remove: set[str], *, expected_k: int | None = None) -> ImpactAssessment:
    """Run the gate, and only if it passes, recompute without ``remove``."""
    report = check(studies, config, published, expected_k=expected_k)
    removed = tuple(sorted(remove))
    if not report.passed:
        return ImpactAssessment(config=config, gate=report, removed=removed,
                                original=report.recomputed, revised=None,
                                note="gate did not pass, so no delta was computed")

    original = report.recomputed
    assert original is not None

    try:
        revised = pool(studies, config, exclude=remove)
    except InsufficientData as e:
        return ImpactAssessment(
            config=config, gate=report, removed=removed, original=original,
            revised=None, effect_before=original.effect, collapsed=True,
            note="nothing left to pool after removal: %s" % e)

    before, after = original.effect, revised.effect
    shift = revised.estimate - original.estimate
    # Relative change is taken on the display scale so it reads the way the
    # published claim reads.
    relative = (after - before) / abs(before) if before not in (0, None) else None

    crossed = (not original.crosses_null) and revised.crosses_null
    alpha = config.ci_level.alpha
    lost = original.p_value < alpha <= revised.p_value

    return ImpactAssessment(
        config=config, gate=report, removed=removed, original=original, revised=revised,
        effect_before=before, effect_after=after, estimate_shift=shift,
        relative_change=relative, crossed_null=crossed, lost_significance=lost,
    )
