"""Build code.zip for submission.

Contents: README.md, code/ (minus caches of Python bytecode and OS files), and the two reports at
evaluation/ in the archive root, the path AGENTS.md specifies (the README's code/evaluation/ copy is also kept).
Usage: python3 code/package.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}
SKIP_NAMES = {".DS_Store"}
SKIP_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}  # no nested archives


def wanted(p):
    return not (SKIP_PARTS & set(p.parts) or p.name in SKIP_NAMES or p.suffix in SKIP_SUFFIXES or p.name == ".env")


def main():
    out = ROOT / "code.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "README.md", "README.md")
        for p in sorted((ROOT / "code").rglob("*")):
            if p.is_file() and wanted(p.relative_to(ROOT)):
                z.write(p, p.relative_to(ROOT).as_posix())
        for name in ("evaluation_report.md", "usage_report.md"):
            z.write(ROOT / "code/evaluation" / name, f"evaluation/{name}")
        n = len(z.namelist())
    print(f"wrote {out.name} ({n} files)")


if __name__ == "__main__":
    main()
