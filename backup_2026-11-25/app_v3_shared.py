"""Streamlit demo: compare (before/after) -> interpret & verify -> rules -> catch-up.

Run:  streamlit run app.py

Both tabs share one stored result per request and condition (with / without rules),
so the Compare tab and the Interpret tab always show the same reading.
"Run again" makes a new model call on purpose.
"""
import json
from pathlib import Path

import streamlit as st

import config
import interpreter
import rules as rules_store
import scoring

st.set_page_config(page_title="Customer Request Interpreter", layout="wide")

SAVED_FILE = Path(__file__).parent / "data" / "saved_results.json"
STATE_LABEL = {"active": "🟢 active", "fading": "🟡 fading", "flagged": "🟠 flagged", "inactive": "⚪ inactive"}

SS = st.session_state
for key, default in [("cache", {}), ("result", None), ("result_from_cache", False), ("request", None),
                     ("proposal", None), ("note", None), ("cmp_id", None)]:
    SS.setdefault(key, default)


# ---- shared results ---------------------------------------------------------
def load_saved() -> dict:
    if SAVED_FILE.exists() and SAVED_FILE.read_text(encoding="utf-8").strip():
        return json.loads(SAVED_FILE.read_text(encoding="utf-8"))
    return {}


def save_saved(data: dict) -> None:
    SAVED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cache_key(req: dict, use_rules: bool) -> str:
    return f"{req['id']}|{req['text']}|{'rules' if use_rules else 'no_rules'}"


def get_result(req: dict, use_rules: bool, fresh: bool = False):
    """Returns (result, came_from_store). Calls the model only when needed."""
    key = cache_key(req, use_rules)
    if not fresh and key in SS.cache:
        return SS.cache[key], True
    res = interpreter.interpret(req, use_rules=use_rules)
    SS.cache[key] = res
    return res, False


def clear_results() -> None:
    """The rules changed, so stored readings are out of date."""
    SS.cache = {}
    SS.result = None
    SS.cmp_id = None


def reset_flow():
    SS.result = None
    SS.proposal = None
    SS.note = None


# ---- display helpers ----------------------------------------------------------
def rule_card(rule: dict, removable: bool = False) -> None:
    state = scoring.rule_state(rule)
    with st.container(border=True):
        st.markdown(f"**Rule {rule['rule_id']}** · {STATE_LABEL.get(state, state)} · "
                    f"line: {rule['scope'].get('line', 'all')}")
        st.write(rule["text"])
        st.caption(
            f"Taught by {rule.get('taught_by', 'a specialist')} · confirmed {rule.get('times_confirmed', 0)}× · "
            f"last confirmed {rule.get('last_confirmed', '–')} · created {rule.get('created', '–')}  \n"
            f"Source: {rule.get('learned_from', '')}"
        )
        if removable:
            c1, c2 = st.columns(2)
            if state == "flagged":
                if c1.button("Mark reviewed", key=f"rev_{rule['rule_id']}"):
                    rules_store.set_status(rule["rule_id"], "approved")
                    st.rerun()
            elif c1.button("Flag for review", key=f"flag_{rule['rule_id']}"):
                rules_store.set_status(rule["rule_id"], "flagged")
                st.rerun()
            if c2.button("Remove this rule", key=f"del_{rule['rule_id']}"):
                rules_store.delete_rule(rule["rule_id"])
                clear_results()
                st.rerun()


def score_line(sc: dict) -> str:
    return f"**{sc['value']}%** :{sc['colour']}-background[{sc['band']}]"


def why_box(sc: dict) -> None:
    with st.container(border=True):
        st.markdown("**Why this score?**")
        for pts, text in sc["reasons"]:
            st.markdown(f"`{pts:+d}`  {text}")
        st.markdown(f"**= {sc['value']}%**")
        if sc.get("ignored"):
            st.caption("Ignored (the model cited ids that are not in the data): " + ", ".join(sc["ignored"]))


