"""Run test modules in separate processes; fail on tests, imports, crashes or timeouts."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FOCUS = (
    "test_document_library", "test_product_support", "test_qt_library", "test_search_index",
    "test_product_search", "test_application_search", "test_local_rag", "test_embedding_service",
    "test_qt_my_files", "test_qt_file_browser", "test_qt_overview", "test_qt_setup",
    "test_qt_settings", "test_qt_configuration_pages", "test_qt_activity", "test_qt_service_bridge",
    "test_packaging_foundation", "test_configuration_service", "test_product_configuration",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--focus", action="store_true", help="Run document-library and impacted regression modules")
    args = parser.parse_args()
    names = FOCUS if args.focus else tuple(p.stem for p in sorted((ROOT / "tests").glob("test_*.py")))
    output = ROOT / "build" / "test-results"
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.name != "nt":
        environment.setdefault("PYSTRAY_BACKEND", "dummy")
        environment.setdefault("LOCALAPPDATA", str(output / "test-profile"))
    records = []
    for name in names:
        try:
            run = subprocess.run([sys.executable, "-m", "unittest", f"tests.{name}"],
                cwd=ROOT, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", timeout=180)
            code, log = run.returncode, run.stdout
        except subprocess.TimeoutExpired:
            code, log = 124, "Test module exceeded its 180-second timeout."
        (output / f"{name}.log").write_text(log, encoding="utf-8")
        count = re.search(r"Ran (\d+) tests?", log)
        skipped = re.search(r"skipped=(\d+)", log)
        records.append({"module": name, "returncode": code, "tests": int(count.group(1)) if count else 0,
                        "skipped": int(skipped.group(1)) if skipped else 0})
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({records[-1]['tests']} tests)", flush=True)
        if code:
            print("\n".join(log.splitlines()[-15:]), flush=True)
    report = {"modules": len(records), "tests": sum(r["tests"] for r in records), "skipped": sum(r["skipped"] for r in records),
              "failed_modules": [r["module"] for r in records if r["returncode"]], "results": records}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}), flush=True)
    return int(bool(report["failed_modules"]))


if __name__ == "__main__":
    raise SystemExit(main())
