"""Retraction Impact Engine: meta-analysis recomputation primitives.

The engine exists to answer one question: when retracted studies are removed
from a published synthesis, does its conclusion change? Because a wrong answer
in this domain is worse than no answer, every published finding must first
reproduce the review's own numbers (see rie.gate).
"""
from .effects import study_effect, study_effects
from .pooling import InsufficientData, pool, pool_effects
from .types import (
    AnalysisConfig,
    CiLevel,
    CiMethod,
    Continuous,
    Dichotomous,
    EffectMeasure,
    ExclusionReason,
    Generic,
    Heterogeneity,
    Method,
    Model,
    OEVariance,
    PooledResult,
    StudyEffect,
    TauEstimator,
)

__all__ = [
    "AnalysisConfig", "CiLevel", "CiMethod", "Continuous", "Dichotomous",
    "EffectMeasure", "ExclusionReason", "Generic", "Heterogeneity",
    "InsufficientData", "Method", "Model", "OEVariance", "PooledResult",
    "StudyEffect", "TauEstimator", "pool", "pool_effects", "study_effect",
    "study_effects",
]
