"""Loading of the participant-facing CSV files and identity resolution."""
import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset"


def read_csv(name, base=DATASET):
    with open(base / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def norm_email(s):
    return (s or "").strip().lower()


def norm_phone(s):
    return re.sub(r"\D", "", s or "")


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class Identity:
    user_id: str
    name: str
    emails: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    phones: set = field(default_factory=set)  # digits only
    audit_ids: list = field(default_factory=list)

    def tokens(self):
        """Exact-match identifier strings (lowercased); phones are prefixed "tel:" + digits.

        Names are deliberately excluded: different people share names, so a name alone
        would link decoys (AS2).
        """
        return {self.user_id} | self.emails | self.hashes | {"tel:" + p for p in self.phones if p}


class Identities:
    def __init__(self, base=DATASET):
        self.by_user = {}
        # email -> [(user_id, valid_from, valid_to)]; "" means open-ended
        self.email_periods = {}
        self.current_email = {}
        for r in read_csv("user_identities.csv", base):
            self.current_email[r["user_id"]] = norm_email(r["email"])
            ident = Identity(r["user_id"], r["name"])
            ident.emails.add(norm_email(r["email"]))
            ident.hashes.add(r["email_hash"].lower())
            ident.phones.add(norm_phone(r["phone"]))
            self.by_user[r["user_id"]] = ident
        changes = []
        for a in read_csv("user_audit_log.csv", base):
            ident = self.by_user.get(a["user_id"])
            if ident is None:
                continue
            ident.audit_ids.append(a["audit_id"])
            f = a["field_name"]
            for v in (a["old_value"], a["new_value"]):
                if not v:
                    continue
                if f == "email":
                    ident.emails.add(norm_email(v))
                    ident.hashes.add(sha256(norm_email(v)))
                elif f == "email_hash":
                    ident.hashes.add(v.lower())
                elif f == "phone":
                    ident.phones.add(norm_phone(v))
            if f == "email":
                # old value was valid until this change, new value from it onwards
                changes.append((a["user_id"], a["event_time"], norm_email(a["old_value"]), norm_email(a["new_value"])))
        self.email_index = {}
        for ident in self.by_user.values():
            for e in ident.emails:
                self.email_index.setdefault(e, set()).add(ident.user_id)
        self._build_email_periods(changes)

    def _build_email_periods(self, changes):
        """Ownership windows per email, so a reused address resolves to whoever held it then."""
        starts = {}
        for user_id, when, old, new in sorted(changes, key=lambda c: c[1]):
            if old:  # closed period: from when it started (or the beginning) until this change
                self.email_periods.setdefault(old, []).append((user_id, starts.pop((user_id, old), ""), when))
            if new:
                starts[(user_id, new)] = when
        for (user_id, email), start in starts.items():  # changed-to addresses still held
            self.email_periods.setdefault(email, []).append((user_id, start, ""))
        for user_id, email in self.current_email.items():  # never-changed current addresses
            if not any(p[0] == user_id for p in self.email_periods.get(email, [])):
                self.email_periods.setdefault(email, []).append((user_id, "", ""))

    def resolve(self, email, at=None):
        """Return the single user owning this email (current or historical), else None.

        Only lowercase/trim normalization: dots and +tags are significant (AS1). If several users
        have held the address and `at` is given, the owner at that time wins (AS: reused emails).
        """
        e = norm_email(email)
        owners = self.email_index.get(e, set())
        if len(owners) == 1:
            return next(iter(owners))
        if len(owners) > 1 and at:
            held = {u for u, start, end in self.email_periods.get(e, [])
                    if (not start or start <= at) and (not end or at < end)}
            if len(held) == 1:
                return next(iter(held))
        return None


def load_requests(name="deletion_requests.csv", base=DATASET):
    return read_csv(name, base)


def load_manifest(base=DATASET):
    return {r["file_id"]: r for r in read_csv("evidence_files.csv", base)}


def load_rules(base=DATASET):
    return {r["data_category"]: r for r in read_csv("deletion_rules.csv", base)}


def load_activity(base=DATASET):
    return read_csv("system_activity.csv", base)
