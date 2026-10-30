"""Ghost Data Still Exists - deletion-audit agent.

Usage:
    python3 code/main.py                 # rules baseline + full agent run -> output.csv
    python3 code/main.py --sample        # same pipeline on the 32 samples -> code/evaluation/sample_predictions.csv
    python3 code/main.py --rules-only    # deterministic, no LLM at all (also written on every run as a safe copy)
    python3 code/main.py --offline       # no API calls; uses cached LLM replies if present
"""
import argparse
import csv
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost import audit, data, intent, llm as llm_mod, verify, vision  # noqa: E402


def run(requests_name, out_path, review_path, base=data.DATASET, llm=None, extra_facts=()):
    """Audit every request in `requests_name`. llm=None means pure rules (no vision, no LLM intent)."""
    visual_notes = []
    if llm is not None:
        vf, visual_notes = vision.extract_all_visual(data.load_manifest(base), data.load_activity(base), llm, base)
        extra_facts = list(extra_facts) + vf
    ctx = audit.Context(base, extra_facts)
    # all requests (sample + eval) compete for workflow activity, since they share one activity stream
    everything = data.load_requests("sample_deletion_requests.csv", base) + data.load_requests("deletion_requests.csv", base)
    if requests_name not in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        everything += data.load_requests(requests_name, base)
    # one classification per distinct text; LLM checks run in parallel (replies are cached on disk)
    texts = sorted({q["request_text"] for q in everything})
    with ThreadPoolExecutor(max_workers=8 if llm is not None and llm.enabled else 1) as pool:
        labels = dict(zip(texts, pool.map(lambda t: intent.combined_intent(t, llm), texts)))
    for q in everything:
        q["_user"] = ctx.ids.resolve(q["email"], at=q["requested_at"])
        q["_label"], q["_why"], q["_intent_notes"] = labels[q["request_text"]]
    workflow = ctx.assign_workflow_resources(everything)
    by_user = {}
    for q in everything:
        if q["_user"]:
            by_user.setdefault(q["_user"], []).append(q)
    targets = {q["case_id"] for q in data.load_requests(requests_name, base)}
    rows, review = [], [("-", n) for n in visual_notes]
    seen = set()
    for q in everything:
        if q["case_id"] not in targets or q["case_id"] in seen:
            continue
        seen.add(q["case_id"])
        peers = [p for p in by_user.get(q["_user"], []) if p["case_id"] != q["case_id"]]
        status, steps, notes = audit.audit_case(ctx, q, workflow, peers)
        rows.append((q["case_id"], status, json.dumps(steps, separators=(",", ":"))))
        for n in notes:
            review.append((q["case_id"], n))
    rows.sort(key=lambda r: int(r[0].split("_")[1]))
    write_csv(out_path, ["case_id", "deletion_status", "steps_json"], rows)
    write_csv(review_path, ["case_id", "reason"], review)
    return rows


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="audit the public sample requests instead")
    ap.add_argument("--rules-only", action="store_true", help="deterministic baseline, no LLM")
    ap.add_argument("--offline", action="store_true", help="no API calls; cached LLM replies only")
    args = ap.parse_args()
    root = data.REPO_ROOT
    if args.sample:
        name, final, rules_copy, review = ("sample_deletion_requests.csv", root / "code/evaluation/sample_predictions.csv",
                                           root / "code/evaluation/sample_predictions_rules.csv",
                                           root / "code/evaluation/sample_review_queue.csv")
    else:
        name, final, rules_copy, review = ("deletion_requests.csv", root / "output.csv",
                                           root / "output_v0_rules.csv", root / "review_queue.csv")
    # 1. safe deterministic baseline, always written
    run(name, rules_copy, review.with_name(review.stem + "_rules.csv"))
    if args.rules_only:
        shutil.copyfile(rules_copy, final)
        print(f"wrote {final.name} (rules only)")
        return
    # 2. full agent run; only replaces the output if it passes the contract check
    client = llm_mod.LLM(offline=args.offline)
    candidate = final.with_name(final.stem + "_candidate.csv")
    run(name, candidate, review, llm=client)
    problems = verify.validate_output(candidate, name)
    if problems:
        base_problems = verify.validate_output(rules_copy, name)
        if base_problems:
            print(f"WARNING: rules baseline also fails {len(base_problems)} checks: {base_problems[0]}")
        shutil.copyfile(rules_copy, final)
        print(f"agent output failed {len(problems)} contract checks; kept rules baseline. First: {problems[0]}")
    else:
        shutil.move(candidate, final)
        print(f"wrote {final.name} (agent run, contract valid)")
    usage = client.summary()
    (root / "code/evaluation" / ("usage_sample.json" if args.sample else "usage_full.json")).write_text(
        json.dumps({"models": usage, "llm_enabled": client.enabled, "failures": client.failures[:200],
                    "failure_count": len(client.failures)}, indent=2), encoding="utf-8")
    print(f"LLM enabled={client.enabled}  calls={sum(m['live_calls'] for m in usage.values())} live / "
          f"{sum(m['cached_calls'] for m in usage.values())} cached  failures={len(client.failures)}")


if __name__ == "__main__":
    main()
