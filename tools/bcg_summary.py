"""Print every pooling path on the BCG dataset, for eyeballing against RevMan/metafor."""
from rie import (
    AnalysisConfig, CiMethod, EffectMeasure, Method, Model, TauEstimator, pool,
)
from tests import fixtures

studies = fixtures.bcg()

CASES = [
    ("MH  OR   fixed", EffectMeasure.OR, Method.MH, Model.FIXED, TauEstimator.DL, CiMethod.WALD),
    ("MH  RR   fixed", EffectMeasure.RR, Method.MH, Model.FIXED, TauEstimator.DL, CiMethod.WALD),
    ("MH  RD   fixed", EffectMeasure.RD, Method.MH, Model.FIXED, TauEstimator.DL, CiMethod.WALD),
    ("Peto OR  fixed", EffectMeasure.PETO_OR, Method.PETO, Model.FIXED, TauEstimator.DL, CiMethod.WALD),
    ("IV  RR   fixed", EffectMeasure.RR, Method.IV, Model.FIXED, TauEstimator.DL, CiMethod.WALD),
    ("IV  RR   random DL", EffectMeasure.RR, Method.IV, Model.RANDOM, TauEstimator.DL, CiMethod.WALD),
    ("IV  RR   random REML", EffectMeasure.RR, Method.IV, Model.RANDOM, TauEstimator.REML, CiMethod.WALD),
    ("IV  RR   random DL+HKSJ", EffectMeasure.RR, Method.IV, Model.RANDOM, TauEstimator.DL, CiMethod.HKSJ),
    ("MH  OR   random DL", EffectMeasure.OR, Method.MH, Model.RANDOM, TauEstimator.DL, CiMethod.WALD),
    ("IV  OR   random DL", EffectMeasure.OR, Method.IV, Model.RANDOM, TauEstimator.DL, CiMethod.WALD),
]

hdr = "%-24s %8s  %-18s %7s %5s %7s %8s %6s" % (
    "analysis", "effect", "95% CI", "Q", "df", "I2 %", "tau2", "k")
print(hdr)
print("-" * len(hdr))
for label, measure, method, model, tau, ci in CASES:
    cfg = AnalysisConfig(effect_measure=measure, method=method, model=model,
                         tau_estimator=tau, ci_method=ci)
    r = pool(studies, cfg)
    lo, hi = r.ci
    h = r.heterogeneity
    print("%-24s %8.4f  [%7.4f, %7.4f] %7.2f %5d %7.1f %8.4f %6d" % (
        label, r.effect, lo, hi, h.q, h.df, h.i_squared, h.tau_squared, r.k))

print()
print("Note: MH-OR-random and IV-OR-random differ only in the centre used for Q.")
mh = pool(studies, AnalysisConfig(effect_measure=EffectMeasure.OR, method=Method.MH,
                                 model=Model.RANDOM))
iv = pool(studies, AnalysisConfig(effect_measure=EffectMeasure.OR, method=Method.IV,
                                  model=Model.RANDOM))
print("  Q_MH = %.4f   Q_IV = %.4f   tau2_MH = %.5f   tau2_IV = %.5f"
      % (mh.heterogeneity.q, iv.heterogeneity.q,
         mh.heterogeneity.tau_squared, iv.heterogeneity.tau_squared))
