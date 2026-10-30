"""Interpret facts into resource state signals, and pick the state at audit_at."""
from .extract import _null, prefix_of

PII_KEYS = {
    "email", "phone", "full_name", "name", "display_name", "requester_name", "requester_email",
    "requester_phone", "customer_email", "owner_email", "owner_phone", "email_hash", "matched_value",
    "document.email", "document.display_name", "profile.email", "profile.full_name",
}
# For analytics records any user link means "still attributable" (rule_07/08).
ATTRIBUTION_KEYS = {"user_id", "customer_reference", "document.customer_reference"}
TRUE = {"true", "1", "yes", "enabled"}
FALSE = {"false", "0", "no", "disabled"}


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in TRUE:
            return True
        if s in FALSE:
            return False
    return None


def classify(fact):
    """Return state signals for one fact. None means 'this fact says nothing about it'."""
    a = fact.attrs
    sig = {"exists": None, "pii": None, "enabled": None, "restore": None,
           "retain_until": None, "record_type": a.get("record_type")}
    # ---- existence
    for k in ("status_code", "http_status"):
        v = str(a.get(k, ""))
        if v == "404":
            sig["exists"] = False
        elif v.startswith("2"):
            sig["exists"] = True
    rows = a.get("rows")
    if rows is not None and str(rows).isdigit():
        sig["exists"] = int(rows) > 0
    res = "" if "cache" in a else str(a.get("result", "")).upper()  # cache hit/miss is not record state
    pref = prefix_of(fact.resource_id)
    if res in ("NOT_FOUND", "MISS", "ABSENT", "DELETED"):
        sig["exists"] = False
    elif res in ("FOUND", "HIT", "IDENTIFIABLE", "ACTIVE", "PRESENT", "RETAINED", "EXPOSED"):
        sig["exists"] = True
    if "profile" in a and a["profile"] is None:
        sig["exists"] = False
    if "hits.total" in a:
        sig["exists"] = int(a["hits.total"] or 0) > 0
    mf = _bool(a.get("marker_found"))
    if mf is not None:
        sig["exists"] = not mf
    status = str(a.get("account_status", "")).upper()
    if status in ("DELETED", "PURGED", "CLOSED") and pref != "tombstone":
        sig["exists"] = False
    ap = _bool(a.get("archive_present"))
    if ap is not None:
        sig["exists"] = ap
    if fact.kind in ("sql", "json") and sig["exists"] is None:
        sig["exists"] = True  # a stored row is a present copy
    # ---- identifiers still exposed
    keys = PII_KEYS | (ATTRIBUTION_KEYS if pref in ("analytics", "telemetry") else set())
    seen = [k for k in keys if k in a]
    if seen:
        sig["pii"] = any(not _null(a[k]) for k in seen)
    for k in ("message_text", "message_excerpt"):
        if k in a and isinstance(a[k], str) and "@" in a[k]:
            sig["pii"] = True
    di = a.get("direct_identifiers")
    if di is not None and str(di).isdigit():
        sig["pii"] = int(di) > 0
    if res == "IDENTIFIABLE" or str(a.get("event", "")).endswith("pii_scan"):
        sig["pii"] = True
        sig["exists"] = True
    if res in ("REDACTED", "ANONYMIZED"):
        sig["pii"] = False
        sig["exists"] = True
    # ---- data flow
    en = _bool(a.get("enabled"))
    if en is not None:
        sig["enabled"] = en
    elif pref == "data_flow" and res in ("ACTIVE", "DISABLED", "INACTIVE"):
        sig["enabled"] = res == "ACTIVE"
    # ---- restore
    ra = _bool(a.get("restore_allowed"))
    if ra is not None:
        sig["restore"] = ra
    elif pref == "backup" and res in ("RESTORABLE", "ACTIVE", "BLOCKED", "DISABLED"):
        sig["restore"] = res in ("RESTORABLE", "ACTIVE")
    db = _bool(a.get("deletion_block"))
    if pref == "backup" and db is not None:
        sig["restore"] = not db and _bool(a.get("eligible")) is not False
    for k in ("retention_until", "retained_until", "retain_until"):
        if a.get(k):
            sig["retain_until"] = str(a[k])
    return sig


def merge(sigs):
    """Combine signals from several facts of one resource in one evidence file (any copy counts)."""
    out = {"exists": None, "pii": None, "enabled": None, "restore": None,
           "retain_until": None, "record_type": None}
    for s in sigs:
        for k in ("exists", "pii", "enabled", "restore"):
            if s[k] is True or (s[k] is False and out[k] is None):
                out[k] = s[k]
        for k in ("retain_until", "record_type"):
            out[k] = out[k] or s[k]
    return out


def state_at(facts, audit_at):
    """Latest-evidence-wins state for one resource. Returns (signals, evidence_id) or (None, None)."""
    usable = [f for f in facts if f.captured_at <= audit_at]
    if not usable:
        return None, None
    by_file = {}
    for f in usable:
        by_file.setdefault((f.captured_at, f.evidence_id), []).append(f)
    # walk from newest to oldest until a file carries a decisive signal
    for key in sorted(by_file, reverse=True):
        sig = merge(classify(f) for f in by_file[key])
        if any(sig[k] is not None for k in ("exists", "pii", "enabled", "restore")):
            return sig, key[1]
    return None, None
