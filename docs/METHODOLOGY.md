# Methodology

**Agentic AI Equity Research Analyst — computation and scoring methodology**

| | |
|---|---|
| Version | 1.0 |
| Date | 09 September 2026 |
| Companion documents | [PRD.md](../PRD.md), `docs/EVALUATION.md` (Phase 6) |
| Config versions documented here | `weights.yaml` v1.0.0, `thresholds.yaml` v1.0.0 |
| Implementation status | Phase 1 (computation layer) complete and tested. Sections 7–9 specify Phase 2 and are not yet implemented. |

This document exists so that any number the system reports can be traced to a
formula, a convention and a source. Where a choice was available, the choice
is stated and justified rather than left to be inferred from the code — and
where the implementation departs from a published definition, the departure is
recorded explicitly.

---

## Table of Contents

1. [Conventions](#1-conventions)
2. [Normalisation: value to score](#2-normalisation-value-to-score)
3. [The metric library](#3-the-metric-library)
4. [Growth methodology](#4-growth-methodology)
5. [Composite screens](#5-composite-screens)
6. [Missing data and undefined results](#6-missing-data-and-undefined-results)
7. [Valuation (Phase 2)](#7-valuation-phase-2)
8. [The scoring model (Phase 2)](#8-the-scoring-model-phase-2)
9. [Guardrails and veto rules (Phase 2)](#9-guardrails-and-veto-rules-phase-2)
10. [Determinism](#10-determinism)
11. [What the computation layer does not do](#11-what-the-computation-layer-does-not-do)
12. [Change control](#12-change-control)

---

## 1. Conventions

Every convention below is a decision that changes reported numbers. They are
listed first because a ratio without its convention is not a fact.

### 1.1 Units

| Quantity | Unit | Example |
|---|---|---|
| Monetary amounts | **Rupees crore** | `revenue: 10000.0` means ₹10,000 crore |
| Share counts | **Crore shares** | `shares_diluted: 100.0` means 100 crore shares |
| Per-share amounts | **Rupees** | EPS, BVPS, DPS, CMP |
| Percentages | **Held as percentages** | ROE of 32.39% is stored as `32.386...`, not `0.3239` |
| Ratios and multiples | **Multiples** | D/E of `0.40` means 0.40× |
| Day counts | **Days** | DSO of `49.28` means 49.28 days |
| Margin trend | **Percentage points per year** | `0.88` means the margin rises 0.88pp a year |

Percentages are held as percentages so that `thresholds.yaml` can be read and
argued with directly — a curve breakpoint of `15` for ROE means fifteen
percent, which is how an analyst thinks about it. The alternative (fractions
internally, percentages at the display boundary) trades a readable config file
for one more conversion point at which a factor of 100 can be lost.

Because `shares_outstanding` is in crore and monetary amounts are in crore,
`pat / shares_diluted` yields EPS in rupees with no scaling factor.

### 1.2 Signs

Costs, capital expenditure, depreciation, interest expense, tax expense and
dividends paid are stored as **positive magnitudes**. Providers that return
expenses as negative numbers are corrected in the normalisation layer, not in
the ratio engine. Free cash flow, net debt and margin trend may legitimately
be negative and are preserved as such.

### 1.3 Fiscal years

Indian companies ordinarily report to 31 March. A period labelled `FY2026`
is the twelve months **ending** 31 March 2026, and `fiscal_year_end` is held
explicitly on every statement set.

This is recorded rather than inferred because provider labelling of Indian
fiscal years is inconsistent — the same year appears as 2025 or 2026 depending
on the source (PRD risk R11). A one-year misalignment between the income
statement and the balance sheet would corrupt every average-based ratio while
leaving each individual statement looking correct.

### 1.4 Consolidated versus standalone

Consolidated statements are preferred. The basis actually used is recorded on
every period, and a statement set mixing bases across years raises a warning,
because a ratio trend that spans a basis change is not a trend (PRD risk R12).

### 1.5 Average versus closing balance-sheet values

This is the convention most often applied inconsistently, so each ratio states
which it uses.

**Average of opening and closing** — used where a full year's *flow* is
divided by a *stock*, because the flow was earned over the year rather than at
the instant of the closing balance sheet:

> ROE, ROA, asset turnover, fixed asset turnover, inventory days, receivable
> days, payable days, and all three DuPont factors.

**Closing values** — used where the ratio describes the position the company
*ends* the year in:

> ROCE, ROIC, debt-to-equity, net debt/EBITDA, interest coverage, DSCR,
> equity multiplier, current ratio, quick ratio, net working capital.

The difference is material. Acme's FY2026 ROE is 32.39% on the average basis
and 28.50% on the closing basis — 3.9 percentage points, on a company that
grew its equity 32% during the year. That is more than enough to cross a
threshold band and change a pillar score.

Where no prior-year balance sheet exists, the closing value is used and the
fallback is recorded in the metric's `notes`. Dropping the metric entirely
would be worse; leaving the substitution unstated would be worse still.

### 1.6 Day count

`DAYS_IN_YEAR = 365`, throughout. Not 360, not 366 in leap years. Working
capital day-counts move by roughly 1.4% between the 360 and 365 conventions,
which is larger than it sounds when a threshold band boundary is nearby.

### 1.7 Tax rate

The effective tax rate is `tax_expense / PBT`, used where PBT is positive and
the resulting rate falls in [0%, 60%]. Outside that range — typically a
loss-making year, where the ratio is meaningless — the statutory rate of
**25.17%** stands in (22% plus surcharge and cess, for a domestic company
under the concessional regime). Any metric relying on that substitution
records it in its `notes`; at present only ROIC does.

---

## 2. Normalisation: value to score

`app/engine/normalisation.py` is the narrowest place in the system where a
financial fact becomes a judgement. Everything upstream of it is arithmetic;
everything downstream is weighting. Keeping the value-laden step small and
declarative is what makes the scoring model arguable instead of mysterious.

### 2.1 Curve semantics

Each scored metric has a piecewise-linear curve in `thresholds.yaml`:

```yaml
roe:
  unit: PCT
  direction: higher_better
  curve: [[0, 0], [8, 30], [15, 55], [20, 75], [30, 92], [50, 100]]
```

* `x` values are in the metric's own natural unit (here, percent).
* `x` must be strictly increasing; validated at load.
* Scores must lie in [0, 100]; validated at load.
* Between breakpoints, linear interpolation.
* Outside the declared range, **clamped — never extrapolated.** Extrapolation
  is how a 400% ROE becomes a score of 340.

The curve encodes direction. A "lower is better" metric simply has a
decreasing score, so there is no direction flag driving the arithmetic; the
`direction` field is descriptive, used only for report labelling. This
eliminates a class of bug where a flag and a curve disagree.

**Worked example.** Acme's ROE of 32.386% sits in the `(30, 92) → (50, 100)`
segment:

```
band_score = 92 + (32.386 - 30) / (50 - 30) × (100 - 92) = 92.95
```

### 2.2 Peer blending

```
final_score = 0.60 × band_score + 0.40 × peer_percentile
```

A metric is judged both against an absolute financial standard and against
what is actually achievable in its industry: a 12% ROE is mediocre in IT
services and strong in a regulated utility. Weights are in `thresholds.yaml`
under `blend` and validated to sum to 1.0.

Peer percentiles use the **midpoint convention for ties**, and the subject
company's own value must be included in the population, so a company equal to
its single peer ranks at 50 rather than 0 or 100.

With no peer set — which is every Phase 1 run — `final_score = band_score`.
The band score is *not* diluted against a null percentile, because that would
drag every score toward zero and make Phase 1 output incomparable with
Phase 3 output.

### 2.3 Two deliberately non-monotonic curves

Most curves are monotonic. Two are not, and the shape is the point.

**Current ratio** rises to a plateau around 3.0× and then *declines*. A very
high current ratio is not a virtue — beyond a comfortable buffer it usually
means capital idling in receivables or inventory. Scoring it as monotonically
better would reward exactly the working-capital inefficiency that the cash
conversion cycle penalises two metrics later.

**Capex intensity** is hump-shaped, peaking around 3–6% of revenue on the
general curve. Near-zero capex signals underinvestment in the asset base as
readily as capital discipline, and very high capex strains free cash flow. The
optimum is strongly industry-specific, which is why this metric carries a
sector override for five of the six defined sectors.

### 2.4 Sector overrides

`sector_overrides` in `thresholds.yaml` replaces individual curves by sector
key. Only the listed metrics are replaced; everything else falls through to
the base curve. Free-text provider sector strings are mapped to keys by
first-match substring rules in `normalisation.sector_key()`.

Defined sectors: `it_services`, `pharmaceuticals`, `fmcg`, `capital_goods`,
`metals_mining`, `utilities`, and `general` as the fallback.

**Worked example.** Acme's 7.0% capex intensity scores **80.0** on the general
curve and **86.0** on the `capital_goods` curve, because heavy capital
expenditure is normal in that sector. Whenever an override applies, a note is
added to the metric so the report can state which standard the company was
judged against.

Two sector overrides deserve specific mention:

* `it_services.inventory_days` is a flat 50 across all values. A services
  business carries no meaningful inventory, so rewarding or punishing it on
  that metric would be measuring noise.
* `utilities.debt_to_equity` and `metals_mining.debt_to_equity` are more
  permissive than the general curve, because regulated or asset-heavy cash
  flows genuinely support higher leverage.

### 2.5 Pillar assembly and weight redistribution

A pillar score is the weighted average of its component metric scores, with
weights from `weights.yaml`. When a component is unavailable, **its weight is
redistributed proportionally across the components that are available**, and
the redistribution is recorded in the pillar's notes.

The alternative — scoring a missing metric as zero — would punish a company
for a gap in its data provider's coverage. That converts a data problem into
an apparent fundamental weakness, which is precisely the error the
data-completeness reporting exists to prevent.

If more than half a pillar's weight is missing, the pillar returns `None`
rather than a score. Below that point there is not enough left to call it a
measurement.

---

## 3. The metric library

54 metrics, all computed in `app/engine/`, all deterministic. Each carries its
formula string and the exact input values used, which is what lets the report
show its working (`MetricValue.working()`).

### 3.1 Profitability

| Metric | Formula | Basis |
|---|---|---|
| Gross Margin | Gross Profit / Revenue | flow |
| EBITDA Margin | EBITDA / Revenue | flow |
| EBIT (Operating) Margin | EBIT / Revenue | flow |
| Net Profit Margin | PAT / Revenue | flow |
| Effective Tax Rate | Tax Expense / PBT | flow |

### 3.2 Returns

| Metric | Formula | Basis |
|---|---|---|
| Return on Equity | PAT / **Average** Shareholders' Equity | average |
| Return on Assets | PAT / **Average** Total Assets | average |
| Return on Capital Employed | EBIT / (Total Assets − Current Liabilities) | closing |
| Return on Invested Capital | NOPAT / (Total Debt + Equity − Cash) | closing |

`NOPAT = EBIT × (1 − effective tax rate)`, with the statutory-rate fallback
of §1.7.

### 3.3 Leverage and coverage

| Metric | Formula | Basis |
|---|---|---|
| Debt to Equity | Total Debt / Shareholders' Equity | closing |
| Net Debt / EBITDA | (Total Debt − Cash) / EBITDA | closing |
| Interest Coverage | EBIT / Interest Expense | closing |
| Debt Service Coverage Ratio | (EBITDA − Tax) / (Interest + Short-term Debt) | closing |
| Equity Multiplier | Total Assets / Shareholders' Equity | closing |

`Total Debt = short-term debt + long-term debt`.
`Net Debt = Total Debt − cash and equivalents − current investments`; a
negative value indicates a net cash position and is flagged as such.

**DSCR deviation, stated.** The current portion of long-term debt is not
separately reported in most Indian filings, so **short-term borrowings stand
in for it**. For a company that uses short-term debt as working-capital
finance this overstates the denominator, making the reading conservative. The
substitution appears in the metric's notes on every report.

### 3.4 Liquidity

| Metric | Formula |
|---|---|
| Current Ratio | Current Assets / Current Liabilities |
| Quick Ratio | (Current Assets − Inventory) / Current Liabilities |
| Net Working Capital | Current Assets − Current Liabilities |

Net working capital in crore is computed and displayed but **not scored** — the
absolute figure has no sensible universal curve, since it is meaningful only
relative to revenue, which is a separate metric.

### 3.5 Efficiency and working capital

| Metric | Formula | Basis |
|---|---|---|
| Asset Turnover | Revenue / **Average** Total Assets | average |
| Fixed Asset Turnover | Revenue / **Average** Net Fixed Assets | average |
| Inventory Days | (**Average** Inventory / COGS) × 365 | average |
| Receivable Days (DSO) | (**Average** Receivables / Revenue) × 365 | average |
| Payable Days (DPO) | (**Average** Payables / COGS) × 365 | average |
| Cash Conversion Cycle | Inventory Days + Receivable Days − Payable Days | average |
| Net Working Capital / Revenue | Net Working Capital / Revenue | mixed |

A negative cash conversion cycle means the company is financed by its
suppliers, which is a strength; the curve reflects that and the metric notes
say so.

### 3.6 Cash flow and earnings quality

| Metric | Formula |
|---|---|
| Operating Cash Flow | As reported |
| Free Cash Flow | Operating Cash Flow − Capex |
| **OCF / PAT (Cash Conversion)** | Operating Cash Flow / PAT |
| Operating Cash Flow Margin | Operating Cash Flow / Revenue |
| Free Cash Flow Margin | Free Cash Flow / Revenue |
| Capex Intensity | Capex / Revenue |
| Dividend Coverage by FCF | Free Cash Flow / Dividends Paid |

**OCF / PAT is treated as the primary earnings-quality indicator.** Reported
profit is an accounting construct; operating cash flow is closer to observable
fact. A company that persistently reports profit it does not collect in cash
is either building working capital aggressively or recognising revenue it
should not, and in either case its earnings deserve a lower multiple than the
headline suggests. This is why cash flow has its own pillar rather than being
folded into fundamentals, and why a sustained reading below 0.5 triggers veto
rule V3 (§9).

### 3.7 Per-share and market-based

| Metric | Formula | Suppression rule |
|---|---|---|
| EPS (diluted) | PAT / Diluted Shares | — |
| Book Value per Share | Shareholders' Equity / Shares | — |
| Dividend Payout Ratio | DPS / EPS | — |
| P/E (trailing) | CMP / EPS | suppressed when EPS ≤ 0 |
| P/B | CMP / BVPS | suppressed when BVPS ≤ 0 |
| EV / EBITDA | Enterprise Value / EBITDA | suppressed when EBITDA ≤ 0 |
| EV / Sales | Enterprise Value / Revenue | — |
| Price / Free Cash Flow | Market Cap / Free Cash Flow | suppressed when FCF ≤ 0 |
| Dividend Yield | DPS / CMP | — |
| Earnings Yield | EPS / CMP | suppressed when EPS ≤ 0 |
| PEG Ratio | P/E / EPS growth % | suppressed when growth ≤ 0 |

`Enterprise Value = Market Cap + Net Debt`.

**On suppression.** A negative-denominator multiple is not merely unhelpful,
it is actively misleading: dividing a positive market capitalisation by
negative free cash flow yields a negative multiple that a naive threshold
curve scores as *cheap*. Reporting the metric as unavailable, with the reason
stated, is the only correct behaviour. The distressed test fixture exists in
part to hold this behaviour in place.

**On PEG.** Computed against the latest year-on-year EPS growth, not a forward
estimate. A single year of growth is a fragile denominator, which is why PEG
sits in the CONTEXT pillar and carries no scoring weight — it is shown because
analysts expect to see it, not because the model relies on it.

---

## 4. Growth methodology

### 4.1 The CAGR naming problem, and how it is resolved

A "5-year CAGR" is reported by different providers to mean five compounding
intervals (six data points) or a span of five fiscal years (four intervals).
Both conventions are defensible; silently picking one is not.

The scored growth metrics therefore **carry no year suffix** — `revenue_cagr`,
`pat_cagr`, `eps_cagr`, `ebitda_cagr`, `fcf_cagr` — and are computed over the
entire available annual window. Each records `span_years` and `intervals` in
its `inputs_used`, and its label renders the actual endpoints:

> `Revenue CAGR (FY2022 to FY2026, 4y)`

A reader can never be misled about the window a growth rate covers. A
fixed three-interval `revenue_cagr_3y` is also computed, as context only,
where the history allows it.

```
CAGR = (end / begin) ^ (1 / intervals) − 1
```

Minimum three annual periods; below that the metric reports unavailable with
the reason stated.

### 4.2 CAGR guards

CAGR is **undefined** when the base is non-positive, and meaningless when the
sign flips — a swing from a loss to a profit has no compound growth rate. Both
cases return unavailable with a stated reason.

This is the single most common error in automated ratio sheets. Without the
guard, a company that merely stopped losing money reports figures like "PAT
CAGR 220%", and a scoring model reads that as spectacular growth. Six guard
cases are covered by parametrised tests.

### 4.3 Growth consistency

```
consistency = 100 × clamp(1 − σ(YoY growth) / |mean(YoY growth)|, 0, 1)
```

σ is the **population** standard deviation (divisor *n*). Population rather
than sample because the reported years are the complete history under
analysis, not a sample drawn from a larger population of that company's years.
The choice changes the metric and therefore the growth pillar, so it is stated
rather than left to the reader to infer.

A company growing 12%, 13%, 11%, 12% scores far higher than one growing 40%,
−5%, 25%, 0% at the same average. Volatile growth is worth less than steady
growth of the same mean, and this metric is where that belief enters the
model. Undefined when mean growth is near zero, since the coefficient of
variation explodes.

### 4.4 Margin trend

Ordinary-least-squares slope of the EBITDA margin against period index, in
percentage points per year. Missing periods are dropped while the surviving
points keep their original index spacing, so a gap year widens the run rather
than compressing it.

The slope exists because the level alone cannot distinguish two very different
businesses. A 22% margin trending up and a 22% margin trending down score
identically on `ebitda_margin`; only the trend separates them.

---

## 5. Composite screens

Each screen is implemented to its published definition. Every deviation forced
by data availability is recorded in the screen's `caveats` and repeated here.
Reproducing a named academic screen inexactly while still using that name is a
methodological failure, not a rounding difference.

### 5.1 Piotroski F-Score

Nine binary signals, one point each, per Piotroski (2000):

| # | Signal |
|---|---|
| 1 | ROA positive |
| 2 | Operating cash flow positive |
| 3 | ROA improved year on year |
| 4 | Operating cash flow exceeds net income (accrual quality) |
| 5 | Long-term leverage fell |
| 6 | Current ratio improved |
| 7 | No net new equity issued |
| 8 | Gross margin improved |
| 9 | Asset turnover improved |

**Faithful to the paper:** signals 1, 3 and 9 use **beginning-of-year** total
assets, which is why a third year of data is needed to evaluate the prior
year's ROA and turnover.

**Deviation, stated:** signal 5 uses long-term debt over *closing* total
assets rather than the paper's average-assets denominator. The direction of
change is unaffected except in edge cases.

**Two-year floor:** with only two years available, signals 3 and 9 cannot be
evaluated and score zero by construction. The result is then a **lower bound,
not a measurement**, and the caveat says so — otherwise a 7/9 that is really
an unmeasurable 9/9 reads as a mediocre company. Requires at least two years;
below that the score is `None`.

### 5.2 Altman Z-Score

Two variants, selected by data availability, because comparing one against the
other's thresholds is a silent and serious error.

**Original (1968)**, listed non-financial, used when a market capitalisation
is available:

```
Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5
  X1 = Working Capital / Total Assets
  X2 = Retained Earnings / Total Assets
  X3 = EBIT / Total Assets
  X4 = Market Value of Equity / Total Liabilities
  X5 = Revenue / Total Assets
```

Zones: distress < 1.81, grey 1.81–2.99, safe > 2.99.

**Z' (private-firm)**, used when no market price is available:

```
Z' = 0.717·X1 + 0.847·X2 + 3.107·X3 + 0.420·X4' + 0.998·X5
  X4' = Book Equity / Total Liabilities
```

Zones: distress < 1.23, grey 1.23–2.90, safe > 2.90.

**Deviation, stated:** retained earnings are frequently not broken out in
Indian filings, so **reserves and surplus** is used as the X2 proxy. For a
company with substantial non-retained reserves (revaluation, securities
premium) this overstates X2 and therefore the Z-score.

**Not valid for BFSI.** A lender's balance-sheet structure breaks X1 and X4
entirely. Veto V8 excludes banks, NBFCs and insurers before this is reached.

### 5.3 DuPont decomposition

```
ROE = Net Margin × Asset Turnover × Equity Multiplier
```

All three factors are computed on an **average** balance-sheet basis so that
the identity closes exactly against the reported ROE. A unit test asserts the
product equals ROE to 1e-9.

That exactness is worth the trouble. The purpose of DuPont is to say *why* ROE
is what it is; a decomposition whose product does not equal the thing being
decomposed cannot support that claim. Note that this requires the
average-based equity multiplier (average assets / average equity), which
differs from the standalone closing-value `equity_multiplier` metric — both
are reported, under distinct names.

The screen then names the dominant driver, which is the sentence the narrative
agent reuses. Two companies at 25% ROE, one at a 1.2× and one at a 4.0×
equity multiplier, are not comparable businesses, and the decomposition is
what makes that visible.

### 5.4 Cash conversion streak

Consecutive most-recent years with OCF/PAT below 0.5. Supplies veto rule V3.

A single weak year is ordinary — a working capital build, a one-off. Three in a
row is a pattern, and the pattern is what the veto responds to. Years with
non-positive PAT break the streak and are not counted as failures, since the
ratio is not meaningful there.

---

## 6. Missing data and undefined results

One rule, applied at every level: **`None` propagates; it never becomes zero.**

`safe_div` returns `None` for a zero or missing denominator. A missing input
yields a missing metric with a stated `unavailable_reason`. A missing metric
redistributes its pillar weight rather than contributing zero. Substituting
zero for "we don't know" is how a data gap becomes a confidently wrong
recommendation, and the guard is placed as low in the stack as possible.

Consequences visible in output:

* Every unavailable metric states why it is unavailable.
* Every available metric carries a formula and its inputs.
* Balance-sheet validation (`Assets = Liabilities + Equity`, 0.1% tolerance)
  raises a warning rather than passing quietly — a sheet that does not balance
  means the normalisation mapped a field wrongly, and every ratio built on that
  period is suspect.
* Data completeness is reported as a headline figure, and below 70% the system
  declines to issue a rating at all (veto V1).

---

## 7. Valuation (Phase 2)

*Specification. Not yet implemented.*

### 7.1 DCF (FCFF, two-stage)

```
WACC = (E/V × Re) + (D/V × Rd × (1 − t))
  Re = Rf + β × ERP
  Rd = Interest Expense / Average Total Debt
  t  = effective tax rate, 3-year average, capped at statutory

FCFF_t = EBIT_t × (1 − t) + D&A_t − Capex_t − ΔNWC_t

EV = Σ [FCFF_t / (1 + WACC)^t] + TV / (1 + WACC)^n
  TV = FCFF_n × (1 + g) / (WACC − g)

Equity Value = EV − Net Debt
Fair Value per Share = Equity Value / Diluted Shares
```

**Assumption guardrails, hard-coded and enforced in Python:**

| Assumption | Constraint | Source |
|---|---|---|
| Risk-free rate | live 10-year G-Sec yield | RBI / allowlisted, cited |
| Equity risk premium | 6.0%–8.5% | Damodaran India ERP, cited |
| Beta | 5-year monthly regression vs NIFTY 50, floor 0.5, cap 2.0 | computed |
| Terminal growth `g` | 3.0%–5.5%, **and** ≤ WACC − 1.5% | configured |
| Forecast horizon | 5 or 10 years | configured |

A language model may **propose and justify** the revenue and margin path,
grounded in the industry analysis and historical trend. Python **validates**
it against the guardrails and performs every arithmetic step. Breaching a
guardrail flags the DCF `LOW_CONFIDENCE` and halves its reconciliation weight.

A mandatory WACC × terminal-growth sensitivity grid is produced, and fair value
is always reported as a **range**. A single-point DCF presented as precision is
treated as a defect, not a simplification.

### 7.2 Relative valuation

For each of P/E, P/B, EV/EBITDA, EV/Sales and P/FCF: the company's multiple,
the peer median and interquartile range, the company's own five-year median,
its percentile rank in the peer set, and the implied value on both the
peer-median and own-history multiples.

**Premium/discount decomposition.** Where the company trades at a premium, the
system tests whether the premium is *justified* by comparing its percentile
rank on quality metrics (ROE, margin, growth, FCF conversion) against its
percentile rank on valuation multiples. 90th percentile on quality and 90th on
valuation is consistently priced; 40th on quality and 90th on valuation is a
valuation-risk flag.

### 7.3 Reconciliation

| Method | Weight |
|---|---|
| DCF | 50% |
| Peer-relative | 30% |
| Own five-year history | 20% |

Weights live in `weights.yaml` under `valuation_methods`. If the DCF is
`LOW_CONFIDENCE` its weight halves and the remainder is redistributed
proportionally.

### 7.4 Sector exclusion

Banks, NBFCs and insurers are excluded in V1. FCFF-based DCF is invalid where
debt is raw material rather than financing, and the metric set that matters
for them (NIM, GNPA/NNPA, CASA, CAR, provision coverage, credit cost) is not
implemented. `normalisation.is_bfsi()` detects the sector so the system
refuses to rate rather than producing a confidently invalid valuation.

---

## 8. The scoring model (Phase 2)

*Specification. Not yet implemented. The Phase 1 CLI deliberately reports only
the three pillars it can compute, and does not synthesise a composite from
part of the model.*

### 8.1 Pillar weights

| Pillar | Weight | Measures |
|---|---|---|
| Fundamentals | 30% | Profitability, returns, balance-sheet strength |
| Valuation | 20% | Price against intrinsic value, peers, own history |
| Growth | 15% | Rate, consistency and quality of growth |
| Cash Flow | 10% | Cash generation and earnings quality |
| Industry | 10% | Industry outlook, structural position, regulation |
| News & Events | 10% | Materiality-weighted, recency-decayed news |
| Risk | 5% | Financial, business, valuation, governance (inverted) |

**Justification.** Over a multi-quarter horizon business quality dominates
returns, so fundamentals carry the most weight. Valuation is second because
entry price determines realised return even for a good business. News is
capped at 10% so that short-term sentiment cannot hijack a fundamental
process. Risk carries a small *direct* weight because its real function is to
**veto** (§9) rather than to shave points off an average — a serious risk
should cap the rating, not cost it two points.

### 8.2 Composite

```
Composite = 0.30 × Fundamentals
          + 0.20 × Valuation
          + 0.15 × Growth
          + 0.10 × CashFlow
          + 0.10 × Industry
          + 0.10 × News
          + 0.05 × (100 − Risk)
```

### 8.3 Rating bands

| Composite | Rating |
|---|---|
| ≥ 78 | BUY |
| 65 – 78 | ACCUMULATE |
| 45 – 65 | HOLD |
| 30 – 45 | REDUCE |
| < 30 | SELL |
| — | NO RATING (veto V1) |

Bands are half-open intervals validated at load to tile [0, 101) with no gap
and no overlap. A gap would leave a score with no rating; an overlap would
make the rating depend on dictionary iteration order, which is exactly the
non-determinism the design exists to prevent.

### 8.4 News scoring

The one part of the model where LLM judgement enters the arithmetic, and it is
worth being precise about how narrowly:

```
For each article i:
  direction_i   = +1 | −1 | 0                    ← LLM (bounded enum)
  materiality_i = 1..5                           ← LLM (bounded integer)
  confidence_i  = 0.0..1.0                       ← LLM (bounded float)
  credibility_i = 1.00 | 0.60 | 0.25             ← source tier, from config
  recency_i     = exp(−age_days / 30)            ← Python

  weight_i       = materiality_i × credibility_i × confidence_i × recency_i
  contribution_i = direction_i × weight_i

news_raw   = Σ contribution_i / Σ weight_i       → [−1, +1]
news_score = 50 + news_raw × 50                  → [0, 100]

If Σ weight_i < 2.0:  news_score = 50, flagged "insufficient coverage"
```

The model supplies three bounded fields per article. Python performs every
arithmetic step. A 60-day-old item carries roughly 13% of a same-day item's
weight.

Note what is *not* used: the news provider's own `overall_sentiment_score`. It
is retrieved and stored solely as a comparison baseline for the evaluation
harness. A single sentiment scalar loses causality, materiality, impact area
and time horizon — the four things an analyst actually needs.

---

## 9. Guardrails and veto rules (Phase 2)

Applied **after** the composite is computed. These exist because a favourable
average can hide a condition that makes a company uninvestable.

| # | Condition | Action |
|---|---|---|
| V1 | Data completeness < 70% | **NO RATING** |
| V2 | Net Debt/EBITDA > 5.0 **and** Interest Coverage < 1.5 | cap at REDUCE |
| V3 | OCF/PAT < 0.5 for 3 consecutive years | cap at HOLD + earnings-quality flag |
| V4 | Altman Z < 1.81 (distress zone) | cap at REDUCE |
| V5 | Negative shareholders' equity | SELL or NO RATING |
| V6 | Evidenced HIGH-severity governance flag | cap at HOLD |
| V7 | Downside to fair value > 40% | cap at HOLD |
| V8 | BFSI sector | NOT SUPPORTED |
| V9 | Verification failed twice | retain rating, downgrade confidence |
| V10 | Fewer than 3 valid peers | halve valuation pillar weight |

Every triggered veto is recorded in the audit trail with the values that
triggered it, alongside the rating that would have applied without it.

The `leveraged_cyclicals` test fixture was constructed to sit exactly on the
V2, V3 and V4 boundaries — net debt/EBITDA of 5.76×, interest coverage of
1.25×, a three-year conversion streak (with the fourth year above the line),
and an Altman Z of 1.14 — so that these rules have something to bite on before
the Phase 2 engine exists.

---

## 10. Determinism

The system's central defensibility claim is that identical inputs produce an
identical score. For the computation layer that claim is **absolute**, and it
is tested as such: `MetricSet.fingerprint()` is a SHA-256 over every computed
value and score, and the suite asserts bit-identical equality (`==`, not
`approx`) across repeated runs, across JSON serialisation, and across
insertion-order changes.

Four things make it hold:

1. Every arithmetic operation is pure Python — no numpy or pandas, so no
   BLAS-dependent reduction whose summation order can vary between builds.
2. No language model computes any number.
3. The engine may not import an agent, an LLM client, the network, or a source
   of randomness. `tests/unit/test_layering.py` parses the engine's AST and
   fails if it ever does. It is a cheap test protecting an expensive claim:
   once the engine can call a model, "the recommendation is computed, not
   generated" stops being true.
4. LLM judgements, where they exist at all, are constrained to bounded
   discrete fields (materiality 1–5, direction as an enum), so their influence
   is capped and quantised.

**The honest limitation.** LLM inference is not perfectly deterministic even
at temperature 0. On a *fresh* run the industry and news pillars may therefore
vary slightly. Two things bound the effect: the bounded discrete fields limit
any single judgement to at most one band, and those two pillars carry 20% of
total weight between them. Fundamentals, Valuation, Growth and Cash Flow —
75% of the weight — carry **zero** variance.

Evaluation Track 4 measures this rather than asserting it away. Reporting the
observed standard deviation of the composite across repeated fresh runs is
itself a finding worth having, and a more credible claim than "the system is
deterministic" stated flatly.

---

## 11. What the computation layer does not do

Stated explicitly, because the boundary is the design:

* It does not produce a composite score or a BUY/HOLD/SELL rating. Three of
  seven pillars are computable from statements alone; a composite built on
  those three, silently reweighted, would be a different model wearing the
  same name.
* It does not value a company. DCF and relative valuation are Phase 2.
* It does not read prose, assess news, or research an industry.
* It does not call a language model, reach the network, or read an API key.
* It does not fetch data. Phase 1 reads normalised statements; the provider
  layer is Phase 1b.
* It does not clean or guess. A golden file that does not parse fails loudly.

What it does do is produce, from a statement set, 54 audited metrics and four
composite screens, each traceable to a formula and its inputs, reproducibly.
Everything above it in the stack is built on that, which is why it was built
first (PRD §22.2).

---

## 12. Change control

`weights.yaml` and `thresholds.yaml` **are** the scoring model. Changing a
curve breakpoint changes every score the system produces.

Accordingly:

* Both files declare a `version`, and both versions are stamped onto every
  report and onto every `MetricSet`.
* Both are validated at load: weights sum to 1.0 per pillar and per component
  map, curves are strictly increasing with scores in [0, 100], rating bands
  tile the range without gaps, sector overrides may only target metrics that
  exist, and a missing file is an error rather than a silent fallback to
  built-in defaults — which would mean a report stamped with a configuration
  version that was never applied.
* Every component weight in the Phase 1 pillars is tested to have a
  corresponding threshold curve, so a typo cannot silently reweight a pillar.
* Material changes to conventions, formulas, weights or thresholds are
  recorded in this document and in the PRD change log.

---

## Appendix A — Test coverage of this methodology

| Methodology section | Test file |
|---|---|
| §1 conventions, derivations, balance validation | `tests/unit/test_statements.py` |
| §2 normalisation, sector overrides, pillar assembly | `tests/unit/test_normalisation.py` |
| §3 metric library, golden values, suppression rules | `tests/unit/test_ratios_golden.py` |
| §4 growth, CAGR guards, consistency, margin trend | `tests/unit/test_growth.py` |
| §5 composite screens and their deviations | `tests/unit/test_screens.py` |
| §6 missing-data policy | `tests/unit/test_helpers.py` |
| §10 determinism | `tests/unit/test_determinism.py` |
| §10 layer boundary | `tests/unit/test_layering.py` |
| §12 config validation | `tests/unit/test_config.py` |
| Golden-file harness (Track 1) | `tests/unit/test_golden_files.py` |

Current status: **176 tests passing, 2 skipped by design** — the
`InvestmentDecision` schema test awaits Phase 2, and the real-company
golden-value test awaits its first hand-entered annual report (see
`data/golden/TEMPLATE.json`).

## Appendix B — References

**Finance**

* Damodaran, A. — *Investment Valuation*; India equity risk premium tables
* Penman, S. — *Financial Statement Analysis and Security Valuation*
* Piotroski, J. (2000) — "Value Investing: The Use of Historical Financial
  Statement Information to Separate Winners from Losers"
* Altman, E. (1968) — "Financial Ratios, Discriminant Analysis and the
  Prediction of Corporate Bankruptcy"
* CFA Institute — Equity Valuation curriculum readings

**Regulatory**

* SEBI (Research Analysts) Regulations, 2014
* SEBI (Investment Advisers) Regulations, 2013

See PRD §20 for how these bear on the project's operating constraints.
