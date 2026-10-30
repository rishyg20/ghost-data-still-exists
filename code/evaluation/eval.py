"""Score predictions against dataset/sample_output.csv and list every miss.

Usage:
    python3 code/evaluation/eval.py                      # runs the agent on the samples, then scores
    python3 code/evaluation/eval.py --pred FILE --gold FILE
    python3 code/evaluation/eval.py --validate output.csv
"""
import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost import verify  # noqa: E402

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
# HackerRank clarified on Discord (25 Oct) that these sample cases are not considered in evaluation:
# their gold audit records (audit_record_6645, audit_record_5569) exist in no participant-visible file.
EXCLUDED_BY_ORGANIZERS = {"case_18", "case_20"}


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def key(s):
    return tuple(s[k] for k in FIELDS)


def score(pred, gold, show=True, list_misses=True):
    m = Counter()
    miss_list = []
    for cid, (gs, gsteps) in sorted(gold.items(), key=lambda kv: int(kv[0].split("_")[1])):
        ps, psteps = pred.get(cid, ("<missing>", []))
        m["cases"] += 1
        m["status_ok"] += ps == gs
        g, p = Counter(map(key, gsteps)), Counter(map(key, psteps))
        tp = sum((g & p).values())
        m["tp"] += tp
        m["gold_steps"] += sum(g.values())
        m["pred_steps"] += sum(p.values())
        m["seq_ok"] += [key(s) for s in psteps] == [key(s) for s in gsteps]
        # field-level: positional alignment
        for i in range(max(len(gsteps), len(psteps))):
            for fld in FIELDS:
                m[f"f_total_{fld}"] += 1
                if i < len(gsteps) and i < len(psteps) and gsteps[i][fld] == psteps[i][fld]:
                    m[f"f_ok_{fld}"] += 1
        if ps != gs or g != p or [key(s) for s in psteps] != [key(s) for s in gsteps]:
            miss_list.append((cid, gs, ps, sorted(g - p), sorted(p - g)))
    prec = m["tp"] / m["pred_steps"] if m["pred_steps"] else 0.0
    rec = m["tp"] / m["gold_steps"] if m["gold_steps"] else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    res = {
        "cases": m["cases"],
        "status_accuracy": m["status_ok"] / m["cases"],
        "step_precision": prec, "step_recall": rec, "step_f1": f1,
        "sequence_accuracy": m["seq_ok"] / m["cases"],
        "field_accuracy": {fld: m[f"f_ok_{fld}"] / m[f"f_total_{fld}"] for fld in FIELDS},
    }
    if show:
        print(f"cases={res['cases']}  status_acc={res['status_accuracy']:.3f}  step P/R/F1="
              f"{prec:.3f}/{rec:.3f}/{f1:.3f}  sequence_acc={res['sequence_accuracy']:.3f}")
        print("field accuracy (positional): " + "  ".join(f"{k}={v:.3f}" for k, v in res["field_accuracy"].items()))
    if show and list_misses:
        print(f"\nmisses: {len(miss_list)}")
        for cid, gs, ps, missing, extra in miss_list:
            print(f"- {cid}: status gold={gs} pred={ps}")
            for s in missing:
                print("    MISSING ", s)
            for s in extra:
                print("    EXTRA   ", s)
            if not missing and not extra and gs == ps:
                print("    (same steps, different order)")
    return res, miss_list


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred")
    ap.add_argument("--gold", default=str(ROOT / "dataset/sample_output.csv"))
    ap.add_argument("--validate")
    ap.add_argument("--quiet", action="store_true", help="headline numbers only, no per-case misses")
    args = ap.parse_args()
    if args.validate:
        probs = verify.validate_output(args.validate)
        print("valid" if not probs else "\n".join(probs[:200]))
        sys.exit(1 if probs else 0)
    pred_path = args.pred
    if not pred_path:
        # offline: scoring never triggers paid API calls; uses cached LLM replies
        subprocess.run([sys.executable, str(ROOT / "code/main.py"), "--sample", "--offline"], check=True)
        pred_path = ROOT / "code/evaluation/sample_predictions.csv"
    gold, pred = load(args.gold), load(pred_path)
    score(pred, gold, show=True, list_misses=not args.quiet)  # all cases, every miss listed
    kept = {c: v for c, v in gold.items() if c not in EXCLUDED_BY_ORGANIZERS}
    if len(kept) != len(gold):  # secondary view: the scope HackerRank said it will grade
        print(f"\nexcluding {sorted(EXCLUDED_BY_ORGANIZERS)} (organizer clarification, not graded):")
        score(pred, kept, show=True, list_misses=False)
    probs = verify.validate_output(pred_path, "sample_deletion_requests.csv")
    print(f"\ncontract check on predictions: {'valid' if not probs else str(len(probs)) + ' problems'}")
    for p in probs[:30]:
        print("  ", p)


if __name__ == "__main__":
    main()
