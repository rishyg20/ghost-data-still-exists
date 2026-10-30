"""Case auditing: identity -> linked resources -> state at audit_at -> policy -> steps."""
import collections
import datetime as dt
import re

from . import data, extract, intent, policy, state

WINDOW_BEFORE = dt.timedelta(hours=3)   # workflow events cluster just before reported_at
WINDOW_AFTER = dt.timedelta(hours=1)


def _t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class Context:
    """Everything extracted once and shared by all cases."""

    def __init__(self, base=data.DATASET, extra_facts=()):
        self.ids = data.Identities(base)
        self.manifest = data.load_manifest(base)
        self.activity = data.load_activity(base)
        self.facts = []
        for row in self.manifest.values():
            self.facts.extend(extract.extract_file(row, base))
        self.facts.extend(extra_facts)
        self.by_resource = collections.defaultdict(list)
        for f in self.facts:
            self.by_resource[f.resource_id].append(f)
        # identifier token -> users (exact match only; AS1/AS2)
        self.tok2user = collections.defaultdict(set)
        for u, ident in self.ids.by_user.items():
            for tok in ident.tokens():
                self.tok2user[tok].add(u)
        # resource -> users it is directly linked to, with the proving evidence ids
        self.direct = collections.defaultdict(lambda: collections.defaultdict(set))
        for f in self.facts:
            for tok in f.idents:
                for u in self.tok2user.get(tok, ()):
                    self.direct[f.resource_id][u].add(f.evidence_id)
        self.coverage = self.coverage_snapshots()
        self.conflicts = []  # (resource_id, activity_id, user) where the record itself names someone else
        for a in self.activity:  # data-catalog identity links stated in activity details
            if a["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            rid = a["resource_id"]
            for tok in re.findall(r"[\w.+-]+@[\w.-]+\.\w+|user_\d+", a["details"]):
                for u in self.tok2user.get(tok.lower(), ()):
                    if self._content_contradicts(rid, u):
                        self.conflicts.append((rid, a["activity_id"], u))  # D8: record content wins
                        continue
                    self.direct[rid][u].add(a["activity_id"])

    def _content_contradicts(self, rid, user):
        """True if the record's own facts show emails, none of which belong to `user`."""
        emails = {t for f in self.by_resource.get(rid, []) for t in f.idents if "@" in t}
        return bool(emails) and not (emails & self.ids.by_user[user].tokens())

    def coverage_snapshots(self):
        """Snapshots that list a system's records: SQL primary/replica tables and JSON snapshot-bundle
        collections. Keyed (captured_at, evidence_id, system) -> resource ids present."""
        cov = collections.defaultdict(set)
        for f in self.facts:
            if f.kind == "sql" and f.role:
                cov[(f.captured_at, f.evidence_id, f.system)].add(f.resource_id)
            elif f.kind == "json" and f.attrs.get("ctx.data_store") == f.system:
                cov[(f.captured_at, f.evidence_id, f.system)].add(f.resource_id)
        return cov

    def absence_proof(self, rid, audit_at, not_before=""):
        """Latest snapshot in [not_before, audit_at] that covers rid's system and does not contain rid.

        not_before is the case's reported_at: a snapshot taken before the deletion was reported done
        cannot prove that the deletion happened.
        """
        best = None
        for (cap, eid, system), rids in self.coverage.items():
            if system == extract.system_for(rid) and not_before <= cap <= audit_at and rid not in rids:
                if best is None or cap > best[0]:
                    best = (cap, eid)
        return best[1] if best else None

    def assign_workflow_resources(self, requests):
        """Attribute deletion-workflow activity to cases (for records with no identifiers left).

        A resource touched by the deletion worker near a case's reported_at is a candidate for that
        case. Candidates directly linked to another user are dropped. Assignment is one-to-one per
        resource type and greedy by time distance to reported_at; a case that already owns a directly
        linked resource of the same type takes no workflow resource of that type. Leftover ties are
        flagged as ambiguous in the review queue.
        """
        case_user = {q["case_id"]: q["_user"] for q in requests if q.get("_user")}
        pairs = {}
        for a in self.activity:
            if a["source_system"] != "deletion_worker":
                continue
            t = _t(a["event_time"])
            for q in requests:
                cid = q["case_id"]
                if cid not in case_user:
                    continue
                r = _t(q["reported_at"])
                if r - WINDOW_BEFORE <= t <= r + WINDOW_AFTER:
                    d = abs((t - r).total_seconds())
                    k = (a["resource_id"], cid)
                    if d < pairs.get(k, (float("inf"),))[0]:
                        pairs[k] = (d, a["activity_id"], a["reported_result"])
        owned = set()
        for rid, users in self.direct.items():
            for cid, u in case_user.items():
                if u in users:
                    owned.add((cid, extract.prefix_of(rid)))
        taken_rid, taken_slot, out = set(), set(), collections.defaultdict(dict)
        # prefer candidates with evidence inside this case's own window (reported_at..audit_at), then any
        # evidence at all, then the closest in time
        window = {q["case_id"]: (q["reported_at"], q["audit_at"]) for q in requests}

        def seen_in_window(rid, cid):
            lo, hi = window[cid]
            return any(lo <= f.captured_at <= hi for f in self.by_resource.get(rid, []))

        ranked = sorted(pairs.items(), key=lambda kv: (not seen_in_window(*kv[0]), kv[0][0] not in self.by_resource,
                                                       kv[1][0], kv[0]))
        for (rid, cid), (d, act_id, result) in ranked:
            linked = set(self.direct.get(rid, {}))
            if linked and case_user[cid] not in linked:
                continue
            slot = (cid, extract.prefix_of(rid))
            if not linked and slot in owned:
                continue
            if rid in taken_rid or slot in taken_slot:
                continue
            ties = [c for (r2, c), v in pairs.items() if r2 == rid and v[0] == d and c != cid]
            taken_rid.add(rid)
            taken_slot.add(slot)
            out[cid][rid] = {"ambiguous": bool(ties), "activity_id": act_id, "result": result}
        return out


def resolve_intent(q, peers):
    """Final intent for one case, using the same user's other requests (D1, D4).

    peers: the user's other requests (any of their emails), each with _label set.
    Only requests filed at or before this case's audit_at are known at audit time.
    Returns (label, notes).
    """
    if "_label" in q:  # precomputed (rules + optional LLM second opinion)
        label, why, notes = q["_label"], q.get("_why", ""), list(q.get("_intent_notes", []))
    else:
        label, why = intent.classify_intent(q["request_text"])
        notes = []
    known = [p for p in peers if p["requested_at"] <= q["audit_at"]]
    if label == "TOO_SHORT":  # D1: let another request from the same user clarify it
        clear = [p for p in known if p["_label"] == "ERASURE"]
        stop = [p for p in known if p["_label"] in ("RETRACTION", "NON_ERASURE")]
        if clear and not stop:
            return "ERASURE", [f"short request clarified by {clear[0]['case_id']}"]
        return "NOT_APPLICABLE", [f"intent {label}: {why}"]
    if label == "ERASURE":  # D4: a later retraction filed before audit_at withdraws it
        withdrawn = [p for p in known if p["_label"] == "RETRACTION" and p["requested_at"] >= q["requested_at"]]
        if withdrawn:
            return "NOT_APPLICABLE", [f"deletion withdrawn by {withdrawn[0]['case_id']} before audit_at"]
        notes += [f"{flag}" for flag in intent.risk_flags(q["request_text"])]  # D3, D5
        return "ERASURE", notes
    if label != "NON_ERASURE":
        notes.append(f"intent {label}: {why}")
    return "NOT_APPLICABLE", notes


def audit_case(ctx, q, workflow, peers=()):
    """Return (deletion_status, steps, notes) for one request row (q has _user resolved)."""
    label, notes = resolve_intent(q, peers)
    q["_intent"] = label
    if label != "ERASURE":
        return "NOT_APPLICABLE", [], notes
    user = q.get("_user")
    if not user:
        notes.append("identity not resolvable from request email")
        return "NOT_APPLICABLE", [], notes
    # D10: an email in the text that is not the requester's own (current or historical) may be someone
    # else's account -> don't act; a human confirms with both the requester and that account holder
    own = ctx.ids.by_user[user].tokens()
    others = sorted({e.lower() for e in extract.EMAIL_RE.findall(q["request_text"])} - own)
    if others:
        whose = [ctx.ids.resolve(e, at=q["requested_at"]) or "no user on record" for e in others]
        notes.append("request names an email that is not the requester's (" +
                     ", ".join(f"{e} -> {w}" for e, w in zip(others, whose)) +
                     "); confirm with both account holders before acting")
        return "NOT_APPLICABLE", [], notes
    audit_at = q["audit_at"]
    for rid, act, u in ctx.conflicts:
        if u == user:
            notes.append(f"{rid}: {act} links it to {u} but the record names someone else; not linked")
    wf = workflow.get(q["case_id"], {})
    resources = {rid: "direct" for rid, users in ctx.direct.items() if user in users}
    # one primary account per case: prefer the account the workflow handled for this request
    wf_accounts = [r for r in wf if r.startswith("account_")]
    if wf_accounts:
        for rid in [r for r in resources if r.startswith("account_") and r not in wf_accounts]:
            del resources[rid]
    for rid, info in wf.items():
        resources.setdefault(rid, "workflow")
        if info["ambiguous"]:
            notes.append(f"{rid} attributed by workflow timing; another case is equally close")
    # the same user's earlier request (already audited before this one was filed) owns older evidence
    earlier = [p for p in peers if p["audit_at"] < q["requested_at"]]
    last_earlier_audit = max((p["audit_at"] for p in earlier), default="")
    planned, later_only = [], []
    for rid, how in sorted(resources.items()):
        facts = ctx.by_resource.get(rid, [])
        usable = [f for f in facts if f.captured_at <= audit_at]
        if how == "direct" and usable and max(f.captured_at for f in usable) <= last_earlier_audit:
            owner = max(earlier, key=lambda p: p["audit_at"])["case_id"]
            notes.append(f"{rid}: evidence only from {owner}'s earlier episode; not re-reported")
            continue
        if not usable and how == "direct":
            if facts:
                later_only.append(rid)  # known only from evidence captured after audit_at
            continue
        sig, eid = state.state_at(facts, audit_at)
        if sig:
            F, R, V = policy.plan_resource(rid, sig, eid, audit_at, [f.attrs for f in usable])
        else:
            F, R, V = [], [], _verify_only(rid)
            eid = ctx.absence_proof(rid, audit_at, q["reported_at"]) if V == ["VERIFY_RECORD_ABSENT"] else None
        planned += [(op, rid, eid) for op in F] + [(op, rid, "") for op in R] + [(op, rid, eid or "") for op in V]
    complete = not any(policy.PHASE[op] == "FINDING" for op, _, _ in planned)
    if complete:  # COMPLETE steps must be evidence-backed
        kept = []
        for op, rid, eid in planned:
            if not eid and rid in wf:
                if rid.startswith("account_"):
                    # D9: only the workflow's own claim says it is gone -> not proven, not COMPLETE
                    complete = False
                    notes.append(f"{rid}: absence not proven by any readback; case left INCOMPLETE")
                else:
                    notes.append(f"{rid} dropped: no evidence at or before audit_at")
                    continue
            kept.append((op, rid, eid))
        planned = kept
    if not complete:  # future verification may name resources first seen in later evidence
        planned += [(op, rid, "") for rid in later_only for op in _verify_only(rid)]
    steps = policy.build_steps(planned, complete)
    if not steps:  # nothing evidenced for this user: flag instead of emitting an invalid empty audit
        notes.append("no evidence linked to this user; cannot audit (flagged, not guessed)")
        return "NOT_APPLICABLE", [], notes
    if complete and any(not s["evidence_id"] for s in steps):
        # never claim COMPLETE without proof for every check: keep the checks as future work
        notes.append("COMPLETE not proven for every check; case left INCOMPLETE")
        steps = policy.build_steps(planned, False)
        complete = False
    return ("COMPLETE" if complete else "INCOMPLETE"), steps, notes


def _verify_only(rid):
    cat = policy.category_of(rid, None)
    act = policy.ACTION.get(cat)
    if cat == "DATA_FLOW":
        return ["VERIFY_DATA_FLOW_DISABLED"]
    if cat == "BACKUP_ARCHIVE":
        return ["VERIFY_RESTORE_BLOCKED"]
    if act == "RETAIN":
        return ["VERIFY_RECORD_PRESENT"]
    if act in ("REDACT", "ANONYMIZE"):
        return [{"REDACT": "VERIFY_RECORD_REDACTED", "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED"}[act]]
    return ["VERIFY_RECORD_ABSENT"]
