#!/usr/bin/env python3
"""
CVE Ready Checker - CLI wrapper for ScriptExecutor.run_cve_check()

This script provides a command-line interface to verify CVE reproductions.
The core logic is implemented in orchestrator/script_executor.py.

Usage:
    # From project root directory:
    python scripts/check_cve_ready.py CVE-2025-12345
    python scripts/check_cve_ready.py --all --dir cve_tasks_1209
    python scripts/check_cve_ready.py CVE-2025-12345 --skip-docker   # files only
    python scripts/check_cve_ready.py CVE-2025-12345 --skip-vuln     # skip vulnerable test
    python scripts/check_cve_ready.py CVE-2025-12345 -v              # verbose mode

    # Auto-detect CVE from current directory (for Checker agent):
    cd cve_tasks/CVE-2025-12345 && python ../../scripts/check_cve_ready.py
"""

import argparse
import asyncio
import logging
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add project root to path (parent of scripts/)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.script_executor import ScriptExecutor


# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


def load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml."""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def print_check_result(name: str, check: Dict[str, Any], verbose: bool = False) -> None:
    """Print a single check result."""
    success = check.get('success', False)
    skipped = check.get('skipped', False)

    if skipped:
        status = f"{Colors.DIM}○ SKIPPED{Colors.ENDC}"
    elif success:
        status = f"{Colors.GREEN}✓ PASS{Colors.ENDC}"
    else:
        status = f"{Colors.RED}✗ FAIL{Colors.ENDC}"

    print(f"  {status} {name}")

    # Print details on failure
    if not success and not skipped:
        if 'missing' in check:
            for f in check['missing']:
                print(f"    {Colors.DIM}└─ Missing: {f}{Colors.ENDC}")

        details = check.get('details', {})
        if details:
            issues = details.get('validation', {}).get('issues', [])
            for issue in issues[:5]:  # Limit to first 5 issues
                print(f"    {Colors.DIM}└─ {issue}{Colors.ENDC}")

            if verbose:
                raw_output = details.get('raw_output', '')
                if raw_output:
                    print(f"    {Colors.DIM}└─ Output (last 500 chars):{Colors.ENDC}")
                    for line in raw_output[-500:].split('\n')[-10:]:
                        print(f"      {Colors.DIM}│ {line}{Colors.ENDC}")


def print_results(results: Dict[str, Any], verbose: bool = False) -> None:
    """Print formatted check results."""
    cve_id = results['cve_id']
    ready = results['ready']
    checks = results['checks']

    # Header
    print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
    ready_str = f"{Colors.GREEN}✓ READY{Colors.ENDC}" if ready else f"{Colors.RED}✗ NOT READY{Colors.ENDC}"
    print(f"{Colors.BOLD}CVE: {cve_id}{Colors.ENDC}  [{ready_str}]")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")

    # Check results
    print_check_result("Required files", checks.get('files', {}), verbose)
    print_check_result("Vulnerable environment test", checks.get('vulnerable_test', {}), verbose)
    print_check_result("Apply solution.sh", checks.get('solution', {}), verbose)
    print_check_result("Fixed environment test", checks.get('fixed_test', {}), verbose)

    print()


def print_summary_table(results_list: List[Dict[str, Any]]) -> None:
    """Print a summary table for all CVEs."""
    print(f"\n{Colors.BOLD}{'='*80}{Colors.ENDC}")
    print(f"{Colors.BOLD}SUMMARY{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*80}{Colors.ENDC}")

    # Header
    print(f"\n{'CVE ID':<20} {'Files':<8} {'Vuln':<8} {'Solution':<10} {'Fixed':<8} {'Status':<10}")
    print("-" * 70)

    for results in results_list:
        cve_id = results['cve_id']
        checks = results['checks']

        def status_char(check_name: str) -> str:
            check = checks.get(check_name, {})
            if check.get('skipped'):
                return f"{Colors.DIM}○{Colors.ENDC}"
            elif check.get('success'):
                return f"{Colors.GREEN}✓{Colors.ENDC}"
            else:
                return f"{Colors.RED}✗{Colors.ENDC}"

        ready = results.get('ready', False)
        ready_str = f"{Colors.GREEN}READY{Colors.ENDC}" if ready else f"{Colors.RED}NOT READY{Colors.ENDC}"

        # Use padding that accounts for color codes
        print(f"{cve_id:<20} {status_char('files'):<17} {status_char('vulnerable_test'):<17} {status_char('solution'):<19} {status_char('fixed_test'):<17} {ready_str}")


def find_cve_dirs(base_dir: Path) -> List[Path]:
    """Find all CVE directories."""
    return sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("CVE-")])


async def check_single_cve(
    executor: ScriptExecutor,
    cve_dir: Path,
    cve_id: str,
    skip_docker: bool = False,
    skip_vuln: bool = False,
    verbose: bool = False,
    cleanup_images: bool = True
) -> Dict[str, Any]:
    """Check a single CVE and return results."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{Colors.DIM}[{timestamp}]{Colors.ENDC} {Colors.CYAN}Starting checks for {cve_id}{Colors.ENDC}")
    print(f"{Colors.DIM}[{timestamp}]{Colors.ENDC} Working directory: {cve_dir}")

    results = await executor.run_cve_check(
        working_dir=cve_dir,
        cve_id=cve_id,
        skip_docker=skip_docker,
        skip_vuln=skip_vuln,
        verbose=verbose,
        cleanup_images=cleanup_images  # CLI: cleanup on success by default
    )

    print_results(results, verbose)
    return results


