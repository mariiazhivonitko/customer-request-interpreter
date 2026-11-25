"""Batch evaluation: runs the test requests without and with the learned rules.

    python evaluate.py                    # all requests, 1 run each, both conditions
    python evaluate.py --repeat 3         # 3 runs per request (shows how stable the model is)
    python evaluate.py --only 1,5,7       # selected request ids
    python evaluate.py --split heldout    # only requests marked heldout
    python evaluate.py --rules-only       # skip the no-rules baseline (faster)

It uses the rules currently saved in data/rules.json for the "with_rules" condition,
so run it after you have taught the system. Results are printed and saved to
results/eval_<time>.csv and results/eval_<time>.md (the .md table can be pasted into Notion).

What is scored automatically: the top interpretation's part number and add-on options
against the gold answer. The gold "ask" text is printed for you to judge by eye.
"""
import argparse
import csv
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import interpreter
import rules as rules_store

RESULTS_DIR = Path(__file__).parent / "results"
OVERCONFIDENT = 0.8  # a wrong answer at or above this confidence counts as overconfident


def norm_options(options) -> list[str]:
    return sorted(o.strip().lower() for o in options)


def run_one(request: dict, use_rules: bool) -> dict:
    try:
        result = interpreter.interpret(request, use_rules=use_rules)
        interps = result.get("interpretations") or []
        if not interps:
            return {"part": "none", "options": [], "conf": 0.0, "error": ""}
        top = interps[0]
        return {"part": top["part"], "options": top["options"], "conf": top["confidence"], "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"part": "error", "options": [], "conf": 0.0, "error": str(exc)[:200]}


def evaluate(requests: list[dict], conditions: list[tuple[str, bool]], repeat: int) -> list[dict]:
    rows = []
    total = len(requests) * len(conditions)
    done = 0
    for req in requests:
        gold = req["gold"]
        for cond_name, use_rules in conditions:
            trials = [run_one(req, use_rules) for _ in range(repeat)]
            done += 1
            part_ok = sum(t["part"] == gold["part"] for t in trials)
            opts_ok = sum(norm_options(t["options"]) == norm_options(gold.get("options", [])) for t in trials)
            wrong_confident = sum(t["part"] != gold["part"] and t["conf"] >= OVERCONFIDENT for t in trials)
            mode_part = Counter(t["part"] for t in trials).most_common(1)[0][0]
            row = {
                "id": req["id"],
                "split": req.get("split", ""),
                "condition": cond_name,
                "expected": gold["part"],
                "got": mode_part,
                "part_ok": part_ok,
                "options_ok": opts_ok,
                "runs": repeat,
                "avg_conf": round(sum(t["conf"] for t in trials) / repeat, 2),
                "overconfident_wrong": wrong_confident,
                "errors": sum(1 for t in trials if t["error"]),
                "message": req["text"],
                "gold_ask": gold.get("ask", ""),
            }
            rows.append(row)
            mark = "OK " if part_ok == repeat else ("~  " if part_ok else "BAD")
            print(f"[{done}/{total}] #{req['id']:<2} {cond_name:<10} {mark} expected {gold['part']}, got {mode_part} "
                  f"({part_ok}/{repeat} part, {opts_ok}/{repeat} options)", flush=True)
    return rows


def summarize(rows: list[dict]) -> list[str]:
    lines = []
    for cond in sorted({r["condition"] for r in rows}):
        sel = [r for r in rows if r["condition"] == cond]
        runs = sum(r["runs"] for r in sel)
        part = sum(r["part_ok"] for r in sel)
        opts = sum(r["options_ok"] for r in sel)
        wrong_conf = sum(r["overconfident_wrong"] for r in sel)
        errors = sum(r["errors"] for r in sel)
        lines.append(
            f"{cond:<10} part correct {part}/{runs} ({100 * part // max(runs, 1)}%), "
            f"options correct {opts}/{runs}, overconfident wrong answers: {wrong_conf}, errors: {errors}"
        )
        for split in sorted({r["split"] for r in sel if r["split"]}):
            s = [r for r in sel if r["split"] == split]
            sr = sum(r["runs"] for r in s)
            sp = sum(r["part_ok"] for r in s)
            lines.append(f"    {split:<8} part correct {sp}/{sr}")
    return lines


def markdown_table(rows: list[dict]) -> str:
    head = "| # | split | condition | expected | got | part ok | options ok | avg confidence |\n|---|---|---|---|---|---|---|---|\n"
    body = "".join(
        f"| {r['id']} | {r['split']} | {r['condition']} | {r['expected']} | {r['got']} | "
        f"{r['part_ok']}/{r['runs']} | {r['options_ok']}/{r['runs']} | {r['avg_conf']} |\n"
        for r in rows
    )
    return head + body


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1, help="runs per request and condition")
    ap.add_argument("--only", default="", help="comma-separated request ids")
    ap.add_argument("--split", default="", help="train or heldout")
    ap.add_argument("--rules-only", action="store_true", help="skip the no-rules baseline")
    args = ap.parse_args(argv)

    requests = interpreter.load_requests()
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        requests = [r for r in requests if r["id"] in wanted]
    if args.split:
        requests = [r for r in requests if r.get("split") == args.split]
    if not requests:
        sys.exit("No requests selected.")

    conditions = [("with_rules", True)] if args.rules_only else [("no_rules", False), ("with_rules", True)]
    saved = [r for r in rules_store.load_rules() if r.get("status") == "approved"]
    print(f"{len(requests)} requests x {len(conditions)} condition(s) x {args.repeat} run(s). "
          f"Approved rules in use for 'with_rules': {len(saved)}")
    for r in saved:
        print(f"  {r['rule_id']}: {r['text']}")
    if not saved and not args.rules_only:
        print("  (no rules saved yet, so both conditions will behave the same)")
    print()

    started = time.time()
    rows = evaluate(requests, conditions, args.repeat)

    print("\n=== Summary ===")
    summary = summarize(rows)
    print("\n".join(summary))
    print(f"\nTook {int(time.time() - started)} s.")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"eval_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    md_path = RESULTS_DIR / f"eval_{stamp}.md"
    md_path.write_text(
        "# Evaluation\n\n" + markdown_table(rows) + "\n## Summary\n\n"
        + "\n".join(f"- {line.strip()}" for line in summary) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {csv_path.name} and {md_path.name} in the 'results' folder.")


if __name__ == "__main__":
    main()