"""Cochrane RevMan API adapter.

Two halves, deliberately separated:

  * A thin authenticated client. The bearer token is a short-lived Keycloak JWT
    read from COCHRANE_TOKEN. It is never written to disk.
  * Pure parsers that turn API JSON into engine types. These take dicts, so they
    run against cached responses with no token and no network, which is what the
    test suite uses.

The published result returned by /analyses/{id}/results is RevMan's own pooled
estimate for the same study data. It is the reproduce-gate's target.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from ..types import (
    AnalysisConfig,
    CiLevel,
    CiMethod,
    Continuous,
    Dichotomous,
    EffectMeasure,
    Generic,
    Method,
    Model,
    OEVariance,
    StudyData,
    TauEstimator,
)
from .http import USER_AGENT, fetch_json

BASE_URL = os.environ.get("COCHRANE_API", "https://api.cochrane.org")

#: RevMan's dataSource controls whether arm-level counts or a contrast-level
#: estimate/SE is used when a study offers both.
ARM_ONLY = "ONLY_ARM_LEVEL"
CONTRAST_ONLY = "ONLY_CONTRAST_LEVEL"
PREFER_CONTRAST = "PREFER_CONTRAST_LEVEL"
PREFER_ARM = "PREFER_ARM_LEVEL"


class MissingToken(RuntimeError):
    pass


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("COCHRANE_TOKEN", "").removeprefix("Bearer ").strip()
    if not token:
        raise MissingToken("set COCHRANE_TOKEN to a bearer token from RevMan Web")
    return {"Authorization": "Bearer " + token, "User-Agent": USER_AGENT}


def get(path: str, **params: Any) -> Any:
    from .http import build_url
    url = build_url(BASE_URL + path, **params)
    return fetch_json(url, namespace="cochrane", headers=_auth_headers())


# --------------------------------------------------------------------------
# Envelope handling
# --------------------------------------------------------------------------

def unwrap(payload: Any, *keys: str) -> list[dict]:
    """Pull the list out of a RevMan collection envelope.

    Responses look like {"PairwiseDataRows": [...]} or {"Analyses": [...]}, with
    an optional "_metadata" sibling. Falls back to the first list value.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        if isinstance(payload.get(key), list):
            return payload[key]
    for key, value in payload.items():
        if key != "_metadata" and isinstance(value, list):
            return value
    return []


# --------------------------------------------------------------------------
# Analysis configuration
# --------------------------------------------------------------------------

def parse_config(analysis: dict) -> AnalysisConfig:
    """Map a PairwiseAnalysis to an AnalysisConfig.

    RevMan leaves heterogeneityEstimator absent on fixed-effect analyses; the
    value is irrelevant there but the enum needs something, so DL is used.
    """
    measure = EffectMeasure(analysis["effectMeasure"])
    method = Method(analysis["method"])
    model = Model(analysis.get("model") or "FIXED")
    estimator = TauEstimator(analysis.get("heterogeneityEstimator") or "DL")
    return AnalysisConfig(
        effect_measure=measure,
        method=method,
        model=model,
        tau_estimator=estimator,
        ci_method=CiMethod(analysis.get("ciMethod") or "WALD"),
        ci_level=CiLevel(analysis.get("ciLevel") or "CI95"),
        swap_events=bool(analysis.get("swapEvents")),
    )


# --------------------------------------------------------------------------
# Study data
# --------------------------------------------------------------------------

def _has(row: dict, *fields: str) -> bool:
    return all(row.get(f) is not None for f in fields)


