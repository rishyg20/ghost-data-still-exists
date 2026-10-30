"""Output-contract validator and evidence-provenance checks."""
import csv
import json

from . import data
from .policy import OP_ORDER, PHASE, STATE

KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]


def validate_output(path, requests_name="deletion_requests.csv", base=data.DATASET):
    """Return a list of human-readable problems (empty list = valid)."""
    problems = []
    reqs = {q["case_id"]: q for q in data.load_requests(requests_name, base)}
    manifest = data.load_manifest(base)
    activity = {a["activity_id"]: a for a in data.load_activity(base)}
    audits = {a["audit_id"]: a for a in data.read_csv("user_audit_log.csv", base)}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        problems.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if len(rows) != len(reqs):
        problems.append(f"{len(rows)} rows, expected {len(reqs)}")
    if set(ids) != set(reqs):
        problems.append("case_id set differs from requests")
    if ids != sorted(ids, key=lambda c: int(c.split("_")[1])):
        problems.append("rows not sorted by case_id")
    for cid, status, sj in rows:
        q = reqs.get(cid)
        try:
            steps = json.loads(sj)
        except ValueError:
            problems.append(f"{cid}: steps_json is not JSON")
            continue
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            problems.append(f"{cid}: bad status {status}")
        if status == "NOT_APPLICABLE" and steps:
            problems.append(f"{cid}: NOT_APPLICABLE must have []")
        if status != "NOT_APPLICABLE" and not steps:
            problems.append(f"{cid}: {status} with no steps")
        last_phase = 0
        for i, s in enumerate(steps, 1):
            if set(s) != KEYS:
                problems.append(f"{cid}: step {i} keys {sorted(s)}")
                continue
            if s["step_no"] != i:
                problems.append(f"{cid}: step_no {s['step_no']} at position {i}")
            op = s["operation"]
            if op not in OP_ORDER or PHASE[op] != s["phase"] or STATE[op] != s["state"]:
                problems.append(f"{cid}: step {i} invalid op/phase/state {op}/{s['phase']}/{s['state']}")
                continue
            p = PHASES.index(s["phase"])
            if p < last_phase:
                problems.append(f"{cid}: phase order broken at step {i}")
            last_phase = p
            if status == "COMPLETE" and s["phase"] != "VERIFICATION":
                problems.append(f"{cid}: COMPLETE has non-verification step {i}")
            eid = s["evidence_id"]
            needs = s["phase"] == "FINDING" or status == "COMPLETE"
            if needs and not eid:
                problems.append(f"{cid}: step {i} needs evidence_id")
            if s["phase"] != "FINDING" and status == "INCOMPLETE" and eid:
                problems.append(f"{cid}: step {i} future step must have empty evidence_id")
            if eid:
                when = (manifest.get(eid, {}).get("captured_at") or activity.get(eid, {}).get("event_time")
                        or audits.get(eid, {}).get("event_time"))
                if when is None:
                    problems.append(f"{cid}: step {i} unknown evidence {eid}")
                elif q and when > q["audit_at"]:
                    problems.append(f"{cid}: step {i} cites {eid} captured after audit_at")
    return problems