def show_result(result: dict, customer: dict, key: str, show_rules: bool = True) -> None:
    """The full card: part, score, why, evidence with sources, gaps, question, alternatives."""
    interpreter.add_scores(result, customer)  # re-score: rule confirmations may have changed
    interps = result["interpretations"]
    if not interps:
        st.warning("The model returned no interpretation. Try again.")
        return
    top = interps[0]
    all_rules = rules_store.load_rules()
    lookup = interpreter.evidence_lookup(customer, all_rules)

    st.markdown(f"**{top['summary']}**")
    c1, c2 = st.columns(2)
    c1.metric("Part", top["part"])
    c2.markdown("Confidence")
    c2.markdown(score_line(top["score"]))
    if top["options"]:
        st.markdown("Options: " + ", ".join(f"`{o}`" for o in top["options"]))
    why_box(top["score"])

    if show_rules:
        by_id = {r["rule_id"]: r for r in all_rules}
        for rid in top["score"]["rules_used"]:
            if rid in by_id:
                rule_card(by_id[rid])

    st.markdown("**Evidence and where it came from**")
    for item in top["evidence"]:
        st.markdown(f"- {item}")
    ids = interpreter.evidence_ids(top["evidence"])
    with st.expander("Source entries behind this", expanded=True):
        shown = [i for i in ids if i in lookup]
        if not shown:
            st.caption("No source entries cited.")
        for i in shown:
            st.markdown(f"**{i}**: {lookup[i]}")

    if result.get("missing_specs"):
        st.markdown("**Missing to pick the part**")
        for m in result["missing_specs"]:
            st.markdown(f"- {m}")
    if result.get("uncertainties"):
        st.markdown("**Not sure about**")
        for u in result["uncertainties"]:
            st.markdown(f"- {u}")
    if result.get("question_to_ask"):
        st.info(f"Ask the customer: {result['question_to_ask']}")
    if result.get("next_steps"):
        st.markdown("**Suggested next steps**")
        for s in result["next_steps"]:
            st.markdown(f"- {s}")
    for w in result.get("warnings", []):
        st.warning(w)

    if len(interps) > 1:
        with st.expander(f"Alternative readings ({len(interps) - 1})"):
            for alt in interps[1:]:
                st.markdown(f"**{alt['summary']}**  \nPart `{alt['part']}` · {score_line(alt['score'])}")
                for e in alt["evidence"]:
                    st.markdown(f"- {e}")

    with st.expander("Debug: prompt sent to the model"):
        st.code(result.get("prompt", ""), language=None)


# ---- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.header("Setup")
    st.caption(f"Model: `{config.MODEL}` · temperature {config.TEMPERATURE}")
    st.caption(f"Server: `{config.BASE_URL}`")
    user = st.text_input("Specialist on duty", "Mikko", help="Shown as the teacher of new rules.")
    use_saved = st.toggle("Use saved results (no model call)", value=False,
                          help="For the event table: both tabs show the saved good run instead of calling the model.")
    use_rules = st.toggle("Use learned rules (tab 2)", value=True,
                          help="Only affects tab 2. The Compare tab always shows both.")
    st.divider()
    st.caption(f"Learned rules: {len(rules_store.load_rules())}")
    if st.button("Reset rules and log (start demo from zero)"):
        rules_store.reset_all()
        reset_flow()
        clear_results()
        st.rerun()

# saved runs go into the shared store, so both tabs show them
if use_saved:
    by_id = {r["id"]: r for r in interpreter.load_requests()}
    for rid, pair in load_saved().items():
        r = by_id.get(int(rid))
        if r:
            SS.cache.setdefault(cache_key(r, False), pair["before"])
            SS.cache.setdefault(cache_key(r, True), pair["after"])

st.title("Customer Request Interpreter")
st.caption("Anna is on holiday and Mikko covers her customers. A customer sends a vague request. "
           "Anna's knowledge, captured as learned rules, tells Mikko what the customer actually means.")

tab_compare, tab_interpret, tab_rules, tab_catchup = st.tabs(
    ["1. Compare: before / after", "2. Interpret & verify", "3. Rules learned", "4. Anna's catch-up"]
)

