# How the confidence score is calculated

The demo calculates confidence from evidence that the code can check, not from the
model's own rating. In our evaluation Gemma rated wrong answers at 75–95% confidence,
so a model's self-reported confidence cannot be trusted. Code: `scoring.py`.

## Three steps

1. **The model names its evidence.** It lists the ids it relied on: learned rules
   (`r…`), past orders and notes (`h…`) and installed equipment (`L…`), plus
   `missing_specs` and other possible readings. It gives no percentage.
2. **The code checks the evidence.** Every cited id must exist in the data; made-up
   ids are ignored and shown on screen. A rule's state comes from its metadata
   (`status`, `last_confirmed`). A past order counts only if its text mentions the
   chosen drive.
3. **A fixed formula adds up the points**, and the result is cut to 0–100.

## Points

| Evidence | Points | Checked in code |
|---|---|---|
| Learned rule, active | +40 | Cited rule exists, is approved and was confirmed within the last 365 days |
| Learned rule, fading | +20 | Last confirmed more than 365 days ago |
| Learned rule, flagged | 0 | Its owner must review it first |
| Past order backs the drive | +30 | At least one cited history entry mentions the chosen or current drive |
| Nothing needed is missing | +20 | The model listed no missing specs |
| Each missing spec | −15 | At most 2 counted |
| Each other possible reading | −15 | At most 2 counted |

Only the strongest cited rule counts.

    score = min(100, max(0, R + H + C − 15 × (missing + alternatives)))

## Bands

| Score | Band | What the specialist does |
|---|---|---|
| 80–100 | Proceed (green) | Act on the reading |
| 50–79 | Check with customer (orange) | Confirm by phone first |
| 0–49 | Questionable (red) | Do not act; clarify the request |

Without a learned rule the score can reach at most 50 (30 + 20), so a reading the
specialists have not taught the system is never marked Proceed.

## Worked example: request 7

"The bakery line drive is acting up again, we need a spare."

| | Without Anna's rule | With Anna's rule |
|---|---|---|
| Reading | Line 1, PD-FC-110 (wrong) | Line 4, PD-FC-040 (right) |
| Learned rule | 0 | +40 (r002) |
| Past order | +30 (h4) | +30 (h10) |
| Nothing missing | +20 | +20 |
| Other possible reading | −15 | — |
| **Score** | **35% Questionable** | **90% Proceed** |

The "with" column is a real run of `qwen3.8-27b-mlx` (25 Nov 2026). The "without"
column is a test case used to check the code, not a model run.

## Limitations

- The weights are hand-set starting values, not calibrated. Change them at the top of `scoring.py`.
- The code checks that a cited rule exists, not that it really applies to the request.
- Next step: compare the scores with experienced specialists' judgements.
