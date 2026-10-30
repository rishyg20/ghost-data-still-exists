# Ghost Data Still Exists: my first hackathon, what I built, and what I'd do differently

*HackerRank Orchestrate, October 2026 · Rank **375 of 4,090** (top 10%) · [Code](../README.md) · [Playbook](../playbook/ai-hackathon-playbook.md)*

This was my first hackathon: 24 hours, one problem, and a coding assistant as a pair programmer. It was a thrilling day. I finished in the top 10%. I'm still a little disappointed, because I went in well prepared, and the points I lost came from a design decision I could have caught. This article covers what I built, how it scored, and the technical lesson I'm taking into the next one.

## The problem

When a company says "your data has been deleted", is it really gone?

The challenge was to build an **AI-powered deletion-audit agent** for a fictional commerce platform. It got 518 customer requests. For each one, the agent had to:

1. decide whether the customer actually asked for deletion (requests came in several languages, and some only *looked* like deletion requests);
2. rebuild every identifier the person ever had: current and old emails, their sha256 hashes, phone numbers;
3. search databases, replicas, logs, exports, analytics, backups, screenshots and PDFs for that person's data;
4. work out the true state of every copy **at a specific audit time**, using only evidence captured before it;
5. apply a deletion policy (delete, redact, anonymize, retain, or keep an account tombstone);
6. output what's left, an ordered remediation plan, and the checks that prove it's done.

The twist is in the name. A workflow can report **COMPLETE** while copies survive in a replica, a backup or an export. Sync jobs and backup restores can even bring deleted data back.

## Thinking from first principles

Working in risk modelling, on both the validation and development side, helped me more than anything else. The same principles carry over: understand the problem before the model, explore the data first, hunt for the sensitive edge cases, and test and validate everything before trusting it. This was a validation problem at heart: a system makes a claim ("deleted"), and you need independent evidence for or against it. Here's how I applied them:

- **Understand the problem before the data, and the data before the model.** I read the problem statement myself, then spent the first stretch on EDA: counting files, reading samples, opening evidence files and writing down traps. Only then did I let the assistant design anything. The notes are in [`eda-notes.md`](eda-notes.md).
- **Brainstorm the edge cases before building.** I spent my first couple of hours listing traps on my own, not just the ones in the 518 requests but ones that could appear later: blank or one-word requests, blank or unreadable images, other languages, "delete my invoice copy" vs "delete my account", a deletion followed by "never mind", someone asking on another person's behalf, emails that differ by one dot. Each one became a design decision (see the [decision log](../code/DECISIONS.md)) so the architecture handled it by construction instead of by patching.
- **AI output must be evidence-backed.** Every finding cites a file the code actually parsed, captured at or before the audit time. A blank image produces no facts, so it can't be cited, and a knockout test proves this. An answer with no evidence behind it goes to review instead of being guessed.
- **A claim is not evidence.** A reported status, a job's "SUCCESS" or an old snapshot is a claim. Only a readback captured after the action and before the audit proves a state.
- **Benchmark, then challenge.** I built a rules-only baseline first, so every later change had a number to beat.
- **Test the components, then the whole.** The project has unit tests, property tests, negative tests for the output validator, 15 hand-made custom cases and a knockout test (blank an image and check that its finding disappears).
- **Don't accept output blindly**, including the assistant's. I prompted step by step and asked for short answers I could absorb and check against the files. If I couldn't explain an answer back, I didn't move on.

## The key finding

**A reported status is a claim, not proof.** In the evaluation set, **168 of the 254 requests reported as COMPLETE still had data left somewhere.**

Two time rules mattered as much as matching:
- **Only evidence captured at or before the audit time counts.** Anything later can't change the answer.
- **Old evidence can prove data still exists, but never that it's gone.** A snapshot from before the deletion request can't prove the deletion happened.

## What I built

A single Python pipeline of about 1,500 lines:

