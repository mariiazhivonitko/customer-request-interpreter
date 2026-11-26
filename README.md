# Customer Request Interpreter (Demola prototype)

Flow: vague customer message -> interpretation card with evidence -> specialist
confirms or corrects -> correction becomes an approved rule -> next similar request is
interpreted better -> Anna sees a catch-up when she returns.

All company and customer data is fictional.

## Run it

1. In LM Studio load `google/gemma-4-e4b`, open the **Developer** tab and **start the server**
   (default address `http://localhost:1234`).
2. In a terminal, in this folder:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python try_one.py 1              # quick check without the UI
streamlit run app.py             # the demo
```

If the model name in LM Studio is different, set it without editing code:

```bash
LLM_MODEL="the-name-shown-in-lm-studio" streamlit run app.py
```

Switching to Qwen or another model = change `LLM_MODEL` (or `config.py`). Nothing else.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI (interpret & verify, rules learned, Anna's catch-up) |
| `interpreter.py` | Builds the prompt from customer data + rules, calls the model |
| `rules.py` | Rule proposal (model), saving rules, interaction log |
| `llm.py` | LM Studio / OpenAI-compatible client with JSON handling |
| `config.py` | Model name, server address, temperature |
| `try_one.py` | Terminal test for one request |
| `data/customers/valkama.json` | Fictional customer: contacts, installed base, history with sources |
| `data/products.json` | Fictional product catalogue |
| `data/test_requests.json` | Requests with gold answers (`split`: train / heldout) |
| `data/rules.json` | Learned rules (starts empty) |
| `data/log.json` | Created automatically, feeds the catch-up tab |

## Event demo (1–2 minutes, Compare tab)

1. Tab 1 "Compare: before / after", request #7 ("The bakery line drive is acting up again"). Click **Compare**.
2. Left: without Anna's knowledge the reading is crossed out (wrong line). Right: with her rule r002 it is
   Line 4, PD-FC-040, with the score and a "Why this score?" box.
3. Before the event: run it live once, click **Save this run as the backup**, then switch on
   **Use saved results** in the sidebar so the table demo never depends on the model.

Confidence is calculated in `scoring.py` from evidence (learned rule +40, past order +30,
nothing missing +20, each missing spec or other reading -15). The model no longer rates itself.
Full explanation: [docs/confidence.md](docs/confidence.md).

## Full demo script (5 minutes)

1. Tab 1, request #1 ("same as before but a bit bigger. Rush."). Show the card and the evidence.
2. Call result: the drive must also include the Profinet module. Choose **Correct**, write the reason, submit.
3. Review the proposed rule, approve it.
4. Select request #2 ("like the mixer one but smaller") and interpret again. The rule is used.
5. Tab 2: the rule with its origin. Tab 3: Anna's catch-up.

Use the sidebar button to reset to zero before every rehearsal.
Tip: toggle "Use learned rules" off to show the before/after behaviour.

## Tuning a small model

- Keep `temperature` low (0.2).
- If it returns weak readings, shorten the context in `data/customers/*.json` or make the history clearer.
- The prompt is in `interpreter.py` (`INSTRUCTIONS`, `EXAMPLE`). Edit the wording there.
- Use the "Debug: prompt sent to the model" expander in the UI to see exactly what the model got.
