"""Streamlit demo: interpret -> verify -> learn -> catch up.

Run:  streamlit run app.py
"""
import streamlit as st

import config
import interpreter
import rules as rules_store

st.set_page_config(page_title="Customer Request Interpreter", layout="wide")

SS = st.session_state
for key, default in [("result", None), ("request", None), ("proposal", None), ("note", None)]:
    SS.setdefault(key, default)


def reset_flow():
    SS.result = None
    SS.proposal = None
    SS.note = None


# ---- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.header("Setup")
    st.caption(f"Model: `{config.MODEL}`")
    st.caption(f"Server: `{config.BASE_URL}`")
    use_rules = st.toggle("Use learned rules", value=True, help="Turn off to show the 'before learning' behaviour.")
    st.divider()
    st.caption(f"Approved rules: {len(rules_store.load_rules())}")
    if st.button("Reset rules and log (start demo from zero)"):
        rules_store.reset_all()
        reset_flow()
        st.rerun()

st.title("Customer Request Interpreter")
st.caption(
    "Anna is on vacation. A customer sends a vague request. "
    "The assistant explains what the customer likely means, a specialist verifies it, "
    "and every correction becomes knowledge for next time."
)

tab_interpret, tab_rules, tab_catchup = st.tabs(["1. Interpret & verify", "2. Rules learned", "3. Anna's catch-up"])

# ---- tab 1: interpret & verify -----------------------------------------------
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

    if st.button("Interpret", type="primary", disabled=not request["text"].strip()):
        reset_flow()
        with st.spinner("The model is reading the customer context..."):
            try:
                SS.result = interpreter.interpret(request, use_rules=use_rules)
                SS.request = request
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    result = SS.result
    if result:
        req = SS.request
        lookup = interpreter.evidence_lookup(customer, rules_store.rules_for_customer(customer_id))
        interps = result["interpretations"]
        if not interps:
            st.warning("The model returned no interpretation. Try again.")
        else:
            top = interps[0]
            st.subheader("Most likely meaning")
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{top['summary']}**")
            c2.metric("Part", top["part"])
            c3.metric("Confidence", f"{int(top['confidence'] * 100)}%")
            if top["options"]:
                st.markdown("Options: " + ", ".join(f"`{o}`" for o in top["options"]))

            st.markdown("**Why (evidence and where it came from)**")
            for item in top["evidence"]:
                st.markdown(f"- {item}")
            ids = interpreter.evidence_ids(top["evidence"])
            with st.expander("Show the source entries behind this"):
                for i in ids:
                    if i in lookup:
                        st.markdown(f"**{i}**: {lookup[i]}")

            if result["uncertainties"]:
                st.markdown("**Not sure about**")
                for u in result["uncertainties"]:
                    st.markdown(f"- {u}")
            if result["question_to_ask"]:
                st.info(f"Ask the customer: {result['question_to_ask']}")
            if result["next_steps"]:
                st.markdown("**Suggested next steps**")
                for s in result["next_steps"]:
                    st.markdown(f"- {s}")
            for w in result["warnings"]:
                st.warning(w)

            if len(interps) > 1:
                with st.expander("Alternative readings"):
                    for alt in interps[1:]:
                        st.markdown(f"**{alt['summary']}**  \nPart `{alt['part']}`, confidence {int(alt['confidence'] * 100)}%")
                        for e in alt["evidence"]:
                            st.markdown(f"- {e}")

            with st.expander("Debug: prompt sent to the model"):
                st.code(result["prompt"])

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
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action="confirmed", correction="", rule_id="",
                    )
                    SS.note = "Confirmed. Logged for Anna."
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
                b1, b2 = st.columns(2)
                if b1.button("Approve and save rule", type="primary"):
                    rule = rules_store.add_rule(
                        customer_id, rule_text, rule_line,
                        learned_from=f"{prop['action']} on request #{req['id']} ({req['sender']}: \"{req['text']}\")",
                    )
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action=prop["action"],
                        correction=prop["correction"], rule_id=rule["rule_id"],
                    )
                    SS.proposal = None
                    SS.note = f"Rule {rule['rule_id']} saved. Run a similar request again to see the effect."
                    st.rerun()
                if b2.button("Discard"):
                    rules_store.log_event(
                        request_id=req["id"], sender=req["sender"], message=req["text"],
                        reading=top["summary"], action=prop["action"] + " (rule discarded)",
                        correction=prop["correction"], rule_id="",
                    )
                    SS.proposal = None
                    st.rerun()

# ---- tab 2: rules -------------------------------------------------------------
with tab_rules:
    st.subheader("Knowledge learned from specialists")
    all_rules = rules_store.load_rules()
    if not all_rules:
        st.info("No rules yet. Correct an interpretation in tab 1 and approve the proposed rule.")
    for r in all_rules:
        line = r["scope"].get("line", "all")
        with st.container(border=True):
            st.markdown(f"**{r['rule_id']}**  ·  {r['scope'].get('customer_id')}  ·  line: {line}")
            st.write(r["text"])
            st.caption(f"Learned from: {r['learned_from']}  ·  created {r['created']}  ·  status {r['status']}")
            if st.button("Remove this rule", key=f"del_{r['rule_id']}"):
                rules_store.delete_rule(r["rule_id"])
                st.rerun()

# ---- tab 3: catch-up ----------------------------------------------------------
with tab_catchup:
    st.subheader("Welcome back, Anna")
    log = rules_store.load_log()
    all_rules = rules_store.load_rules()
    if not log:
        st.info("Nothing happened while you were away yet. Handle a request in tab 1.")
    else:
        corrections = [e for e in log if e["action"] != "confirmed"]
        st.markdown(
            f"While you were away, **{len(log)}** request(s) were handled, "
            f"**{len(corrections)}** needed a correction, and **{len(all_rules)}** rule(s) were added to the knowledge base."
        )
        st.markdown("**What happened**")
        for e in log:
            st.markdown(f"- `{e['time']}`  **{e['sender']}**: \"{e['message']}\"  \n  Assistant read it as: {e['reading']}  \n  Outcome: **{e['action']}**"
                        + (f"  \n  Correction: {e['correction']}" if e["correction"] else "")
                        + (f"  \n  New rule: `{e['rule_id']}`" if e["rule_id"] else ""))
        if all_rules:
            st.markdown("**New rules to review** (you can remove any in tab 2)")
            for r in all_rules:
                st.markdown(f"- `{r['rule_id']}`: {r['text']}")