```
intent     → multilingual rules, Claude Haiku 5.5 second opinion, Claude Sonnet 5.5 tie-break
identity   → current + historical emails, sha256 hashes, phones (exact matching only)
evidence   → 94 files → normalized "facts" (one record, seen in one file, at one time)
             JSON / logs / SQL snapshots parsed by code;
             screenshots and PDFs read by Claude Sonnet into a strict JSON schema
state      → latest evidence at or before audit_at decides each copy's state
policy     → deletion_rules.csv → findings, remediation, verification, in contract order
safety     → schema validation, retry, cache, fallback, contract validator, review queue
```

Parts I'm still happy with:
- **Exact identifier matching.** The dataset had decoy emails one dot or suffix away from the real one. Exact matching meant no wrong-person links.
- **Linking records with no identifiers left.** A deleted account or anonymized row has no email to match. I linked those through the timing of the deletion job's activity, using one-to-one assignment, and flagged every tie for a human.
- **A cautious-only LLM.** Claude could recognise a deletion request in wording the rules missed. It could never overrule a safety flag such as "this is someone else's account" or "possible prompt injection".
- **Flag, don't guess.** Ambiguous, third-party, too-short or withdrawn requests go to a review queue instead of being silently decided.
- **Cost:** 416 Claude calls and about 392k tokens for all 518 requests, **about $0.30 in total**. Cached replies made re-runs free and byte-identical.

## Results

On the 32 labelled sample cases:

| Metric | Score |
|---|---|
| Status accuracy | **100%** |
| Step F1 (each step as an exact 6-field match) | **0.93** |
| Exact case match (every step, in order) | 56% |

Competition scores:

| Part | Score |
|---|---|
| Output CSV | **26.1 / 30** |
| Chat transcript | 8.0 / 10 |
| Code zip | 19.2 / 30 |
| AI judge interview | 15.3 / 30 |
| **Total** | **68.6 / 100** · rank 375 of 4,090 |

The output scored 87%. The points I lost came almost entirely from the code review and the interview, which both judge the *approach*.

## Where I could have done better

### 1. I put the deterministic code in the wrong place

The brief asked for an **AI-powered agent**. I optimised for correctness and reproducibility, so the LLM did only two jobs: reading request wording and reading images. Everything else was regexes, field-name lists and hand-written mappings.

Technically, the problem is not that I used deterministic code. It's *where* I used it. There are two ways to combine an LLM with rules:

| | Rules as the decision engine (what I built) | Rules as verifiers of the LLM (what I should have built) |
|---|---|---|
| Who decides | Code, using mappings tuned on the samples | The LLM, reasoning over the policy and evidence |
| What code does | Everything | Checks every claim: schema, allowed values, time cutoff, "this ID really is in this file", "this rule's condition was evaluated" |
| On new data | Brittle: a new field name or wording silently fails | Generalises; the checks catch confident-but-wrong answers |
| Reproducibility | By construction | Temperature 0, a fixed schema and a reply cache give the same answer every run |
| What judges see | A rules engine with AI on the side | An agent with guardrails |

From what I saw in discussions after the event, I wasn't alone. Many participants built fully deterministic pipelines, and those tended to score well on output but lose points on design. Determinism is a property of *how you run* a system: fixed prompts, temperature 0, caching and validation. It isn't a reason to take the model out of the decision.

### 2. Hard-coded values I didn't review

The clearest example: `deletion_rules.csv` has a plain-English `condition` column. My code read only `data_category → required_action` and never looked at the conditions:

| Rule | Condition | What my code did |
|---|---|---|
| Security audit log | "within the supplied retention period" | Coded by hand (retention date check) |
| Analytics / telemetry | "still attributable to a user" | Coded by hand (a fixed list of linking fields) |
| Legal hold | "a supplied legal hold is active" | Only partly: a date check, not whether the hold is active |
| Support message | "contains shared customer, agent or order context" | **Assumed always true**: always redacted |
| Support attachment | "belongs only to the deletion subject" | **Assumed always true**: always deleted |
| Backup | "restore access can be blocked" | **Assumed always true** |
| Account tombstone | "account deleted, only the user ID is needed" | **Assumed always true** |

