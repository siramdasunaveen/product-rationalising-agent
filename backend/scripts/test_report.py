"""Run the backend test suite and the frontend type check, then write docs/TEST_REPORT.md.

Run from the backend folder with the local infra up (docker compose up -d --wait):

    .\\.venv\\Scripts\\python.exe scripts\\test_report.py
"""

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "TEST_REPORT.md"
PY = sys.executable


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    if cmd[0] == "npm" and platform.system() == "Windows":
        cmd = ["npm.cmd", *cmd[1:]]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       shell=platform.system() == "Windows")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    started = datetime.now(UTC)
    report = ROOT / "backend" / "var" / "pytest-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    if "--reuse" in sys.argv and report.with_suffix(".xml").exists():  # report an earlier run without re-running
        code, out = 0, "reused backend/var/pytest-report.xml"
    else:
        code, out = run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", str(report.with_suffix(".xml"))],
                        ROOT / "backend")
    summary = [line for line in out.strip().splitlines() if "passed" in line or "failed" in line or "error" in line][-1:]
    per_file: dict[str, dict[str, int]] = {}
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(report.with_suffix(".xml"))
        for case in tree.iter("testcase"):
            f = (case.get("classname") or "").split(".")[-1] or "?"
            row = per_file.setdefault(f, {"tests": 0, "failed": 0, "skipped": 0})
            row["tests"] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                row["failed"] += 1
            if case.find("skipped") is not None:
                row["skipped"] += 1
        if "--reuse" in sys.argv:
            total = sum(v["tests"] for v in per_file.values())
            failed = sum(v["failed"] for v in per_file.values())
            code = 0 if failed == 0 else 1
            summary = [f"{total - failed} passed, {failed} failed (from the saved junit report)"]
    except Exception:  # noqa: BLE001 - the report is best effort
        pass
    fe_code, fe_out = run(["npm", "run", "typecheck", "--silent"], ROOT / "frontend") if (ROOT / "frontend" / "node_modules").exists() else (-1, "node_modules missing")
    build_code, build_out = run(["npm", "run", "build", "--silent"], ROOT / "frontend") if fe_code == 0 else (-1, "skipped")
    git = run(["git", "rev-parse", "--short", "HEAD"], ROOT)[1].strip()
    lines = [
        "# Test report", "",
        f"Generated {started.strftime('%Y-%m-%d %H:%M UTC')} on {platform.system()} {platform.release()}, "
        f"Python {platform.python_version()}, commit `{git}`.", "",
        "## Backend (pytest, PostgreSQL + fakeredis)", "",
        f"Result: **{'PASS' if code == 0 else 'FAIL'}** — {summary[0] if summary else out[-300:]}", "",
        "| Test module | Tests | Failed | Skipped |", "|---|---:|---:|---:|",
        *[f"| {k} | {v['tests']} | {v['failed']} | {v['skipped']} |" for k, v in sorted(per_file.items())],
        f"| **Total** | **{sum(v['tests'] for v in per_file.values())}** | **{sum(v['failed'] for v in per_file.values())}** | **{sum(v['skipped'] for v in per_file.values())}** |",
        "", "## Frontend", "",
        f"Type check (`tsc -b`): **{'PASS' if fe_code == 0 else 'FAIL'}**" + ("" if fe_code == 0 else f"\n\n```\n{fe_out[-1500:]}\n```"),
        f"Production build (`vite build`): **{'PASS' if build_code == 0 else 'FAIL' if build_code > 0 else 'SKIPPED'}**", "",
        "## What the suite covers", "",
        "Pure domain rules (scoring, guardrails, detectors, readiness, decision readiness, evidence requests, decisions),",
        "API behaviour with the real PostgreSQL schema and fakes for outside systems (Redis, AI provider, Salesforce,",
        "malware scanner), the CAND-1042 happy path and gap branch end to end (evidence upload → mapping → reconciliation",
        "→ snapshot → analysis → candidate review → evidence request → child snapshot → reassessment → decision →",
        "multi-approver approval), permissions and tenancy on every router, append-only and immutability guarantees.", "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}: backend {'PASS' if code == 0 else 'FAIL'}, frontend typecheck {'PASS' if fe_code == 0 else 'FAIL'}")
    print(json.dumps({"backend": code, "typecheck": fe_code, "build": build_code}))
    return 0 if code == 0 and fe_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
