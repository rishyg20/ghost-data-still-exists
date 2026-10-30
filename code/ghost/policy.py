"""Map resources to deletion_rules.csv categories and turn state into ordered steps."""
from .data import load_rules
from .extract import prefix_of, system_for

DEFAULT_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "analytics": "ANALYTICS_PROFILE",
    "telemetry": "PRODUCT_TELEMETRY", "support": "SUPPORT_MESSAGE", "export": "USER_EXPORT",
    "cache": "CACHE_ENTRY", "marketing": "MARKETING_PROFILE", "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit_record": "SECURITY_AUDIT_LOG", "legal_hold": "LEGAL_HOLD_RECORD", "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE", "data_flow": "DATA_FLOW", "object": "SUPPORT_ATTACHMENT",
}
# required_action per data category, read from dataset/deletion_rules.csv (single source of truth)
ACTION = {cat: row["required_action"] for cat, row in load_rules().items()}

OP_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE = {op: ("FINDING" if op.startswith("REPORT") else "VERIFICATION" if op.startswith("VERIFY") else "REMEDIATION")
         for op in OP_ORDER}
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}


def category_of(resource_id, sig, attrs_seen=()):
    rt = (sig or {}).get("record_type")
    if rt and rt in ACTION:
        return rt
    pref = prefix_of(resource_id)
    # contact-lookup copies of identifiers (legacy contact index, opt-in lookups) are secondary identifiers
    for a in attrs_seen:
        ev = str(a.get("event", ""))
        if ev.startswith(("contact_lookup", "contact_query")):
            return "SECONDARY_IDENTIFIER"
    return DEFAULT_CATEGORY.get(pref, "USER_PROFILE")


def plan_resource(resource_id, sig, evidence_id, audit_at, attrs_seen=()):
    """Return (findings, remediations, verifications) as lists of (op, resource_id, evidence_id)."""
    cat = category_of(resource_id, sig, attrs_seen)
    exists = True if sig is None or sig["exists"] is None else sig["exists"]
    F, R, V = [], [], []
    if cat == "DATA_FLOW":
        if sig and sig["enabled"]:
            F.append("REPORT_ACTIVE_DATA_FLOW"); R.append("DISABLE_DATA_FLOW")
        V.append("VERIFY_DATA_FLOW_DISABLED")
    elif cat == "BACKUP_ARCHIVE":
        # restore must be shown blocked; an existing backup with unknown restore access is a risk
        if sig and (sig["restore"] or (sig["restore"] is None and sig["exists"])):
            F.append("REPORT_RESTORE_RISK"); R.append("BLOCK_RESTORE")
        V.append("VERIFY_RESTORE_BLOCKED")
    elif ACTION.get(cat) == "RETAIN":
        ru = sig.get("retain_until") if sig else None
        if exists and (not ru or ru > audit_at):
            V.append("VERIFY_RECORD_PRESENT")
        elif exists:  # retention lapsed: no longer protected, treat as deletable
            F.append("REPORT_REMAINING_RECORD"); R.append("DELETE_RECORD"); V.append("VERIFY_RECORD_ABSENT")
    elif ACTION.get(cat) in ("REDACT", "ANONYMIZE"):
        verb = ACTION[cat]
        if not exists:
            V.append("VERIFY_RECORD_ABSENT")
        elif sig and sig["pii"]:
            F.append("REPORT_EXPOSED_PII"); R.append(f"{verb}_RECORD"); V.append({"REDACT": "VERIFY_RECORD_REDACTED", "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED"}[verb])
        else:
            V.append({"REDACT": "VERIFY_RECORD_REDACTED", "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED"}[verb])
    else:  # DELETE
        if exists:
            F.append("REPORT_REMAINING_RECORD"); R.append("DELETE_RECORD")
        V.append("VERIFY_RECORD_ABSENT")
    return F, R, V


def build_steps(planned, complete):
    """planned: list of (op, resource_id, evidence_id). Sort and number per the contract."""
    rows = []
    for op, rid, eid in planned:
        phase = PHASE[op]
        cite = eid if (phase == "FINDING" or complete) else ""
        rows.append((OP_ORDER.index(op), system_for(rid), rid, op, cite))
    rows = sorted(set(rows), key=lambda r: (["FINDING", "REMEDIATION", "VERIFICATION"].index(PHASE[r[3]]), r[0], r[1], r[2]))
    return [{"step_no": i, "phase": PHASE[op], "system_id": sys_id, "resource_id": rid,
             "operation": op, "state": STATE[op], "evidence_id": cite or ""}
            for i, (_, sys_id, rid, op, cite) in enumerate(rows, 1)]