It cost almost nothing on this dataset, because the data mostly fitted those assumptions. But it's exactly the input an LLM should reason over: "here's the record, here's the condition, does it apply? Quote the field that proves it." A deterministic check would then confirm that the quoted field exists. I didn't review the hard-coding closely enough to see this, and I accepted the design without asking "which columns does the code never read?"

Other places an LLM should have done the work, with code checking it:
- **Record state:** I wrote a 72-line function full of dataset-specific field names. An LLM turning each record into a fixed schema, quoting the field it used, would handle formats it has never seen.
- **An investigation agent for hard cases**, with tools such as `grep_exact`, `read_file`, `query_sql` and `view_image`. I planned it but didn't build it.
- **Retrieval as a candidate generator.** I rejected BM25 and embeddings because they pull in decoys. Used for *recall*, with Claude and exact identifiers deciding the *link*, they would have closed my biggest blind spot.
- **A checker pass:** "Does every cited file really show this? Is any account missing?"

### 3. A record you never link has no check to fail

My safeguards protected everything the agent *knew* about. Every COMPLETE check needed fresh evidence, and a deletion job's own "success" wasn't enough. But a record that was never linked was invisible. One sample case was correct only because the account it never found really had been deleted. Two cheap checks would have caught it:
- **"No account linked" flag:** every user has an account, so a COMPLETE case with no account linked goes to review.
- **Unassigned-activity sweep:** flag any record the deletion job touched that ended up assigned to no case. In my data that was 4 accounts, 2 of them only partly deleted.

### 4. Interview prep explained the design instead of attacking it

I prepared clear explanations. The interviewer asked how the system fails: "What happens to a record outside your time window? Could it be marked COMPLETE?" I should have red-teamed my own design before the real interview.

### 5. Smaller things

- Prompts lived inside Python strings instead of versioned `prompts/` files.
- I chose the timing window (3 hours before, 1 hour after) on the same samples I scored on, without a sensitivity sweep.
- A few long functions, and no type hints.

## What I'd keep

- **Reading the problem and the data myself before prompting.** That old-school hands-on work is what let me judge the assistant's answers.
- **Baseline first, measure every change.** One "fix" briefly lowered accuracy, and I only knew because I measured.
- **Safety by design.** Customer text can never change the policy, a wrong deletion counts as the worst error, and uncertainty is visible in a review queue.
- **A fresh-eyes review.** An independent subagent review found two high-severity and six medium-severity issues I'd missed.

## Lessons

1. **Read the brief as a scoring rubric.** "AI-powered agent", "retrieval" and "multimodal" tell you what the judges score.
2. **LLM proposes, code verifies.** Put deterministic rules around the model as guardrails, not in place of it.
3. **Review every hard-coded value.** Ask which input columns are never read, and which conditions are assumed to be true.
4. **Spend more time on the problem statement than feels necessary**, and brainstorm the edge cases yourself before the first line of code.
5. **Every AI output must be evidence-backed.** Cite what was read, check the citation in code, and route anything unbacked to a human.
6. **Design for what you can't see.** Ask "how could this say COMPLETE wrongly?" before someone else does.
7. **Prompt so you can absorb the answer.** Be specific, go step by step, and never accept output you can't explain.

I turned all of this into a reusable [hackathon playbook](../playbook/ai-hackathon-playbook.md) for anyone preparing for their first event. It covers setup, EDA, architecture, the build loop, validation gates (including the hard-code review I skipped), the interview and submission.

*Built with Python, Claude Code as pair programmer, and Claude Haiku 5.5 and Sonnet 5.5 inside the agent.*
