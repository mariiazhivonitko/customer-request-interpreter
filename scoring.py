"""Evidence-based confidence.

The language model does NOT rate its own confidence (a small model stays confident
even when it is wrong). Instead it names the evidence it used, this module checks
that evidence against the data, and a simple formula turns it into a score:

    learned rule backs the reading   +40 (active)  +20 (fading)  0 (flagged)
    a past order backs the part      +30
    nothing needed is missing        +20
    each missing spec                -15
    each other possible reading      -15

Without a learned rule the score can never go above 50, so a reading the
specialists have not taught the system is never marked "Proceed".
"""
import re
from datetime import date

W_RULE = {"active": 40, "fading": 20, "flagged": 0}
W_HISTORY = 30
W_COMPLETE = 20
W_GAP = -15
FADE_DAYS = 365  # a rule not confirmed for this long starts to fade

# (lowest score, label, streamlit colour)
BANDS = [(80, "Proceed", "green"), (50, "Check with customer", "orange"), (0, "Questionable", "red")]
LEGEND = "0–50 questionable · 50–80 check with the customer · 80–100 proceed"


def rule_state(rule: dict, today=None) -> str:
    """active, fading, flagged or inactive."""
    status = rule.get("status")
    if status == "flagged":
        return "flagged"
    if status not in ("approved", "active"):
        return "inactive"
    today = today or date.today()
    last = rule.get("last_confirmed") or rule.get("created") or today.isoformat()
    try:
        age = (today - date.fromisoformat(last)).days
    except ValueError:
        age = 0
    return "fading" if age > FADE_DAYS else "active"


def band(value: int):
    for low, label, colour in BANDS:
        if value >= low:
            return label, colour
    return BANDS[-1][1], BANDS[-1][2]


def cited_ids(evidence: list) -> list:
    ids = re.findall(r"\b([hLr]\d+)\b", " ".join(str(e) for e in evidence))
    return list(dict.fromkeys(ids))


def score(interp: dict, alternatives: list, missing: list, customer: dict, all_rules: list) -> dict:
    """Returns {value, band, colour, reasons: [(points, text)], rules_used, ignored}."""
    ids = cited_ids(interp.get("evidence", []))
    rules_by_id = {r["rule_id"]: r for r in all_rules}
    history = {h["id"]: h for h in customer["history"]}
    base_ids = {i["id"] for i in customer["installed_base"]}

    reasons, ignored, rules_used = [], [], []
    total = 0

    # 1. learned rules (the best one counts)
    best = None
    for i in ids:
        if not i.startswith("r"):
            continue
        rule = rules_by_id.get(i)
        if rule is None:
            ignored.append(i)
            continue
        state = rule_state(rule)
        if state == "inactive":
            continue
        rules_used.append(i)
        pts = W_RULE.get(state, 0)
        if best is None or pts > best[0]:
            best = (pts, i, state)
    if best:
        total += best[0]
        note = {"active": "", "fading": " (fading: not confirmed for over a year)",
                "flagged": " (flagged: check with the customer)"}[best[2]]
        reasons.append((best[0], f"Learned rule {best[1]} supports this reading{note}"))
    else:
        reasons.append((0, "No learned rule supports this reading"))

    # 2. past orders that mention the part
    parts = {p for p in (interp.get("part"), interp.get("current_part")) if p}
    matches = []
    for i in ids:
        if i.startswith("h"):
            h = history.get(i)
            if h is None:
                ignored.append(i)
            elif any(p in h["text"] for p in parts):
                matches.append(i)
        elif i.startswith("L") and i not in base_ids:
            ignored.append(i)
    if matches:
        total += W_HISTORY
        reasons.append((W_HISTORY, f"Past orders mention this drive ({', '.join(matches)})"))
    else:
        reasons.append((0, "No past order backs this drive"))

    # 3. missing information and other possible readings
    missing = [m for m in missing if str(m).strip()][:2]
    if not missing:
        total += W_COMPLETE
        reasons.append((W_COMPLETE, "Nothing needed to pick the part is missing"))
    for m in missing:
        total += W_GAP
        reasons.append((W_GAP, f"Missing: {m}"))
    for alt in alternatives[:2]:
        total += W_GAP
        reasons.append((W_GAP, f"Other possible reading: {alt.get('part', '')}"))

    value = max(0, min(100, total))
    label, colour = band(value)
    return {
        "value": value,
        "band": label,
        "colour": colour,
        "reasons": reasons,
        "rules_used": rules_used,
        "ignored": list(dict.fromkeys(ignored)),
    }