def parse_data_row(row: dict, config: AnalysisConfig,
                   data_source: str = ARM_ONLY) -> StudyData | None:
    """Map one PairwiseDataRow to a study input, honouring RevMan's dataSource.

    Returns None when the row carries no numbers usable for this measure, which
    is how RevMan's own "not estimable" rows present.
    """
    study = row.get("study") or {}
    study_id = str(row.get("studyId") or study.get("id") or row.get("id"))

    arm_dichotomous = _has(row, "events1", "total1", "events2", "total2")
    arm_continuous = _has(row, "n1", "mean1", "sd1", "n2", "mean2", "sd2") or \
        (_has(row, "mean1", "sd1", "mean2", "sd2") and _has(row, "total1", "total2"))
    contrast = _has(row, "estimate", "se")
    oe = _has(row, "oe", "variance")

    if config.method in (Method.PETO, Method.EXP_O_E_VAR) and oe and not arm_dichotomous:
        return OEVariance(study_id, oe=float(row["oe"]), variance=float(row["variance"]))

    prefer_contrast = data_source in (CONTRAST_ONLY, PREFER_CONTRAST)
    if prefer_contrast and contrast:
        return Generic(study_id, estimate=float(row["estimate"]), se=float(row["se"]))
    if data_source == CONTRAST_ONLY:
        return Generic(study_id, estimate=float(row["estimate"]), se=float(row["se"])) if contrast else None

    if config.effect_measure in (EffectMeasure.MD, EffectMeasure.SMD):
        if arm_continuous:
            n1 = row.get("n1") if row.get("n1") is not None else row.get("total1")
            n2 = row.get("n2") if row.get("n2") is not None else row.get("total2")
            return Continuous(study_id, n1=int(n1), mean1=float(row["mean1"]),
                              sd1=float(row["sd1"]), n2=int(n2),
                              mean2=float(row["mean2"]), sd2=float(row["sd2"]))
        return Generic(study_id, estimate=float(row["estimate"]), se=float(row["se"])) if contrast else None

    if arm_dichotomous:
        return Dichotomous(study_id, events1=int(row["events1"]), total1=int(row["total1"]),
                           events2=int(row["events2"]), total2=int(row["total2"]))
    if contrast:
        return Generic(study_id, estimate=float(row["estimate"]), se=float(row["se"]))
    if oe:
        return OEVariance(study_id, oe=float(row["oe"]), variance=float(row["variance"]))
    return None


def parse_study_covariate_assignments(payloads: dict[str, Any],
                                      value_to_definition: dict[str, str] | None = None
                                      ) -> dict[str, set[str]]:
    """Map study id to the covariate definitions it holds a value for.

    ``payloads`` is keyed by study id, each value a studyCovariateValues
    response. Some rows name only the covariate *value*, so
    ``value_to_definition`` (from the review's covariateValues) resolves those.
    """
    lookup = value_to_definition or {}
    assignments: dict[str, set[str]] = {}
    for study_id, payload in payloads.items():
        definitions: set[str] = set()
        for row in unwrap(payload, "StudyCovariateValues"):
            value_id = str(row.get("covariateValueId") or "")
            definition = str(row.get("covariateDefinitionId") or lookup.get(value_id) or "")
            if definition:
                definitions.add(definition)
        assignments[str(study_id)] = definitions
    return assignments


def parse_covariate_value_map(payload: Any) -> dict[str, str]:
    """Map covariate value id to its covariate definition id."""
    return {str(v["id"]): str(v["covariateDefinitionId"])
            for v in unwrap(payload, "CovariateValues")
            if v.get("id") and v.get("covariateDefinitionId")}


def eligible_study_ids(analysis: dict, assignments: dict[str, set[str]]) -> set[str] | None:
    """Which studies RevMan will pool, decided from input data.

    When an analysis is subgrouped by a covariate, RevMan drops any study that
    has no value for that covariate -- from the subgroups AND from the overall
    total. Verified against every covariate-subgrouped analysis in the
    calibration review: the prediction matched RevMan's study count 7 times out
    of 7.

    Returns None when no restriction applies, so callers can distinguish "every
    row counts" from "an empty eligible set".

    This is derived from the covariate assignments, which are inputs, not from
    the pooled result. Reading membership off RevMan's own output would make the
    reproduce-gate circular.
    """
    if analysis.get("subgroupType") != "COVARIATE":
        return None
    definition = str(analysis.get("subgroupByCovariateDefinitionId") or "")
    if not definition:
        return None
    return {sid for sid, definitions in assignments.items() if definition in definitions}


def parse_data_rows(payload: Any, config: AnalysisConfig,
                    data_source: str = ARM_ONLY,
                    eligible: set[str] | None = None) -> tuple[list[StudyData], list[str]]:
    """Returns (studies, ids of rows that carried no usable numbers).

    ``eligible`` restricts which studies are pooled, per eligible_study_ids.
    Ineligible rows are not reported as unusable: their numbers are fine, they
    simply do not belong to this analysis.
    """
    studies: list[StudyData] = []
    unusable: list[str] = []
    for row in unwrap(payload, "PairwiseDataRows", "CustomPairwiseDataRows"):
        study = row.get("study") or {}
        study_id = str(row.get("studyId") or study.get("id") or row.get("id"))
        if eligible is not None and study_id not in eligible:
            continue
        parsed = parse_data_row(row, config, data_source)
        if parsed is None:
            unusable.append(str(study.get("name") or study_id))
        else:
            studies.append(parsed)
    return studies, unusable


