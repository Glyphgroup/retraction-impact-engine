# Retraction Impact Engine

When a trial is retracted, everyone can tell you the trial is retracted. Nobody
tells you what happened to the conclusions built on top of it.

This project walks the chain — retracted trial, meta-analysis, systematic review,
guideline — and answers one question: **given retraction R, which downstream
syntheses now have a materially different answer, and by how much?**

The output we are trying to produce, per finding, looks like this:

> Guideline X recommends treatment Y. Its supporting meta-analysis pooled 14
> trials. Three were retracted for data falsification. Recomputed without them,
> the pooled OR moves from 0.71 to 0.94 and the confidence interval crosses the
> null.

Every such statement is independently checkable arithmetic. Thousands of them
sit unwritten.

## Why the problem is real

- Removing retracted studies from meta-analyses that included them changed the
  effect estimate by at least 10% in 35% of cases, and by at least 50% in 14%
  ([JAMA Internal Medicine, 2025](https://jamanetwork.com/journals/jamainternalmedicine/fullarticle/2831911)).
- Of 68 publications affected by one cluster of retracted trials, exactly one
  had been reassessed. The authors describe correction as slow, uncoordinated
  and inconsistent ([BMJ Open](https://bmjopen.bmj.com/content/9/10/e031909)).
- Retracted RCTs continue to be cited, and left uncorrected, in systematic
  reviews and clinical practice guidelines
  ([Kataoka et al., J Clin Epidemiol 2022](https://pubmed.ncbi.nlm.nih.gov/35779825/)).
- What happens to papers citing retracted work is the overlooked part of the
  problem ([Nature, 2024](https://www.nature.com/articles/d41586-024-02747-1)).

## The design decision that matters: reproduce, then perturb

LLM extraction of full meta-analytic tuples is unreliable, and pooling amplifies
small upstream errors. A naive extract-and-recompute pipeline would therefore
produce confident nonsense, which is unacceptable in a medical domain.

So the pipeline is gated:

1. Extract study-level data from the published review.
2. Recompute the review's **own published pooled estimate**.
3. If our number does not match theirs within tolerance, **emit nothing**.
   Extraction failed. Stay silent.
4. Only if it reproduces, remove the retracted studies and recompute the delta.

The gate makes the failure mode silence rather than confident error. It also
means the published quantity is a *difference* between two estimates computed
identically from the same extraction, so systematic extraction error largely
cancels. Silence on hard cases is a feature. The goal is zero false alarms, not
coverage.

**No finding is ever emitted that did not pass the gate.**

## Status

Working:

- **Statistical engine.** Mantel-Haenszel (OR with Robins-Breslow-Greenland
  variance, RR, RD), Peto and O-E-and-variance, inverse-variance fixed and
  random effects, DerSimonian-Laird and REML between-study variance, Wald and
  Hartung-Knapp-Sidik-Jonkman intervals, Q, I², and RevMan's zero-cell and
  study-exclusion rules.
- **Reproduce-gate** with an explicit, documented tolerance policy.
- **Retraction Watch ingest** via the free Crossref Labs export: 71,362 records,
  65,943 actual retractions, indexed by original-paper DOI and PubMed ID.

Validation. The engine reproduces RevMan's own pooled results for **14 of 16
analyses** in a real Cochrane review, to double-precision agreement (differences
at the 1e-16 level, against a gate tolerance of 1e-2), spanning
inverse-variance, Mantel-Haenszel and Peto, fixed and random effects, and odds
ratio, mean difference and Peto odds ratio. The two exceptions are genuine: both
are subgrouped by a covariate, and RevMan drops a study with no value for that
covariate from the total. Our estimate moves by more than tolerance and the gate
correctly refuses to emit anything. That is the gate doing its job.

Independent cross-checks, since R and metafor are not installed here: statsmodels
for Mantel-Haenszel OR with RBG variance, inverse-variance fixed, DerSimonian-
Laird random, Q, I² and HKSJ; numerical maximisation of the restricted
log-likelihood for REML; closed-form algebra for Mantel-Haenszel RR and RD and
for Peto. One finding worth flagging: statsmodels does not floor DerSimonian-
Laird τ² at zero, and returns negative weights and a NaN standard error on
underdispersed data. That is pinned as a test.

Not built yet: the OpenAlex citation walk, extraction from open-access full text
in PubMed Central, and the ranking of findings by how far a conclusion moved.

## Deliberately not rebuilt

Retraction status lookup is solved (Retraction Watch, Crossref, Zotero).
Paper-mill and fabricated-text detection is solved and commercial. Single-paper
statistical error detection is solved (statcheck, GRIM, and others). Leave-one-out
sensitivity analysis is a solved calculator (metafor, MetaSubtract). None of
those walk the citation chain to the downstream conclusion, which is the gap
this project addresses.

## Data sources

- **Retraction Watch** via the Crossref Labs export — the source of truth for
  retraction status.
- **OpenAlex** for the citation graph. Its `is_retracted` field is *not* used;
  it has documented misclassifications
  ([arXiv:2403.13339](https://arxiv.org/abs/2403.13339)).
- **Cochrane RevMan API** for structured study-level data, where accessible.
- **Europe PMC / PubMed** for open-access full text.

Nothing in this repository redistributes third-party data. The Retraction Watch
export, Cochrane API responses and the Cochrane OpenAPI description are fetched
locally and git-ignored. The only committed data is a reduced numeric fixture in
`tests/data/`, holding study counts and pooled estimates for regression testing.

## Running it

```bash
pip install -e ".[dev]"
python -m pytest                      # engine and gate validation
python tools/ingest_retractions.py    # download and index Retraction Watch
python tools/bcg_summary.py           # every pooling path on a known dataset
```

The Cochrane tools need a bearer token from an authenticated RevMan Web session
in `COCHRANE_TOKEN`. Tokens are short-lived and are never written to disk.

```bash
python tools/run_gate_cochrane.py     # gate against cached Cochrane responses
```

## Honest caveats

Coverage will be low, and many reviews will not reproduce. A few hundred verified
findings would be a landmark rather than a failure.

Cochrane announced in June 2026 that it is strengthening its handling of
retractions. That validates the problem. It covers Cochrane's own reviews, and
the policy is to exclude retracted studies going forward, not to recompute the
downstream guideline graph. The contaminated universe is overwhelmingly
non-Cochrane.

This is probably not a business. It is more likely infrastructure: a cited public
resource that changes a few guidelines.

Before any finding is published, a professional epidemiologist or systematic
reviewer reviews it, and the authors of the affected review are notified. We can
prove arithmetic. We cannot certify that a delta is clinically meaningful.
