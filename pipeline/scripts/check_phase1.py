#!/usr/bin/env python3
"""Check Phase 1 completion status for CVE tasks."""

import os
from pathlib import Path

def main():
    cve_tasks_dir = Path("cve_tasks_1209")

    not_completed = []

    for cve_dir in sorted(cve_tasks_dir.iterdir()):
        if not cve_dir.is_dir() or not cve_dir.name.startswith("CVE-"):
            continue

        analyzer_res = cve_dir / ".agent_state" / "analyzer-res.xml"
        generator_res = cve_dir / ".agent_state" / "generator-res.xml"

        if not (analyzer_res.exists() and generator_res.exists()):
            not_completed.append(cve_dir.name)

    print(" ".join(not_completed))

if __name__ == "__main__":
    main()