async def main():
    parser = argparse.ArgumentParser(description="Check if CVE reproductions are ready")
    parser.add_argument("cve_id", nargs="?", help="CVE ID to check (auto-detected if in CVE directory)")
    parser.add_argument("--all", action="store_true", help="Check all CVEs in directory")
    parser.add_argument("--dir", default="cve_tasks_1209", help="CVE tasks directory (relative to project root)")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker tests (files only)")
    parser.add_argument("--skip-vuln", action="store_true", help="Skip vulnerable environment test")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format=f'{Colors.DIM}%(asctime)s{Colors.ENDC} {Colors.CYAN}[%(name)s]{Colors.ENDC} %(message)s',
        datefmt='%H:%M:%S'
    )
    logging.getLogger('orchestrator.script_executor').setLevel(log_level)

    # Load config and create executor
    config = load_config()
    executor = ScriptExecutor(config)

    # Determine CVE directory and ID
    if args.cve_id:
        # Explicit CVE ID provided
        base_dir = PROJECT_ROOT / args.dir
        cve_dir = base_dir / args.cve_id
        cve_id = args.cve_id
    elif Path.cwd().name.startswith("CVE-"):
        # Auto-detect: running from within CVE directory
        cve_dir = Path.cwd()
        cve_id = cve_dir.name
        print(f"{Colors.CYAN}Auto-detected CVE directory: {cve_id}{Colors.ENDC}")
    elif args.all:
        # Check all CVEs in directory
        base_dir = PROJECT_ROOT / args.dir
        if not base_dir.exists():
            print(f"{Colors.RED}Error: Directory not found: {base_dir}{Colors.ENDC}")
            sys.exit(1)

        cve_dirs = find_cve_dirs(base_dir)
        if not cve_dirs:
            print(f"{Colors.RED}No CVE directories found in {base_dir}{Colors.ENDC}")
            sys.exit(1)

        print(f"{Colors.BOLD}Checking {len(cve_dirs)} CVEs in {base_dir}...{Colors.ENDC}")
        mode = 'Files only' if args.skip_docker else ('Skip vuln test' if args.skip_vuln else 'Full Docker tests')
        print(f"Mode: {mode}")

        results_list = []
        ready_count = 0

        for i, cve_path in enumerate(cve_dirs, 1):
            print(f"\n{Colors.CYAN}[{i}/{len(cve_dirs)}] Processing {cve_path.name}...{Colors.ENDC}")
            results = await check_single_cve(
                executor, cve_path, cve_path.name,
                args.skip_docker, args.skip_vuln, args.verbose
            )
            results_list.append(results)
            if results['ready']:
                ready_count += 1

        # Print summary
        print_summary_table(results_list)

        print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}FINAL SUMMARY{Colors.ENDC}")
        print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
        print(f"Total:     {len(cve_dirs)}")
        print(f"Ready:     {Colors.GREEN}{ready_count}{Colors.ENDC}")
        print(f"Not Ready: {Colors.RED}{len(cve_dirs) - ready_count}{Colors.ENDC}")

        not_ready = [r['cve_id'] for r in results_list if not r['ready']]
        if not_ready:
            print(f"\n{Colors.YELLOW}Not ready CVEs:{Colors.ENDC}")
            for cve in not_ready:
                print(f"  - {cve}")

        sys.exit(0 if ready_count == len(cve_dirs) else 1)
    else:
        parser.print_help()
        sys.exit(1)

    # Single CVE check
    if not cve_dir.exists():
        print(f"{Colors.RED}Error: CVE directory not found: {cve_dir}{Colors.ENDC}")
        sys.exit(1)

    results = await check_single_cve(
        executor, cve_dir, cve_id,
        args.skip_docker, args.skip_vuln, args.verbose
    )
    sys.exit(0 if results['ready'] else 1)


if __name__ == "__main__":
    asyncio.run(main())
