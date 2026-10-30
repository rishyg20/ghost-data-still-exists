"""Write code/evaluation/usage_report.md from the usage summary of the last full run.

Usage: python3 code/evaluation/usage_report.py   (after python3 code/main.py)
Token counts for cached replies are the tokens originally spent to produce them, so the report
reflects what the run that produced output.csv cost, even when re-run from cache.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost.llm import PRICES  # noqa: E402

N_REQUESTS = 518


def main():
    src = ROOT / "code/evaluation/usage_full.json"
    u = json.loads(src.read_text(encoding="utf-8"))
    models = u["models"]
    lines = ["# Usage report (final full-dataset run)", "",
             f"Run: `python3 code/main.py` over all {N_REQUESTS} requests in `dataset/deletion_requests.csv`.",
             "Provider: Anthropic (Claude API). Prices are Anthropic first-party list prices per 1M tokens.", "",
             "| Provider | Model | Purpose | Calls (live + cached) | Input tokens | Output tokens | Total tokens | Price in/out per 1M | Est. cost (USD) |",
             "|---|---|---|---|---|---|---|---|---|"]
    tot_in = tot_out = tot_calls = 0
    tot_cost = 0.0
    for model, m in sorted(models.items()):
        calls = m["live_calls"] + m["cached_calls"]
        pin, pout = PRICES.get(model, (0, 0))
        purposes = ", ".join(f"{k.split('_')[0]}×{v}" if k.startswith("vision") else f"{k}×{v}"
                             for k, v in sorted(_group(m["by_purpose"]).items()))
        lines.append(f"| Anthropic | `{model}` | {purposes} | {calls} ({m['live_calls']} + {m['cached_calls']}) | "
                     f"{m['input_tokens']:,} | {m['output_tokens']:,} | {m['input_tokens'] + m['output_tokens']:,} | "
                     f"${pin:.2f} / ${pout:.2f} | ${m['cost_usd']:.4f} |")
        tot_in += m["input_tokens"]
        tot_out += m["output_tokens"]
        tot_calls += calls
        tot_cost += m["cost_usd"]
    lines.append(f"| **Total** | | | **{tot_calls}** | **{tot_in:,}** | **{tot_out:,}** | **{tot_in + tot_out:,}** | | **${tot_cost:.4f}** |")
    lines += ["", "## Per request", "",
              f"- Average tokens per request: **{(tot_in + tot_out) / N_REQUESTS:,.1f}** "
              f"({tot_in / N_REQUESTS:,.1f} input + {tot_out / N_REQUESTS:,.1f} output)",
              f"- Average model calls per request: **{tot_calls / N_REQUESTS:.2f}**",
              f"- Estimated cost per request: **${tot_cost / N_REQUESTS:.6f}**",
              f"- Estimated total cost: **${tot_cost:.4f}**", "",
              "## How the calls are made", "",
              "- Intent: rules run first for free; Claude Haiku 5.5 double-checks each *distinct* request text "
              "(texts repeat across requests, so there are fewer intent calls than requests); Claude Sonnet 5.5 is "
              "called only when rules and Haiku disagree.",
              "- Evidence: each of the 15 images/PDFs is read once by Claude Sonnet 5.5 and cached; the result is "
              "shared by every case.",
              "- Everything else (CSV/JSON/log/SQL parsing, identity, time cutoff, policy, step ordering) is code, "
              "with zero tokens.",
              f"- Failed or unavailable LLM calls fall back to the rules: {u.get('failure_count', 0)} fallbacks in this run.",
              "- Live calls in this run vs. replies served from cache are shown per model above; cached replies are "
              "counted at the tokens originally spent producing them."]
    out = ROOT / "code/evaluation/usage_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def _group(by_purpose):
    g = {}
    for k, v in by_purpose.items():
        key = "vision" if k.startswith("vision_") else k
        g[key] = g.get(key, 0) + v
    return g


if __name__ == "__main__":
    main()
