"""Locate the pairwise-analysis config enums in the spec and check for stub schemas."""
import json

raw = open("swagger.json", encoding="utf-8").read()
d = json.loads(raw)

print("PairwiseAnalysis raw:", json.dumps(d["components"]["schemas"]["PairwiseAnalysis"])[:1500])
print()
tokens = [
    "EXP_O_E_VAR", "PETO_OR", "RATE_RATIO", "HKSJ", "REML", "DerSimonian",
    "heterogeneityEstimator", "effectMeasure", "ciMethod", "method", "model",
    "swapEventsNonEvents", "totals", "continuityCorrection",
]
for t in tokens:
    print("%-24s %d" % (t, raw.count(t)))
print()
stubs = [n for n, s in d["components"]["schemas"].items() if not (s.get("properties") or s.get("allOf") or s.get("enum"))]
print("stub schemas (no properties):", stubs)