# ---- tab 1: compare ------------------------------------------------------------
with tab_compare:
    all_requests = interpreter.load_requests()
    labels = {f"#{r['id']}  {r['sender']}: \"{r['text']}\"": r for r in all_requests}
    default_idx = next((i for i, r in enumerate(all_requests) if r["id"] == 7), 0)
    pick = st.selectbox("Incoming request", list(labels), index=default_idx, key="cmp_pick")
    req = labels[pick]

    b1, b2, _ = st.columns([1, 1, 4])
    run = b1.button("Compare", type="primary", key="cmp_btn")
    rerun = b2.button("Run again", key="cmp_rerun", help="New model calls, replacing the stored readings.")
    if run or rerun:
        try:
            with st.spinner("Reading the request without Anna's knowledge..."):
                get_result(req, False, fresh=rerun)
            with st.spinner("Reading it again with Anna's knowledge..."):
                get_result(req, True, fresh=rerun)
            SS.cmp_id = req["id"]
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    kb, ka = cache_key(req, False), cache_key(req, True)
    if SS.cmp_id == req["id"] and kb in SS.cache and ka in SS.cache:
        before, after = SS.cache[kb], SS.cache[ka]
        customer = interpreter.load_customer(req["customer_id"])
        bi, ai = before["interpretations"], after["interpretations"]
        lines = {i["drive"]: f"Line {i['line']} ({i['equipment'].lower()})" for i in customer["installed_base"]}
        gold = (req.get("gold") or {}).get("part")

        left, right = st.columns(2, gap="large")
        with left:
            with st.container(border=True):
                st.markdown("#### ❌ Without Anna's knowledge")
                st.caption("No learned rules. This is what a newcomer, or a plain chatbot, would do.")
                if bi:
                    b = bi[0]
                    wrong = (b["part"] != gold) if gold else (ai and b["part"] != ai[0]["part"])
                    where = lines.get(b["current_part"], "")
                    if wrong:
                        st.markdown(f"### :red[~~{b['part']}~~ {'· ~~' + where + '~~' if where else ''}]")
                        st.error("Wrong reading: acting on it means sending the wrong part.")
                    else:
                        st.markdown(f"### {b['part']} {'· ' + where if where else ''}")
                show_result(before, customer, key="before", show_rules=False)

        with right:
            with st.container(border=True):
                st.markdown("#### ✅ With Anna's knowledge")
                st.caption("The same request, read with the rules Anna taught the system.")
                if ai:
                    a = ai[0]
                    where = lines.get(a["current_part"], "")
                    st.markdown(f"### :green[{a['part']} {'· ' + where if where else ''}]")
                show_result(after, customer, key="after")

        st.markdown(f"**How to read the score:** {scoring.LEGEND}.  \n"
                    "The score is calculated from evidence (learned rules, past orders, missing information), "
                    "not reported by the model itself. Without a learned rule it can never reach *Proceed*.")
        st.caption("Tab 2 shows exactly these readings for the same request. Use 'Run again' for a new model call.")
        if st.button("Save these readings as the backup for the event table"):
            saved = load_saved()
            saved[str(req["id"])] = {"before": before, "after": after}
            save_saved(saved)
            st.success("Saved. Turn on 'Use saved results' in the sidebar to show them without calling the model.")

