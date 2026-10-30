# Decisions, validation gates and scores

## Plan (agreed before code)
One pipeline, split by file format. Code owns everything exact (parsing, identity, time cutoff, policy, ordering).
Claude is used only for judgement on messy input: request intent (Haiku 5.5, Sonnet 5.5 tie-break) and reading
images/PDFs (Sonnet 5.5). Every LLM reply is schema-validated, cached and has a rules fallback.

Rejected alternatives:
- **BM25/embeddings retrieval**: evidence links through exact identifiers; similarity search pulls in decoys
  (`g.silva76@` vs `g.silva76.jr@`). An exact identifier index over 94 files is precise and explainable.
- **Multi-agent / per-case agent loop**: 518 × 94 file reads would be slow, costly and non-deterministic.
  Each file is extracted once.
- **LLM deciding status or steps**: steps must follow `deletion_rules.csv` exactly; code does it deterministically.

## Decisions on edge cases
| # | Case | Decision |
|---|---|---|
| D1 | Too short to know what to delete ("delete") | another request from the same user filed ≤ audit_at may clarify it; else NOT_APPLICABLE + review |
| D2 | "Up to you, you can delete it, I don't care" | NOT_APPLICABLE + review (permission is not a request) |
| D3 | Account takeover / legal threat | audit normally + review flag |
| D4 | Deletion then retraction by the same user | retraction filed ≤ deletion case's audit_at cancels it; later ones ignored |
| D5 | "Ignore your rules / don't keep backups" | steps still from deletion_rules.csv; review flag |
| D6 | Workflow-timing tie between cases | guess (evidence-backed first) + flag |
| D7 | Ownership seen only in evidence after audit_at | allowed for identity only; citations ≤ audit_at |
| D8 | Catalog link contradicts the record's content (marketing_001) | record content wins; not linked; flagged |
| D9 | Absence backed only by the deletion worker's own SUCCESS | not COMPLETE; future verification + flag |
| D10 | Request for someone else (friend, deceased, on behalf of) or naming an email that is not the requester's | NOT_APPLICABLE + review: a human confirms with both people (email or call) before acting |

Assumptions: emails compared after lowercase/trim only (dots and +tags matter); no fuzzy matching; names never link
alone; `email_hash = sha256(lowercase email)`; UTC; evidence usable only if `captured_at ≤ audit_at`; latest
observation per copy wins; every replica checked separately; readbacks beat activity claims; a backup without
proven blocked restore is a risk; retention honoured only while a concrete record's date is after audit_at.

## Validation gates (playbook)
| Gate | Check | Result |
|---|---|---|
| 1 Input QC | rows/columns, unique IDs, blanks, case/space variants, languages, injections, duplicates | 518 rows, unique IDs, 0 blank texts/emails, 20 upper-case and 95 historical emails, 62 non-English (pt/es/fr/ko/ja), 10 non-deletion, 3 users with 2 requests, 0 injections, 375 distinct texts |
| 2 Spec QC | output columns, labels, step vocabulary vs samples | sample ordering = phase → operation table order → system_id → resource_id; INCOMPLETE verification also re-checks compliant records; no conflicts with the spec |
| 3 Components | validator negatives; intent safety gate incl. non-English; vision knockout | `validator_tests.py` 12/12; `custom_cases.py` 15/15; `knockout.py` blank + erased image both remove the finding |
| 4 End-to-end | per-field scores on samples; every miss classified | see scores below and `evaluation/evaluation_report.md` |
| 5 Reconciliation | contract check; run twice and diff; similar inputs → similar decisions; failures to safe default; cost | valid; two runs and an offline run byte-identical; 1 text group differs (case_63, evidence conflict D8, explained); LLM failures fall back to rules; rules-only `output_v0_rules.csv` always written; 416 calls, $0.30 |

## Scores (32 samples)
| Version | Status | Step P / R / F1 | Sequence |
|---|---|---|---|
| V0-initial (rules) | 0.688 | 0.778 / 0.674 / 0.722 | 0.156 |
| V0 (rules, fixed) | 0.969 | 0.926 / 0.907 / 0.916 | 0.531 |
| V1 | 1.000 | 0.927 / 0.927 / 0.927 | 0.531 |
| V1 + fix #4 only | 0.969 | 0.922 / 0.917 / 0.919 | 0.562 |
| V1.1 (final, after fresh-eyes review) | 1.000 | 0.932 / 0.927 / 0.930 | 0.562 |

Organizer clarification (Discord, 25 Oct): case_18 and case_20 are excluded from evaluation. On the 30 counted samples:
rules-only 0.967 / F1 0.926 / seq 0.567; V1.1 1.000 / F1 0.942 / seq 0.600. The pipeline is unchanged by this; only the
eval scope.

Tests: 15 scenario cases, 12 validator negatives, 19 unit + property tests, image knockout, reproducibility diff.

## Changes tried and reverted / replaced (with numbers)
- Workflow window `[requested_at, reported_at + H]`: 0–1 of 33 unlinked sample resources resolved uniquely. Replaced by
  `[reported_at − 3h, reported_at + 1h]` + one-to-one greedy assignment, which fixed most account/analytics links.
- Bare `remove` and `copy of` / `download my` as intent cues: misclassified 6 sample deletions as NOT_APPLICABLE
  (status 0.688). Narrowed: status 0.969.
- COMPLETE verified by citing the deletion worker's own SUCCESS activity: replaced by D9 (not proven → INCOMPLETE + flag).
- Cache-lookup `result=miss` read as record state: made security records "absent". Removed.

## Fresh-eyes review
An independent subagent reviewed code/ (secrets, grounding, high-risk escalation, schema validation, README claims).
No secret leaks. Fixed: LLM can no longer upgrade a flagged request to ERASURE; LLM risk flags kept, injection is a
hard stop; image retention dates parsed to ISO; absence proofs must post-date reported_at (and JSON snapshot bundles
count); user-reference aliases restricted to the resource's own system; uncited COMPLETE → INCOMPLETE; eval.py runs
offline; reports packaged at evaluation/. Fix #4 alone dropped sample status to 0.969 (case_31 lost a weak proof for a
mis-attributed account); adding JSON snapshot coverage restored 1.000 and raised F1 to 0.930. Not fixed (documented):
within-phase order not validated, usage report counts 26 sample-only intent calls, cached replies not re-validated.

## Known limitations (where it breaks)
- Same-`reported_at` cases: deleted accounts / anonymized rows cannot be told apart; guessed + flagged.
- 3 sample gold resources exist in no participant-visible file.
- Rules alone miss unseen phrasings in new languages; V1's Haiku/Sonnet check covers these, offline mode does not.
- Vision reads each file once (no second independent read yet).
