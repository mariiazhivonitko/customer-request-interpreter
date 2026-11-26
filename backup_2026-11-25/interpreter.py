"""Builds the prompt from customer context + approved rules and interprets a request.

Division of work:
- the language model reads the message and decides (a) which drive is installed on the
  machine the customer means and (b) whether the customer wants the same, one step up
  or one step down (this is where the approved rules help);
- plain code then looks up the actual part in the catalogue.
Small models are good at the first and unreliable at the second.
"""
import copy
import json
import re
from pathlib import Path

import llm
import rules as rules_store

DATA = Path(__file__).parent / "data"

CHANGES = ["same", "next_higher", "next_lower", "other"]

INSTRUCTIONS = """You help an account specialist who does not know this customer well.
A customer sent a vague message. Work out what the customer most likely means,
using ONLY the facts given below. Never invent facts, part numbers or stock.

How to think:
1. Find which production line / equipment the message refers to.
   For "like the X one but ..." the reference machine is X.
2. Read the APPROVED RULES first. They come from experienced specialists and
   override everything else.
3. current_part = the drive installed on the machine used as the reference. For
   "like the X one but ..." the reference is machine X, not the machine the new
   drive is for. Otherwise it is the machine the message is about (see INSTALLED BASE).
4. change = how the wanted drive relates to current_part, using the wording of the
   message, the rules and the sender's style:
   - "same": same power rating (same as before, spare, replacement, fault report)
   - "next_higher": one step up in power rating (for example bigger, stronger)
   - "next_lower": one step down in power rating (for example smaller, weaker)
   - "other": none of these fits; then choose 'part' yourself
5. 'part' = your choice of part number from the catalogue (it is checked against
   current_part and change afterwards).
6. options = add-on options (such as a module) that the history or installed base
   shows for the same machine.
7. Give up to 3 interpretations, best first, each with a confidence from 0 to 1.
8. In 'evidence' list the ids you relied on (for example h4, L1, r001), each with a short reason.
9. Suggest ONE question the specialist can ask the customer by phone to confirm.
10. If the message reports a problem and does not order anything, use change "same"
    and suggest diagnosing first in the next steps.
11. In 'reasoning', first write one or two sentences: which machine, which drive is
    installed now, and what the sender's wording means. Then fill the other fields.

Return JSON only."""

EXAMPLE = """EXAMPLE (a different customer, for format only)
Customer: Example Plastics Oy. INSTALLED BASE: L1: Line 1 press, drive PD-FC-075 (upgraded 2022).
History: e1 [2022-02, call_note]: Customer asked for a 'stronger' drive for the press; motor was upsized, shipped PD-FC-075 with EMC filter.
Message from Ville: "Need another drive for the press, same as last time."
Answer:
{"interpretations":[{"reasoning":"The press is Line 1. Its installed drive is PD-FC-075 from the 2022 upgrade, and 'same as last time' means that drive again with the EMC filter from that order.","current_part":"PD-FC-075","change":"same","part":"PD-FC-075","summary":"Same drive as the 2022 upgrade for the Line 1 press","options":["EMC filter"],"confidence":0.8,"evidence":["e1: last order for the press was PD-FC-075 with EMC filter","L1: installed drive"]},{"reasoning":"The customer might want more power than last time.","current_part":"PD-FC-075","change":"next_higher","part":"PD-FC-110","summary":"One power class above the current press drive","options":["EMC filter"],"confidence":0.15,"evidence":["L1: installed drive PD-FC-075"]}],"uncertainties":["Unclear whether the press motor is unchanged since 2022"],"question_to_ask":"Is this a spare for the current 2022 setup?","next_steps":["Check stock","Draft a quote with the EMC filter"]}
"""