def study_names(payload: Any) -> dict[str, str]:
    """Map study id to study name, for readable output."""
    names = {}
    for row in unwrap(payload, "PairwiseDataRows"):
        study = row.get("study") or {}
        sid = str(row.get("studyId") or study.get("id") or row.get("id"))
        if study.get("name"):
            names[sid] = study["name"]
    return names


# --------------------------------------------------------------------------
# Published result (the gate's target)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PublishedStudyResult:
    study_id: str
    estimate: float | None
    se: float | None
    weight: float | None
    estimable: bool


@dataclass(frozen=True)
class PublishedSubgroup:
    label: str
    estimate: float | None
    se: float | None
    studies: tuple[PublishedStudyResult, ...]


@dataclass(frozen=True)
class PublishedResult:
    """RevMan's own pooled estimate, on the analysis scale.

    ``log_scale`` reports whether RevMan held this estimate on the log scale,
    which lets us confirm our own scale convention rather than assume it.
    """
    estimable: bool
    log_scale: bool
    estimate: float | None
    se: float | None
    ci_low: float | None
    ci_high: float | None
    q: float | None
    df: int | None
    i_squared: float | None
    q_p: float | None
    tau_squared: float | None
    z: float | None
    p_z: float | None
    t: float | None
    p_t: float | None
    studies: tuple[PublishedStudyResult, ...]
    subgroups: tuple[PublishedSubgroup, ...] = ()

    @property
    def k(self) -> int:
        """Number of studies the source actually pooled.

        In a subgrouped analysis RevMan leaves the top-level dataRows empty and
        puts the per-study rows inside each subgroup, so the count has to walk
        the subgroups. A study appearing in more than one subgroup is counted
        once, since the overall estimate pools it once.
        """
        if self.studies:
            return sum(1 for s in self.studies if s.estimable)
        ids = {s.study_id for g in self.subgroups for s in g.studies if s.estimable}
        return len(ids)


def _parse_study_rows(rows) -> tuple[PublishedStudyResult, ...]:
    return tuple(
        PublishedStudyResult(
            study_id=str(row.get("studyId")),
            estimate=row.get("mean"),
            se=row.get("se"),
            weight=row.get("weight"),
            estimable=bool(row.get("estimable")),
        )
        for row in rows or []
    )


def parse_results(payload: dict) -> PublishedResult:
    result = payload.get("result") or {}
    het = result.get("heterogeneity") or {}
    overall = result.get("overallEffect") or {}
    rows = _parse_study_rows(payload.get("dataRows"))
    subgroups = tuple(
        PublishedSubgroup(
            label=g.get("label") or "",
            estimate=(g.get("result") or {}).get("mean"),
            se=(g.get("result") or {}).get("se"),
            studies=_parse_study_rows(g.get("dataRows")),
        )
        for g in payload.get("subgroups") or []
    )
    return PublishedResult(
        estimable=bool(result.get("estimable")),
        log_scale=bool(result.get("logScale")),
        estimate=result.get("mean"),
        se=result.get("se"),
        ci_low=result.get("ciStart"),
        ci_high=result.get("ciEnd"),
        q=het.get("chiSquared"),
        df=het.get("degreesOfFreedom"),
        i_squared=het.get("iSquared"),
        q_p=het.get("p"),
        tau_squared=het.get("tauSquared"),
        z=overall.get("z"),
        p_z=overall.get("pZ"),
        t=overall.get("t"),
        p_t=overall.get("pT"),
        studies=rows,
        subgroups=subgroups,
    )


# --------------------------------------------------------------------------
# Live fetch helpers
# --------------------------------------------------------------------------

def list_pairwise_analyses(review_id: str) -> list[dict]:
    return unwrap(get("/reviews/%s/pairwiseAnalyses" % review_id),
                  "PairwiseAnalyses", "Analyses")


def fetch_analysis(review_id: str, analysis_id: str) -> tuple[AnalysisConfig, list[StudyData], PublishedResult]:
    analyses = {str(a.get("id")): a for a in list_pairwise_analyses(review_id)}
    analysis = analyses[str(analysis_id)]
    config = parse_config(analysis)
    rows = get("/reviews/%s/pairwiseAnalyses/%s/pairwiseDataRows" % (review_id, analysis_id))
    studies, _ = parse_data_rows(rows, config, analysis.get("dataSource") or ARM_ONLY)
    published = parse_results(get("/reviews/%s/analyses/%s/results" % (review_id, analysis_id)))
    return config, studies, published
