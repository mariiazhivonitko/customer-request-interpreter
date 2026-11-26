"""Learned rules and the interaction log (both plain JSON files)."""
import json
from datetime import date, datetime
from pathlib import Path

import llm

DATA = Path(__file__).parent / "data"
RULES_FILE = DATA / "rules.json"
LOG_FILE = DATA / "log.json"

RULE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rule_text", "scope_line"],
    "properties": {
        "rule_text": {"type": "string"},
        "scope_line": {"type": "string"},
    },
}

RULE_PROMPT = """A specialist corrected an AI assistant's reading of a customer message.
Turn the correction into ONE short, reusable rule for future requests from this customer.

Write the rule as an instruction starting with "When" or "For". Be specific about the
customer, contact person or production line it applies to. Do not generalise beyond what
the correction says. scope_line: the line number as a string (for example "1") if the rule
only applies to one production line, otherwise "all".

Customer: {customer}
Customer message: "{message}"
AI's reading: {summary} (part: {part}, options: {options})
Specialist's correction: {correction}

Return JSON only."""


def _read(path: Path, default):
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---- rules -----------------------------------------------------------------

def load_rules() -> list[dict]:
    return _read(RULES_FILE, [])


def rules_for_customer(customer_id: str) -> list[dict]:
    return [
        r
        for r in load_rules()
        if r.get("status") == "approved"
        and r["scope"].get("customer_id") in (customer_id, None)
    ]


def propose_rule(customer_name: str, message: str, interpretation: dict, correction: str) -> dict:
    """Ask the model for a rule candidate. Falls back to the raw correction text."""
    prompt = RULE_PROMPT.format(
        customer=customer_name,
        message=message,
        summary=interpretation.get("summary", ""),
        part=interpretation.get("part", ""),
        options=", ".join(interpretation.get("options", [])) or "none",
        correction=correction,
    )
    try:
        out = llm.chat_json(prompt, schema=RULE_SCHEMA, name="rule")
        text = str(out.get("rule_text", "")).strip()
        line = str(out.get("scope_line", "all")).strip() or "all"
        if text:
            return {"text": text, "line": line}
    except Exception:  # noqa: BLE001
        pass
    return {"text": f"For {customer_name}: {correction}", "line": "all"}


def add_rule(customer_id: str, text: str, line: str, learned_from: str, taught_by: str = "Specialist") -> dict:
    rules = load_rules()
    used = {r["rule_id"] for r in rules}
    n = len(rules) + 1
    while f"r{n:03d}" in used:
        n += 1
    today = date.today().isoformat()
    rule = {
        "rule_id": f"r{n:03d}",
        "scope": {"customer_id": customer_id, "line": line},
        "text": text.strip(),
        "taught_by": taught_by,
        "learned_from": learned_from,
        "created": today,
        "status": "approved",
        "times_confirmed": 0,
        "last_confirmed": today,
        "version": 1,
    }
    rules.append(rule)
    _write(RULES_FILE, rules)
    return rule


def record_confirmation(rule_ids: list) -> None:
    """A specialist confirmed a reading that used these rules: add evidence."""
    if not rule_ids:
        return
    rules = load_rules()
    for r in rules:
        if r["rule_id"] in rule_ids:
            r["times_confirmed"] = int(r.get("times_confirmed", 0)) + 1
            r["last_confirmed"] = date.today().isoformat()
    _write(RULES_FILE, rules)


def set_status(rule_id: str, status: str) -> None:
    """approved (active) or flagged. Marking a flagged rule reviewed also refreshes it."""
    rules = load_rules()
    for r in rules:
        if r["rule_id"] == rule_id:
            r["status"] = status
            if status == "approved":
                r["last_confirmed"] = date.today().isoformat()
    _write(RULES_FILE, rules)


def delete_rule(rule_id: str) -> None:
    _write(RULES_FILE, [r for r in load_rules() if r["rule_id"] != rule_id])


# ---- log (used for Anna's catch-up view) -------------------------------------

def load_log() -> list[dict]:
    return _read(LOG_FILE, [])


def log_event(**fields) -> None:
    log = load_log()
    log.append({"time": datetime.now().isoformat(timespec="seconds"), **fields})
    _write(LOG_FILE, log)


def reset_all() -> None:
    _write(RULES_FILE, [])
    _write(LOG_FILE, [])