BASE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretations", "uncertainties", "question_to_ask", "next_steps"],
    "properties": {
        "interpretations": {"type": "array", "items": {"type": "object"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "question_to_ask": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def list_customers() -> list[str]:
    return sorted(p.stem for p in (DATA / "customers").glob("*.json"))


def load_customer(customer_id: str) -> dict:
    return load_json(DATA / "customers" / f"{customer_id}.json")


def load_products() -> dict:
    return load_json(DATA / "products.json")


def load_requests() -> list[dict]:
    return load_json(DATA / "test_requests.json")


def power_steps(products: dict) -> list[str]:
    """Main range of drives, smallest to largest (accepted substitutes are left out)."""
    main = [p for p in products["parts"] if "substitute for" not in p.get("note", "")]
    return [p["part"] for p in sorted(main, key=lambda p: p["rating_kw"])]


def make_schema(products: dict) -> dict:
    """JSON schema for the model output, with part numbers restricted to the catalogue."""
    parts = [p["part"] for p in products["parts"]]
    schema = copy.deepcopy(BASE_SCHEMA)
    schema["properties"]["interpretations"]["items"] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "reasoning", "current_part", "change", "part",
            "summary", "options", "confidence", "evidence",
        ],
        "properties": {  # order matters: the model reasons first
            "reasoning": {"type": "string"},
            "current_part": {"type": "string", "enum": parts},
            "change": {"type": "string", "enum": CHANGES},
            "part": {"type": "string", "enum": parts},
            "summary": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
    }
    return schema


def _catalogue_text(products: dict) -> str:
    lines = [f"Company: {products['company']}. {products['notes']}"]
    for p in products["parts"]:
        extra = f" ({p['note']})" if p.get("note") else ""
        lines.append(f"- {p['part']}: {p['rating_kw']} kW, {p['stock']}{extra}")
    opts = ", ".join(f"{o['option']} ({o['stock']})" for o in products["options"])
    lines.append(f"Options: {opts}")
    lines.append("Power steps, smallest to largest: " + " < ".join(power_steps(products)))
    return "\n".join(lines)


def _customer_text(customer: dict, sender: str) -> str:
    lines = [
        f"{customer['name']}, {customer['location']}, customer since {customer['customer_since']}.",
        f"Business: {customer['business']}",
        "INSTALLED BASE",
    ]
    for item in customer["installed_base"]:
        lines.append(
            f"- {item['id']}: Line {item['line']} {item['equipment']}, drive {item['drive']}, "
            f"installed {item['installed']}. {item['notes']}"
        )
    lines.append("HISTORY (oldest first)")
    for h in customer["history"]:
        lines.append(f"- {h['id']} [{h['date']}, {h['source']}]: {h['text']}")
    contact = next((c for c in customer["contacts"] if c["name"] == sender), None)
    if contact:
        lines.append(f"SENDER: {contact['name']} ({contact['role']}). Style: {contact['style']}")
    else:
        lines.append(f"SENDER: {sender} (no profile known)")
    return "\n".join(lines)


def _rules_text(rule_list: list[dict]) -> str:
    if not rule_list:
        return "APPROVED RULES: none yet."
    lines = ["APPROVED RULES (follow these first)"]
    for r in rule_list:
        line = r["scope"].get("line", "all")
        scope = "all lines" if line in (None, "all") else f"line {line}"
        lines.append(f"- {r['rule_id']} ({scope}): {r['text']}")
    return "\n".join(lines)


def build_prompt(customer: dict, request: dict, rule_list: list[dict], products: dict) -> str:
    return (
        f"{INSTRUCTIONS}\n\n{EXAMPLE}\n"
        f"NOW THE REAL TASK\n\n"
        f"PRODUCT CATALOGUE\n{_catalogue_text(products)}\n\n"
        f"CUSTOMER\n{_customer_text(customer, request['sender'])}\n\n"
        f"{_rules_text(rule_list)}\n\n"
        f'MESSAGE from {request["sender"]}:\n"{request["text"]}"\n\n'
        f"Answer (JSON only):"
    )


def evidence_lookup(customer: dict, rule_list: list[dict]) -> dict[str, str]:
    """Map evidence ids (h4, L1, r001) to readable text with their source."""
    lookup = {}
    for h in customer["history"]:
        lookup[h["id"]] = f"{h['text']}  (source: {h['source']}, {h['date']})"
    for item in customer["installed_base"]:
        lookup[item["id"]] = (
            f"Line {item['line']} {item['equipment']}: {item['drive']}, {item['notes']}  (source: installed base)"
        )
    for r in rule_list:
        lookup[r["rule_id"]] = f"{r['text']}  (source: {r.get('learned_from', 'specialist rule')})"
    return lookup


def evidence_ids(evidence_items: list[str]) -> list[str]:
    ids = []
    for item in evidence_items:
        ids += re.findall(r"\b([hLr]\d+)\b", item)
    return list(dict.fromkeys(ids))


def resolve_part(current_part: str, change: str, steps: list[str]):
    """Catalogue lookup done in code. Returns None if it cannot be resolved."""
    if current_part not in steps:
        return None
    i = steps.index(current_part)
    if change == "same":
        return current_part
    if change == "next_higher":
        return steps[min(i + 1, len(steps) - 1)]
    if change == "next_lower":
        return steps[max(i - 1, 0)]
    return None


STEP_TEXT = {
    "same": "same rating as the current",
    "next_higher": "next power class up from the current",
    "next_lower": "next power class down from the current",
}


def normalize(result: dict, products: dict) -> dict:
    """Clean up model output and resolve the part in code."""
    known = {p["part"] for p in products["parts"]}
    steps = power_steps(products)
    cleaned = []
    warnings = []
    for it in (result.get("interpretations") or [])[:3]:
        try:
            conf = float(it.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        part = str(it.get("part", "none"))
        summary = str(it.get("summary", ""))
        current = str(it.get("current_part", ""))
        change = str(it.get("change", "other"))
        options = [str(o) for o in (it.get("options") or [])]

        resolved = resolve_part(current, change, steps)
        if resolved:
            part = resolved
            summary = f"{part}: {STEP_TEXT[change]} {current}"
            if options:
                summary += f", with {', '.join(options)}"
        elif part not in known and part.lower() != "none":
            warnings.append(f"The model mentioned '{part}', which is not in the catalogue.")

        evidence = [str(e) for e in (it.get("evidence") or [])]
        if it.get("reasoning"):
            evidence.insert(0, f"Model reasoning: {it['reasoning']}")
        cleaned.append(
            {
                "summary": summary,
                "part": part,
                "options": options,
                "confidence": max(0.0, min(1.0, conf)),
                "evidence": evidence,
                "current_part": current,
                "change": change,
            }
        )
    cleaned.sort(key=lambda x: x["confidence"], reverse=True)
    seen, unique = set(), []
    for it in cleaned:
        key = (it["part"], tuple(sorted(it["options"])))
        if key not in seen and (it["confidence"] >= 0.05 or not unique):
            seen.add(key)
            unique.append(it)
    cleaned = unique
    return {
        "interpretations": cleaned,
        "uncertainties": [str(u) for u in (result.get("uncertainties") or [])],
        "question_to_ask": str(result.get("question_to_ask", "")),
        "next_steps": [str(s) for s in (result.get("next_steps") or [])],
        "warnings": warnings,
    }


def interpret(request: dict, use_rules: bool = True) -> dict:
    """request = {customer_id, sender, text}. Returns the normalized interpretation."""
    customer = load_customer(request["customer_id"])
    products = load_products()
    rule_list = (
        rules_store.rules_for_customer(request["customer_id"]) if use_rules else []
    )
    prompt = build_prompt(customer, request, rule_list, products)
    raw = llm.chat_json(prompt, schema=make_schema(products), name="interpretation")
    result = normalize(raw, products)
    result["prompt"] = prompt  # handy for debugging in the UI
    return result