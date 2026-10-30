# Usage report (final full-dataset run)

Run: `python3 code/main.py` over all 518 requests in `dataset/deletion_requests.csv`.
Provider: Anthropic (Claude API). Prices are Anthropic first-party list prices per 1M tokens.

| Provider | Model | Purpose | Calls (live + cached) | Input tokens | Output tokens | Total tokens | Price in/out per 1M | Est. cost (USD) |
|---|---|---|---|---|---|---|---|---|
| Anthropic | `claude-haiku-5-5` | intent×398 | 398 (0 + 398) | 277,065 | 34,402 | 311,467 | $0.10 / $0.50 | $0.0449 |
| Anthropic | `claude-sonnet-5-5` | intent×3, vision×15 | 18 (0 + 18) | 69,777 | 11,140 | 80,917 | $2.00 / $10.00 | $0.2510 |
| **Total** | | | **416** | **346,842** | **45,542** | **392,384** | | **$0.2959** |

## Per request

- Average tokens per request: **757.5** (669.6 input + 87.9 output)
- Average model calls per request: **0.80**
- Estimated cost per request: **$0.000571**
- Estimated total cost: **$0.2959**

## How the calls are made

- Intent: rules run first for free; Claude Haiku 5.5 double-checks each *distinct* request text (texts repeat across requests, so there are fewer intent calls than requests); Claude Sonnet 5.5 is called only when rules and Haiku disagree.
- Evidence: each of the 15 images/PDFs is read once by Claude Sonnet 5.5 and cached; the result is shared by every case.
- Everything else (CSV/JSON/log/SQL parsing, identity, time cutoff, policy, step ordering) is code, with zero tokens.
- Failed or unavailable LLM calls fall back to the rules: 0 fallbacks in this run.
- Live calls in this run vs. replies served from cache are shown per model above; cached replies are counted at the tokens originally spent producing them.
