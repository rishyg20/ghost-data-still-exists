"""Format-specific extractors that turn evidence files into normalized resource facts.

Every fact is one observation of one resource inside one evidence file:
(evidence_id, captured_at, system, resource_id, attrs). Extraction is code-only for
JSON / TXT / logs / SQL; images and PDFs are handled by ghost.vision (V1).
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field

from .data import DATASET, norm_phone

RESOURCE_RE = re.compile(r"\b((?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|legal_hold|email_delivery|cache|marketing|object|telemetry)_\d+)\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TRACE_RE = re.compile(r"\b((?:tr|rb)-[A-Za-z0-9-]+)\b")
KV_RE = re.compile(r'(\w+)=("([^"]*)"|(\S+))')
USER_RE = re.compile(r"\buser_\d+\b")

# Default owning system per resource prefix (matches system_activity target_system usage).
PREFIX_SYSTEM = {
    "account": "account_service", "search": "search_service", "analytics": "analytics_platform",
    "support": "support_platform", "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service", "tombstone": "identity_service",
    "legal_hold": "legal_service", "email_delivery": "email_delivery_service", "cache": "cache_service",
    "marketing": "marketing_platform", "object": "object_storage", "telemetry": "analytics_platform",
}


def prefix_of(resource_id):
    return resource_id.rsplit("_", 1)[0]


def system_for(resource_id):
    return PREFIX_SYSTEM.get(prefix_of(resource_id), "unknown")


@dataclass
class Fact:
    evidence_id: str
    captured_at: str
    resource_id: str
    attrs: dict
    kind: str                 # json | txt | log | sql | image | pdf
    role: str = ""            # primary | replica | "" (SQL topology)
    table: str = ""
    snippet: str = ""         # short provenance text
    idents: set = field(default_factory=set)

    @property
    def system(self):
        return system_for(self.resource_id)


def _null(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none", "[redacted]"))


def compute_idents(attrs):
    """Lowercased identifier strings appearing as values (exact) plus emails inside free text."""
    out = set()
    for v in attrs.values():
        if _null(v) or not isinstance(v, str):
            continue
        s = v.strip()
        out.add(s.lower())
        for e in EMAIL_RE.findall(s):
            out.add(e.lower())
        for u in USER_RE.findall(s):
            out.add(u)
        digits = norm_phone(s)
        if s.startswith("+") and len(digits) >= 8:
            out.add("tel:" + digits)
    return out


def _flatten(d, prefix="", out=None, own_rid=None):
    out = {} if out is None else out
    for k, v in d.items():
        key = f"{prefix}{k}".lower()
        if isinstance(v, dict):
            if "resource_id" in v and v.get("resource_id") != own_rid:
                continue
            _flatten(v, key + ".", out, own_rid)
        elif isinstance(v, list):
            if v and all(not isinstance(x, (dict, list)) for x in v):
                out[key] = " ".join(str(x) for x in v)
            else:
                out[key + ".count"] = len(v)
                for i, x in enumerate(v):
                    if isinstance(x, dict) and "resource_id" not in x:
                        _flatten(x, f"{key}.{i}.", out, own_rid)
        else:
            out[key] = v
    return out


# ---------------------------------------------------------------- JSON
def extract_json(eid, captured_at, text):
    facts = []
    data = json.loads(text)

    def walk(o, ctx):
        if isinstance(o, dict):
            ctx = dict(ctx)
            for k in ("data_store", "system", "collection"):
                if isinstance(o.get(k), str):
                    ctx[k] = o[k]
            rid = o.get("resource_id")
            if isinstance(rid, str) and RESOURCE_RE.fullmatch(rid):
                attrs = _flatten(o, own_rid=rid)
                attrs.update({f"ctx.{k}": v for k, v in ctx.items()})
                facts.append(Fact(eid, captured_at, rid, attrs, "json",
                                  snippet=json.dumps(o, default=str)[:300]))
            for k, v in o.items():
                c2 = dict(ctx)
                if isinstance(v, (dict, list)) and k.endswith(("_service", "_platform")):
                    c2["system"] = k
                walk(v, c2)
        elif isinstance(o, list):
            for x in o:
                walk(x, ctx)

    walk(data, {})
    return facts


# ---------------------------------------------------------------- TXT (pipe-separated handover lines)
def extract_txt(eid, captured_at, text):
    """Handover lines: `- ts | system | resource_id | CATEGORY | RESULT | subject`."""
    facts = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = [p.strip(" -") for p in line.split("|")]
        rids = [p for p in parts if RESOURCE_RE.fullmatch(p)]
        if not rids:
            continue
        attrs = {}
        if len(parts) >= 6:
            attrs.update(observed_at=parts[0], system=parts[1], record_type=parts[3],
                         result=parts[4], subject_reference=parts[5])
        else:
            for p in parts:
                if EMAIL_RE.fullmatch(p) or USER_RE.fullmatch(p):
                    attrs["subject_reference"] = p
        facts.append(Fact(eid, captured_at, rids[0], attrs, "txt", snippet=line.strip()[:300]))
    return facts


# ---------------------------------------------------------------- logs
def parse_log_line(line):
    line = line.replace('\\"', '"')
    attrs = {}
    brace = line.find("{")
    if brace != -1:
        try:
            obj = json.loads(line[brace:])
            if isinstance(obj, dict):
                attrs.update({str(k).lower(): v for k, v in obj.items()})
        except ValueError:
            pass
    for m in KV_RE.finditer(line):
        k = m.group(1).lower()
        v = m.group(3) if m.group(3) is not None else m.group(4)
        attrs.setdefault(k, v)
    # pipe format: ts | LEVEL | service | trace | event | ...
    if " | " in line:
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) >= 5:
            attrs.setdefault("service", parts[2])
            attrs.setdefault("event", parts[4])
    # bracket format: ts [LEVEL] [service] [trace] event: ...
    m = re.match(r"\S+ \[\w+\] \[([\w-]+)\] \[[\w-]+\] (\w+):", line)
    if m:
        attrs.setdefault("service", m.group(1))
        attrs.setdefault("event", m.group(2))
    ts = re.match(r"(\d{4}-\d\d-\d\dT[\d:]+Z)", line)
    if ts:
        attrs.setdefault("observed_at", ts.group(1))
    tr = TRACE_RE.search(line)
    return attrs, (tr.group(1) if tr else None)


def extract_log(eid, captured_at, text):
    facts = []
    traces = {}
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        attrs, trace = parse_log_line(line)
        lines.append((attrs, trace, line))
        if trace:
            traces.setdefault(trace, []).append(attrs)
    for attrs, trace, line in lines:
        rid = attrs.get("resource_id")
        if not (isinstance(rid, str) and RESOURCE_RE.fullmatch(rid)):
            continue
        merged = {}
        if trace:
            for a in traces[trace]:
                for k, v in a.items():
                    merged.setdefault(k, v)
        merged.update(attrs)
        if trace:
            merged["trace_id"] = trace
        facts.append(Fact(eid, captured_at, rid, merged, "log", snippet=line.strip()[:300]))
    return facts


# ---------------------------------------------------------------- SQL
def extract_sql(eid, captured_at, text):
    facts = []
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        for t in tables:
            if t == "snapshot_scope":
                continue
            role = "primary" if "primary" in t else ("replica" if "replica" in t else "")
            for row in con.execute(f'select * from "{t}"'):
                attrs = {k.lower(): row[k] for k in row.keys()}
                rj = attrs.pop("record_json", None)
                if rj:
                    try:
                        attrs.update(_flatten(json.loads(rj)))
                    except ValueError:
                        pass
                rid = attrs.get("resource_id")
                if isinstance(rid, str) and RESOURCE_RE.fullmatch(rid):
                    facts.append(Fact(eid, captured_at, rid, attrs, "sql", role=role, table=t,
                                      snippet=f"{t}: {rj or dict(row)}"[:300]))
    finally:
        con.close()
    return facts


EXTRACTORS = {".json": extract_json, ".txt": extract_txt, ".log": extract_log, ".sql": extract_sql}


def extract_file(manifest_row, base=DATASET):
    path = base / manifest_row["file_path"]
    ext = path.suffix.lower()
    fn = EXTRACTORS.get(ext)
    if fn is None:
        return []
    facts = fn(manifest_row["file_id"], manifest_row["captured_at"], path.read_text(encoding="utf-8"))
    for f in facts:
        f.idents = compute_idents(f.attrs)
    return facts