# ---- tab 2: interpret & verify -----------------------------------------------
with tab_interpret:
    customers = interpreter.list_customers()
    customer_id = st.selectbox("Customer", customers)
    customer = interpreter.load_customer(customer_id)
    demo_requests = [r for r in interpreter.load_requests() if r["customer_id"] == customer_id]

    options = {f"#{r['id']}  {r['sender']}: {r['text']}": r for r in demo_requests}
    choice = st.selectbox("Incoming request", ["Write my own..."] + list(options.keys()), on_change=reset_flow)

    if choice == "Write my own...":
        sender = st.selectbox("Sender", [c["name"] for c in customer["contacts"]], on_change=reset_flow)
        text = st.text_area("Message", "", on_change=reset_flow)
        request = {"id": 0, "customer_id": customer_id, "sender": sender, "text": text}
    else:
        base = options[choice]
        request = {"id": base["id"], "customer_id": customer_id, "sender": base["sender"], "text": base["text"]}
        with st.expander("Gold answer (what an experienced specialist would say)"):
            st.json(base["gold"])

    i1, i2, _ = st.columns([1, 1, 4])
    go = i1.button("Interpret", type="primary", disabled=not request["text"].strip())
    go_fresh = i2.button("Run again", key="int_rerun", disabled=not request["text"].strip(),
                         help="New model call, replacing the stored reading.")
    if go or go_fresh:
        reset_flow()
        with st.spinner("The model is reading the customer context..."):
            try:
                SS.result, SS.result_from_cache = get_result(request, use_rules, fresh=go_fresh)
                SS.request = request
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    result = SS.result
    if result:
        req = SS.request
        st.caption(("Learned rules ON" if use_rules else "Learned rules OFF") + " · "
                   + ("same reading as stored (Compare tab or earlier run)" if SS.result_from_cache else "new model call"))
        st.subheader("Most likely meaning")
        show_result(result, customer, key="interpret")
        interps = result["interpretations"]
        if interps:
            top = interps[0]
            # ---- verify -------------------------------------------------
            st.divider()
            st.subheader("Verify with the customer (phone call), then decide")
            decision_options = ["Confirm: the top reading is right"]
            decision_options += [f"Pick alternative: {a['summary']}" for a in interps[1:]]
            decision_options += ["Correct: the customer meant something else"]
            decision = st.radio("Outcome of the call", decision_options, key="decision")
            reason = st.text_area(
                "What did the customer actually mean? (short reason, becomes a rule)",
                key="reason",
                placeholder="e.g. The new drive must include the Profinet module, like the existing mixer drive.",
            )
            if st.button("Submit decision"):
                if decision.startswith("Confirm"):
                    rules_store.record_confirmation(top["score"]["rules_used"])
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action="confirmed", correction="", rule_id="",
                    )
                    used = ", ".join(top["score"]["rules_used"])
                    SS.note = "Confirmed. Logged for Anna." + (f" Rule {used} gained one more confirmation." if used else "")
                    SS.proposal = None
                else:
                    if decision.startswith("Pick alternative"):
                        picked = next(a for a in interps[1:] if decision.endswith(a["summary"]))
                        correction = f"The correct reading is: {picked['summary']}. {reason}".strip()
                        action = "picked alternative"
                    else:
                        correction = reason.strip()
                        action = "corrected"
                    if not correction:
                        st.error("Please write what the customer actually meant.")
                    else:
                        with st.spinner("Turning the correction into a rule..."):
                            proposal = rules_store.propose_rule(customer["name"], req["text"], top, correction)
                        proposal["correction"] = correction
                        proposal["action"] = action
                        SS.proposal = proposal
                        SS.note = None

            if SS.note:
                st.success(SS.note)

            # ---- learn --------------------------------------------------
            prop = SS.proposal
            if prop:
                st.divider()
                st.subheader("Proposed rule (review before it is saved)")
                rule_text = st.text_area("Rule", prop["text"], key="rule_text")
                line_options = ["all"] + [str(i["line"]) for i in customer["installed_base"]]
                default_idx = line_options.index(prop["line"]) if prop["line"] in line_options else 0
                rule_line = st.selectbox("Applies to line", line_options, index=default_idx, key="rule_line")
                c1, c2 = st.columns(2)
                if c1.button("Approve and save rule", type="primary"):
                    rule = rules_store.add_rule(
                        customer_id, rule_text, rule_line,
                        learned_from=f"{prop['action']} on request #{req['id']} ({req['sender']}: \"{req['text']}\")",
                        taught_by=user or "Specialist",
                    )
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action=prop["action"],
                        correction=prop["correction"], rule_id=rule["rule_id"],
                    )
                    clear_results()
                    SS.proposal = None
                    SS.note = f"Rule {rule['rule_id']} saved. Run a similar request again to see the effect."
                    st.rerun()
                if c2.button("Discard"):
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action=prop["action"] + " (rule discarded)",
                        correction=prop["correction"], rule_id="",
                    )
                    SS.proposal = None
                    st.rerun()
    elif SS.note:
        st.success(SS.note)

# ---- tab 3: rules -------------------------------------------------------------
with tab_rules:
    st.subheader("Knowledge learned from specialists")
    st.caption("Confirmations make a rule stronger. A rule not confirmed for a year starts to fade. "
               "A flagged rule waits for its owner to review it.")
    all_rules = rules_store.load_rules()
    if not all_rules:
        st.info("No rules yet. Correct an interpretation in tab 2 and approve the proposed rule.")
    for r in all_rules:
        rule_card(r, removable=True)

# ---- tab 4: catch-up ----------------------------------------------------------
with tab_catchup:
    st.subheader("Welcome back, Anna")
    log = rules_store.load_log()
    all_rules = rules_store.load_rules()
    to_check = [r for r in all_rules if scoring.rule_state(r) in ("flagged", "fading")]
    if not log:
        st.info("Nothing happened while you were away yet. Handle a request in tab 2.")
    else:
        corrections = [e for e in log if e["action"] != "confirmed"]
        st.markdown(
            f"While you were away, **{len(log)}** request(s) were handled, "
            f"**{len(corrections)}** needed a correction, and **{len(all_rules)}** rule(s) are in the knowledge base."
        )
        st.markdown("**What happened**")
        for e in log:
            st.markdown(f"- `{e['time']}`  **{e['sender']}**: \"{e['message']}\"  \n  Assistant read it as: {e['reading']}  \n  Outcome: **{e['action']}**"
                        + (f"  \n  Correction: {e['correction']}" if e["correction"] else "")
                        + (f"  \n  New rule: `{e['rule_id']}`" if e["rule_id"] else ""))
    if to_check:
        st.markdown(f"**{len(to_check)} rule(s) to check** (manage them in tab 3)")
        for r in to_check:
            st.markdown(f"- `{r['rule_id']}` ({scoring.rule_state(r)}): {r['text']}")
