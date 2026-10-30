"""V2: customer-facing confirmation messages, generated from output.csv with templates (no LLM, no cost).

Messages never name internal systems, files or resource IDs; they only state the outcome.
Usage: python3 code/customer_messages.py   ->  customer_messages.csv (case_id, email, status, message)
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost import data  # noqa: E402

TEMPLATES = {
    "COMPLETE": ("We have finished processing your deletion request. Your account and the personal data linked "
                 "to it have been deleted or irreversibly anonymized, and backups can no longer restore it. "
                 "{retained}"),
    "INCOMPLETE": ("We are still processing your deletion request. Our review found remaining copies of your "
                   "data, and we are removing them now{flows}. We will confirm once every check has passed."),
    "NOT_APPLICABLE": ("We did not treat your message as a request to delete your account, so nothing has been "
                       "deleted. If you would like your account and personal data deleted, please reply "
                       "confirming that."),
}
NA_BY_REASON = [  # (review-queue text, message) for NOT_APPLICABLE cases that were flagged, not declined
    ("withdrawn", "As you asked, we have cancelled your earlier deletion request. Your account remains active."),
    ("third-party", "We can only act on deletion requests from the account holder. Our privacy team will contact "
                    "the account holder directly to verify before anything is changed."),
    ("not the requester's", "Your message refers to an account that is not linked to your email address. For safety we "
                            "will verify with the account holder before anything is deleted."),
    ("TOO_SHORT", "We could not tell from your message what you would like us to delete. Please reply with more "
                  "detail, and we will act on it."),
    ("AMBIGUOUS", "Before we delete anything, we need a clear confirmation from you. Please reply confirming that "
                  "you want your account and personal data deleted."),
    ("cannot audit", "Your deletion request needs a manual review by our privacy team. We will contact you once "
                     "it is complete."),
]
RETAINED = ("A minimal record required for security or legal obligations is kept for the period the law "
            "requires, without your contact details.")


def message(status, steps, notes=()):
    if status == "NOT_APPLICABLE":
        for needle, text in NA_BY_REASON:
            if any(needle in n for n in notes):
                return text
    ops = {s["operation"] for s in steps}
    if status == "COMPLETE":
        return TEMPLATES[status].format(retained=RETAINED if "VERIFY_RECORD_PRESENT" in ops else "").strip()
    if status == "INCOMPLETE":
        flows = " and stopping any process that could copy it back" if ops & {"DISABLE_DATA_FLOW", "BLOCK_RESTORE"} else ""
        return TEMPLATES[status].format(flows=flows)
    return TEMPLATES[status]


def main():
    root = data.REPO_ROOT
    emails = {q["case_id"]: q["email"] for q in data.load_requests()}
    notes = {}
    with open(root / "review_queue.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            notes.setdefault(r["case_id"], []).append(r["reason"])
    with open(root / "output.csv", newline="", encoding="utf-8") as f:
        rows = [(r["case_id"], emails.get(r["case_id"], ""), r["deletion_status"],
                 message(r["deletion_status"], json.loads(r["steps_json"]), notes.get(r["case_id"], []))) for r in csv.DictReader(f)]
    with open(root / "customer_messages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "email", "deletion_status", "message"])
        w.writerows(rows)
    print(f"wrote customer_messages.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
