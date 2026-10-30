"""Knockout test: does the agent really read the pixels?

Builds a temporary copy of the dataset in which one screenshot is replaced by (a) a blank white image
and (b) the same screenshot with the customer's email painted over, re-runs the agent, and checks
that the finding that depended on that screenshot disappears. A model that "concluded" something
from a blank image would fail this test.

Usage: python3 code/evaluation/knockout.py      (one or two Sonnet calls; replies are cached)
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

import main as agent  # noqa: E402
from ghost import data, llm as llm_mod  # noqa: E402

TARGET_FILE = "evidence/images/analytics_profile_view_20260713.png"   # evidence_19
TARGET_CASE, TARGET_RESOURCE = "case_50", "analytics_7438"


def finding_for(out_csv):
    import csv
    for r in csv.DictReader(open(out_csv, encoding="utf-8")):
        if r["case_id"] == TARGET_CASE:
            steps = json.loads(r["steps_json"])
            return r["deletion_status"], [s for s in steps if s["resource_id"] == TARGET_RESOURCE and s["phase"] == "FINDING"]
    return None, []


def run_variant(name, mutate):
    tmp = Path(tempfile.mkdtemp(prefix=f"ghost_{name}_"))
    base = tmp / "dataset"
    shutil.copytree(data.DATASET, base)
    img_path = base / TARGET_FILE
    mutate(img_path)
    out = tmp / "out.csv"
    agent.run("deletion_requests.csv", out, tmp / "review.csv", base=base, llm=llm_mod.LLM())
    status, finding = finding_for(out)
    shutil.rmtree(tmp)
    return status, finding


def blank(path):
    w, h = Image.open(path).size
    Image.new("RGB", (w, h), "white").save(path)


def erase_email(path):
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    d.rectangle([0, 0, w, h // 3], fill="white")  # header strip holding name, user_id and email
    im.save(path)


def main():
    base_status, base_finding = finding_for(ROOT / "output.csv")
    print(f"original : {TARGET_CASE} {base_status}, finding on {TARGET_RESOURCE}: {bool(base_finding)}")
    results = []
    for name, fn in (("blank", blank), ("email_erased", erase_email)):
        status, finding = run_variant(name, fn)
        ok = not finding
        results.append(ok)
        print(f"{name:<9}: {TARGET_CASE} {status}, finding on {TARGET_RESOURCE}: {bool(finding)} -> {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if all(results) and base_finding else 1)


if __name__ == "__main__":
    main()
