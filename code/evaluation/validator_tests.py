"""Gate 3: the output validator must reject malformed outputs (negative tests).

Usage: python3 code/evaluation/validator_tests.py
"""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost import verify  # noqa: E402

GOOD = ROOT / "dataset/sample_output.csv"


def mutate(rows, fn):
    rows = [list(r) for r in rows]
    fn(rows)
    return rows


def first_steps(rows):
    return json.loads(rows[1][2])


def set_steps(rows, steps):
    rows[1][2] = json.dumps(steps)


CASES = [
    ("sample_output.csv itself is valid", lambda r: None, False),
    ("malformed JSON", lambda r: r[1].__setitem__(2, "[{not json"), True),
    ("wrong status label", lambda r: r[1].__setitem__(1, "DONE"), True),
    ("missing step field", lambda r: set_steps(r, [{k: v for k, v in s.items() if k != "evidence_id"} for s in first_steps(r)]), True),
    ("invented operation", lambda r: set_steps(r, [dict(s, operation="PURGE_RECORD") for s in first_steps(r)]), True),
    ("step_no not consecutive", lambda r: set_steps(r, [dict(s, step_no=s["step_no"] + 1) for s in first_steps(r)]), True),
    ("missing row", lambda r: r.pop(5), True),
    ("duplicate row", lambda r: r.insert(3, r[2]), True),
    ("wrong header", lambda r: r.__setitem__(0, ["case_id", "status", "steps_json"]), True),
    ("COMPLETE without evidence", lambda r: set_steps(r, [dict(s, evidence_id="") for s in first_steps(r)]), True),
    ("citation after audit_at", lambda r: set_steps(r, [dict(s, evidence_id="evidence_03") for s in first_steps(r)]), True),
    ("NOT_APPLICABLE with steps", lambda r: r[1].__setitem__(1, "NOT_APPLICABLE"), True),
]


def main():
    rows = list(csv.reader(open(GOOD, encoding="utf-8")))
    tmp = Path(tempfile.mkdtemp())
    passed = 0
    for name, fn, should_fail in CASES:
        path = tmp / "out.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(mutate(rows, fn))
        problems = verify.validate_output(path, "sample_deletion_requests.csv")
        ok = bool(problems) == should_fail
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<36} -> {'rejected: ' + problems[0] if problems else 'accepted'}")
    shutil.rmtree(tmp)
    print(f"\n{passed}/{len(CASES)} validator tests passed")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
