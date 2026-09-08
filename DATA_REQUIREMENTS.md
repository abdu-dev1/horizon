# Horizon — Data Requirements

**Renewal Forecasting model · HPS Level-Funded & Self-Funded**
Prepared for: Liz Moser & Becca Amrani · Owner: Abdumalik Dalerzoda

---

## Where we are today

A working model is already trained and scoring renewals, built from the existing
AM Renewal Tracker, the 2025 Non-Renewal workbook, the 2026 Retention Analysis, and the
Executive Logs. On our own historical decisions it predicts renewal vs. termination with
**~0.84 AUC** (rich records) — strong for a first build.

The model is only as good as the data feeding it. Below is what would take it from a
promising pilot to a production forecast covering the full book. Items are ordered by
impact; **#1 is the single biggest unlock.**

---

## Priority 1 — In-Force Census *(the critical one)*

**What:** one row for every currently active group, refreshed monthly.

**Why it matters:** today the dashboard can only see renewals that someone has already
built an Executive Log for — about 14 groups. It is blind to the ~50+ other in-force
groups whose renewals fall in the next two quarters. A census makes the forecast cover
the *entire* forward book instead of just the blocks being actively worked, and lets a
group be scored 6 months out as an early warning rather than 3 weeks before it lapses.

**Columns needed:**

| Field | Example |
|---|---|
| Group name | Acme Manufacturing LLC |
| Group ID (if one exists) | CP1042 |
| Product | HPS Level Funded / Self Funded |
| Renewal (effective) date | 2026-10-01 |
| Current enrolled lives | 84 |
| Current annual premium | 612,000 |
| Broker | USI – Cincinnati |
| RSD / AM | Chris / Alyssa |
| Carrier / TPA | Zurich / Personify |

---

## Priority 2 — Underwriting Renewal Extract

**What:** the renewal economics for each upcoming group, keyed by group + renewal date —
ideally pulled directly from the UW source rather than re-keyed into a workbook.

**Why it matters:** the model's #1 predictor is the **renewal rate increase**, and #2 is
**claims-to-attachment**. These are what turn a rough early-warning score into a sharp one.
The earlier they reach the model, the earlier a high-risk renewal surfaces on the save list.

**Columns needed:** net loss ratio (w/ rebates), mature claims to attachment, initial UW
increase %, final increase % (with lasers), # of lasers (current + renewal), laser
liability $, # of rate revisions sent to broker.

---

## Priority 3 — Deeper History & Relationship Fields

These sharpen factors we can only partially compute today.

1. **Pre-2023 renewal decisions** — our history starts mid-2023, so "tenure with Crumdale"
   is floored and understated. Older outcomes would deepen tenure and prior-renewal
   behavior (a group's own track record is a strong predictor).

2. **`Years w/ CS` and `BOR Change?` for 2023–2025** — these fields exist cleanly only on
   the 2026 sheet. Backfilling them for prior years would let the model properly weight
   broker-of-record changes, a factor flagged in the original kickoff.

3. **Broker quality / preferred-broker rating** — Kevin's email lists "good vs. bad broker"
   as a factor, but no such field exists. If a broker rating exists internally, we'd use it
   directly; otherwise we can derive each broker's historical retention rate from the data.

4. **Service signal for all groups** — ZD ticket counts / escalations are currently only
   recorded for terminated groups. Capturing them for *every* group (renewed and termed)
   would unlock service friction as a predictor without bias.

---

## Format & delivery

- **Format:** CSV or Excel is fine — column names don't need to match exactly, we map them.
- **Keys:** a consistent group identifier (or exact group name) is what lets us join sources.
  A stable Group ID across systems would eliminate most matching headaches.
- **Cadence:** monthly refresh of the census + UW extract keeps the forecast current; the
  model retrains on the new data in minutes.
- **One rule that matters:** every field should reflect what was known **before** the
  renewal decision (e.g. loss ratio as of 90 days out, not the final settled number).
  This is what keeps the model honest on future renewals.

---

## What each unlock delivers

| Provide… | …and the model gains |
|---|---|
| In-force census | Full 2-quarter book (~65+ groups vs. 14), early-warning scores |
| UW renewal extract | Sharp, high-confidence scores on the strongest predictors |
| Pre-2023 history + BOR/tenure backfill | Proper weighting of loyalty & broker-change risk |
| Broker rating + service tickets | Two factors from the original brief, currently missing |
