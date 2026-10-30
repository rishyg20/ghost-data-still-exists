# AI-Build Hackathon Playbook

This playbook is for timed hackathons where you build an AI agent with a coding assistant (Claude Code, Codex, Cursor and similar). The judges look at the output file, the code, your chat transcript and an AI interview. It was written for HackerRank Orchestrate, but it works for most events in this format.

> **The one-line version:** understand the problem yourself, let the LLM do the reasoning, let code check the LLM, measure every change, and rehearse how your system fails.

---

## 0. Lessons that cost me points (read these first)

From my first run (rank 375 of 4,090, top 10%). My output scored 87%, but I lost most points on the code review and the interview. Each lesson below is linked to the section where it applies.

1. **Read the brief as a scoring rubric.** If it says "AI-powered agent", "retrieval" or "multimodal", the judges score how you used AI, not just whether the answers are right. A pipeline that's 95% deterministic can produce correct output and still score badly on design. See [§3](#3-architecture-and-planning).
2. **LLM proposes, code verifies.** Hard-coded rules belong *around* the model as validators, not *instead of* it. Use them for schema checks, allowed values, time cutoffs, citation checks and "this ID must exist in that file". Don't use them for the decision itself. See [§3](#3-architecture-and-planning).
3. **Review every hard-coded value before you submit.** That means every regex, mapping, constant, threshold and "assume this is always true". In my build, a policy file had a plain-English `condition` column. The code never read it: some conditions were coded by hand, and the rest were silently assumed to be true. That was exactly the input an LLM should have reasoned over. See [§6](#6-validation-and-qc-gates), gate 6.
4. **Make the assistant list its assumptions.** After every design step, ask: "Which columns or fields are you ignoring? What are you treating as always true? What did you hard-code from the samples?"
5. **Spend more time on the problem statement than feels necessary.** Every scoring point and trap is written there somewhere.
6. **A record you never find has no check to fail.** Design checks for what your system *didn't* see, not only for what it did. Ask "how could this say COMPLETE wrongly?" before the judge does. See [§9](#9-ai-judge-interview).
7. **Rehearse the hard questions, not the explanation.** Run a mock interview that attacks your own weak spots. See [§9](#9-ai-judge-interview).

---

## 1. Before the event

- [ ] Check the start time, end time and time zone. Write down the hard deadline and your own target, which should be at least 2 hours earlier.
- [ ] **Protect your usage limits.** Go light on the coding assistant the day before, so your session window starts fresh at kickoff. Turn on paid overflow (with a cap) as a safety net.
- [ ] Check that your API account has credit for the agent's own LLM calls. These are usually billed separately from your assistant subscription.
- [ ] Start a fresh transcript or log file. Rename any practice logs so the competition log starts clean.
- [ ] Laptop charged, tools updated, terminal working (`git --version`, assistant CLI version).
- [ ] Read one or two past winners' write-ups and repos, **for structure, not to copy**.
- [ ] Join the event's Discord or forum for rule clarifications on the day.
- [ ] Keep this playbook open on a second screen.

## 2. First 30 minutes

Understand the problem yourself before the AI does, then get logging working.

1. **Read the README and problem statement yourself (15–20 min), the old-school way.** Note the inputs, every output column, the allowed values, how it's scored and anything that looks tricky. Don't ask the AI to summarise it first; you need your own model of the problem to judge its answers.
2. **Clone the repo.** Don't fork it publicly. For a backup during the event, use a new **private** repo.
3. **Create `.env`** with your API key and confirm `.env` is in `.gitignore`. The key goes only in environment variables, never in code or prompts.
4. **Open the folder in your assistant, in Plan mode** (read-only).
5. **First prompt, in your own words:** "This is the competition. Read AGENTS.md and follow its logging rules, then read the README, problem statement, scoring and sample files. No code yet. List the inputs, every output column and its allowed values, how it's scored, and the trickiest samples."
6. **Check that logging works** before moving on. Ask it to show the log file and confirm your turns are in it.
7. **Allow the routine log writes** so you aren't approving every append.

## 3. Architecture and planning

**Write your own plan first, then ask the assistant to critique it.** Don't ask "what should I do next?"

1. Write a rough plan in a few lines.
2. Prompt: "Here's my plan. Critique it: what's missing, what would break, and what would you change? Also, where does this plan use an LLM, and is that what the brief is scoring?"
3. Accept or reject each point *with a reason*. Pushing back with reasons is the strongest transcript signal.
4. Keep the plan, decisions and scores in `code/DECISIONS.md` (inside the submitted folder) and update it as you go.

### Where the LLM goes and where code goes

This is the decision that matters most. The pattern that scores well on both accuracy and design is **generator plus verifier**:

| Job | Owner | Why |
|---|---|---|
| Reading free text, policies, conditions, documents, images | **LLM** | It's language. Rules written for one dataset break on the next. |
| Deciding which rule applies to a record, and why | **LLM**, with a quoted reason | Policy conditions are usually written in plain English. |
| Investigating ambiguous or hard cases (search → read → decide) | **LLM agent with tools**, step-capped | This is what "agent" means to the judges. |
| Finding candidate evidence | Retrieval (BM25 or embeddings) as a **candidate generator** | High recall; the LLM and exact checks then filter for precision. |
| Arithmetic, dates, time cutoffs, counting | **Code** | Numbers never go through the LLM. |
| Output schema, allowed values, ordering | **Code** (validator) | Contract compliance is never left to chance. |
| Citations ("does this ID really appear in this file?") | **Code** (verifier) | Rejects confident-but-wrong answers. |
| Safety gate (injections, high-risk requests) | **Code first**, LLM second | The LLM can make a decision *more* cautious, never less. |

**Anti-pattern to avoid:** "the LLM is non-deterministic, so code decides everything". That gives you reproducibility, but you lose the AI-design points, and the rules overfit the samples. Make runs repeatable with fixed prompts, a fixed JSON schema and a reply cache (save each answer and reuse it on re-runs). Don't remove the model to get there.

### Default stack (adapt it to the problem)

| Choice | Default | Why |
|---|---|---|
| Language | Python | Has every library you'll need |
| LLM | One provider's SDK, two tiers: a cheap model for bulk, a stronger one for hard cases and images | Cost control and a clear story |
| Retrieval | BM25 (`rank_bm25`), filtered by an obvious key; add embeddings only if BM25 misses | Small, keyword-heavy corpora |
| Agent design | One agent with a few tools and a max-steps cap | Easier to defend than multi-agent; it's what most past winners used |
| Guardrails | Deterministic gate before the LLM, schema validation, bounded retries, safe default on failure | Safety is scored |
| Framework / vector DB | None | Easier to understand and explain in the interview |

Use several agents only if each has a narrow job you can defend. Trained classifiers usually overfit, because there's too little labelled data.

## 4. EDA phase (60–90 min, Plan mode)

Understand the data before any code. **You** form the rules; the assistant counts and finds patterns, and you check its claims against the files.

- **Labelled samples:** read them yourself. Write 3–5 decision rules and note the output conventions (exact spellings, when fields are blank).
- **Spot-check** 2–3 of the assistant's "tricky case" claims directly in the files.
- **The data folder:** ask for counts, formats and caveats, then open 2–3 files yourself.
- **The real inputs (no answers):** look for prompt injections, other languages, missing fields, several requests in one, and off-topic asks. These tell you which guardrails you need.
- **Every policy or rules file:** read **every column**, and write down which ones need judgement. A free-text "condition" column is an LLM job.

**Answer these in your notes before the first build prompt:**
1. What exactly is the decision, and what are the allowed output values?
2. Where does the evidence live: documents, tables, images, audio?
3. Which parts need judgement (LLM), and which have exactly one right answer (code)?
4. What's the safe default when unsure: escalate, abstain or flag?
5. What would a trap look like in this data?

**Prompting so you don't get lost:** ask for one thing at a time, ask for short answers with file and line references, and ask "explain that back in five lines" before moving on. If you can't absorb an answer, you can't defend it later.

## 5. Build loop

Build in versions, score against the samples after every change, and commit each version.

1. **v0, baseline:** an end-to-end run that produces a valid output file, plus `eval.py`. `eval.py` scores each graded column against the samples and lists every miss. Record the score.
2. **v1, LLM reasoning:** the model makes the core judgement with a fixed JSON schema; code validates it. Compare with v0. That gap is your evidence that the AI adds value.
3. **v2, tools and retrieval:** give the model tools such as `search`, `read_file`, `query_sql` and `view_image`, capped at a few calls per case. Log every tool call.
4. **v3, guardrails:** a deterministic gate before the LLM, schema validation, bounded retries, a safe default on failure and a citation verifier.
5. **Iterate on misses:** fix one category at a time. Re-run eval and check for regressions. Revert anything that makes it worse, and log why.
6. **Final run:** produce the output file and spot-check 5–10 rows by eye.

After each version, ask: "Explain what you just built in 5 lines and show me one input going through it." Then read the diff yourself.

**Write tests as you go, not at the end:** unit tests for parsers and calculations, negative tests for the validator, and property tests (for example, "an explicit 'do not delete' is never a deletion"). Re-run them before every commit.

## 6. Validation and QC gates

Treat the build like a model validation: nothing is trusted until it's checked against something you know. Record what each gate found in `code/DECISIONS.md`.

**Gate 1: Input QC (before design)**
- Row counts, columns and types match the spec, and IDs are unique.
- Count blanks, case and space variants, mixed formats, outliers and odd dates.
- Look for other languages, text addressed to the AI, duplicates, and several requests in one row.
- Count each category; the counts tell you which guardrails are worth building.

**Gate 2: Spec QC**
- Write down the exact output columns, allowed values, spellings and what a blank means.
- Check the sample answers against the spec. Where they disagree, decide which wins and log why.

**Gate 3: Component tests (each piece on its own)**
- Retrieval: for each sample, does the search find the file that holds the answer?
- Calculations: hand-checked cases, including boundaries (at a threshold, zero, rounding, time zones).
- Safety gate: planted injections (including non-English) and normal inputs. Check false positives as well as misses.
- Validator: malformed JSON, a wrong label, a missing field. Each must be rejected.
- Knockout test: blank an image or delete an identifier, and check that the conclusion that depended on it disappears.

**Gate 4: End-to-end evaluation**
- Score every graded column separately, on exactly what's graded.
- Give every miss a cause: retrieval, prompt, rule, label convention or genuinely ambiguous.
- Change one thing at a time and check for regressions.
- **Overfitting:** write 10 or more extra cases yourself, label them by hand, and report that score too. Tune parameters on data you don't score on, or run a sensitivity sweep.

**Gate 5: Output reconciliation (before every submission)**
- Rows in equals rows out, with no missing or duplicate IDs and exactly the specified columns.
- Every value comes from the allowed set.
- Every citation can be traced to a file the system actually read.
- Run twice and diff the outputs; with cached replies they should match.
- One bad row must not crash the run; failed rows go to the safe default and are logged.
- Count the API calls and the cost.

**Gate 6: Hard-code and assumption review (the gate I skipped)**
- `grep` for every constant, regex, mapping, threshold and hard-coded list. For each one, ask:
  - Where did it come from: the spec, the data, or one sample?
  - Would it survive a differently worded dataset?
  - Should it be an LLM judgement, with this rule turned into a check on that judgement?
- List every input column the code **never reads**, and justify each one.
- List every condition the code **assumes is always true**, and justify each one.
- Ask the assistant: "Show me every place where the system can report success without positive evidence."

**Edge-case checklist**

| Category | What to try |
|---|---|
| Empty or tiny | Blank, one word, greeting only |
| Huge | Very long input, one huge source file |
| Missing context | No ID, no date, unknown user |
| Conflicting evidence | Two sources disagree; a claim contradicts the records |
| Mixed intent | Several asks in one input |
| Language | Non-English, mixed languages |
| Adversarial | "Ignore your rules", instructions hidden inside data fields |
| Out of scope | Trivia, small talk |
| Boundaries | Exactly at a threshold, rounding, time zones |
| High risk | Fraud, account takeover, legal threats; the LLM must not downgrade these |
| Unseen records | A record your linking never found: what stops a false "all clear"? |
| Format drift | Prose around the JSON, a new label, a truncated reply |

**Sign-off:** list known limitations and what you didn't test, honestly. That's your answer to "where does it break?"

### Fresh-eyes subagent review

Run it twice: once when the agent works end to end, and once before submitting. Not on every commit; each review uses a lot of your limit. A subagent runs in its own context, so it isn't biased by the main session's history, and only its findings come back.

> Spawn a subagent to review `code/` with fresh eyes. Give it the problem statement and the evaluation criteria. Check: (1) secrets and env handling, (2) grounding: can any answer come from model memory instead of the evidence? (3) escalation of high-risk inputs, (4) schema validation and retries, (5) anything the README claims that the code doesn't do, (6) every hard-coded value and the assumption behind it. Rank findings by severity. Don't change any code.

- Confirm it actually ran as a separate agent, and that its turns appear in the log if the rules require that.
- **You** decide which findings to fix, and tell the main agent why. Re-run the eval after each fix.
- A clean code review isn't accuracy: only the eval score tells you the agent works.

## 7. Time plan (24-hour event, about 15 working hours)

Roughly: 15% understanding (reading plus EDA), 7% planning, 45% build and iterate, 15% review and QC, 10% docs and submission, 8% interview prep.

| Hour | Phase | What you do |
|---|---|---|
| 0–0.5 | Setup | Read the problem yourself, clone, `.env`, confirm logging |
| 0.5–2.5 | Understand | Problem statement again, Gate 1, samples, the data, your own rules |
| 2.5–3.5 | Plan | Your plan, then critique; decide where the LLM goes; `DECISIONS.md` |
| 3.5–7 | Build | v0 plus `eval.py`, then v1 LLM reasoning; tests; read the code after each version |
| — | Sleep | Have a working v0 and a list of misses before you sleep |
| 7–11 | Iterate | Tools, guardrails, misses one category at a time |
| 11–12 | Full run | Run on the real input; read the risky rows yourself |
| 12–13 | Review | Gates 5 and 6, a fresh-eyes subagent review, a security review |
| 13–14 | Docs | README and `DECISIONS.md`; one clean run from scratch |
| 14–15 | **Submit** | Once only, no resubmissions: re-check everything, but stay inside the deadline |
| After submitting | Interview prep | Pitch, decisions, reverted changes, **attack your own design** |

You can usually submit only once, so don't rush it just to finish early. In the October 2026 edition you got about 12 hours after submitting to prepare for the AI interview, so if your weekend allows, take the time to get the submission right and use that window for interview prep, not more building. Check your event's rules: the submission and interview windows can change between editions.

## 8. Prompting and transcript habits

Transcripts are usually scored on direction, technical specificity, iteration and verification, and safety awareness. Informal is fine; vague and passive is not.

**Do**
- Name files, columns, thresholds, libraries and output formats.
- Paste errors and say what you think caused them.
- Point at specific misses: "case 7 is marked complete but file X still has the email. Why?"
- Ask "why this way, and what's the alternative?" after each design choice.
- Push back when you disagree, and give the reason.
- Raise safety yourself: injections, invented facts, high-risk inputs.
- **Don't accept output blindly.** Read diffs and spot-check claims against the files.

**Don't**
- "Build the agent", "fix it", or accept everything unread.
- Ask another AI to write your prompts for the logged assistant.
- Hand-edit the transcript log. It's append-only.
- Bring code written before the event.

**Agent system prompt checklist**
- Role and task in the first two lines.
- Instructions separated from data with tags; user text is labelled as untrusted.
- Explicit rules for each decision, including when to refuse or escalate.
- A fixed JSON schema with allowed values, validated in code.
- A rule for uncertainty, plus 2–3 solved examples.
- Tool descriptions: what the tool does, when to use it and what it returns.
- Prompts stored as files (`prompts/*.md`) with a version, not buried in Python strings.

**Assistant settings**
- Use a stronger model and higher effort for planning and hard debugging, and a faster model for routine edits.
- Use Plan mode for EDA and design; allow routine edits once the design is agreed.
- Keep durable state in files (`DECISIONS.md`, `CLAUDE.md`/`AGENTS.md`), because they survive context compaction.
- Use subagents for heavy reading and fresh-eyes reviews; only their summary comes back.

## 9. AI judge interview

The interview often carries as much weight as the output. Answers that are specific, evidence-based and honest score highest.

**Usual order:** pitch → architecture and retrieval → safety and failure modes → code familiarity → evaluation → production monitoring → what's novel.

**Prepare**
- A 60-second pitch: the problem, the architecture, your score.
- Why each choice, and the alternative you rejected.
- Two or three changes you tried and reverted, with the numbers.
- How high-risk inputs and injections are handled, explained as a mechanism, not a slogan.
- **Red-team your own system for 20 minutes:**
  - "What happens to a record your system never linked?"
  - "Where did each constant come from?"
  - "What did you tune on the same data you scored on?"
  - "Could it report success without positive evidence?"
- What you designed versus what the assistant generated, and one place you overruled it.

**Speak as the owner:** "I chose… because…", "we measured… and reverted…". An honest "I'm not sure" beats a confident guess. Never claim a feature the code doesn't have.

Answer structure: direct answer → one concrete detail → tie it to the task → stop.

## 10. Submission checklist

- [ ] No API keys anywhere in the code. Secrets come only from environment variables.
- [ ] Pinned `requirements.txt`; fixed prompts; LLM replies cached so re-runs match.
- [ ] README: install, environment variables, run command, expected outputs, architecture.
- [ ] One clean run from scratch reproduces the output file.
- [ ] The output passes your validator (rows, IDs, columns, allowed values).
- [ ] The code zip contains only what's asked for: no venv, data folder, nested archives or `.env`.
- [ ] The transcript log is complete and unedited.
- [ ] Agent trace (if required): export each session, quit the assistant before copying session files, redact secrets and personal data only, zip it, open it once to check.
- [ ] You can submit only once: re-check everything above before you click submit.
- [ ] Check the submission page shows every uploaded file.
- [ ] Afterwards: push to a **new** GitHub repo, private until submissions close, then public.

## Sources and further reading

- [How I Won HackerRank Orchestrate (May 2026), Saai Syvendra](https://saai.syvendra.com/myspace/hackerrank-orchestrate)
- [saai-syvendra/hackerrank-orchestrate on GitHub](https://github.com/saai-syvendra/hackerrank-orchestrate)
- [Getting better at Orchestrate, HackerRank Blog](https://www.hackerrank.com/blog/getting-better-at-orchestrate/)
- [What 12,885 Developers Taught Us About Building with AI, HackerRank Blog](https://www.hackerrank.com/blog/what-12885-developers-taught-us-about-building-with-ai/)
- [My write-up for the October 2026 edition](../docs/article.md)
