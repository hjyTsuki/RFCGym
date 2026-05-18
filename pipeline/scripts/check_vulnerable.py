#!/usr/bin/env python3
"""
Validator Check Script - Verify vulnerable environment is correctly set up.

This script is used by the Validator agent to verify that:
1. Functionality tests PASS (application works correctly)
2. Vulnerability tests FAIL (vulnerability exists and can be exploited)

Usage:
    # From CVE directory:
    python ../../scripts/check_vulnerable.py

    # Or specify CVE:
    python scripts/check_vulnerable.py CVE-2025-12345 --dir cve_tasks_1209
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.script_executor import ScriptExecutor
import yaml


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    DIM = '\033[2m'
    BOLD = '\033[1m'
    ENDC = '\033[0m'


def load_config():
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


async def check_vulnerable_env(cve_dir: Path, cve_id: str, verbose: bool = False):
    """
    Check that vulnerable environment is correctly set up.

    Success criteria:
    - func tests: ALL PASS
    - vuln tests: ALL FAIL (vulnerability exists)
    """
    config = load_config()
    executor = ScriptExecutor(config)

    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}Validator Check: {cve_id}{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.DIM}[{timestamp}]{Colors.ENDC} Working directory: {cve_dir}")
    print(f"{Colors.DIM}[{timestamp}]{Colors.ENDC} Expected: func tests PASS, vuln tests FAIL")
    print()

    # Run tests in vulnerable stage
    print(f"{Colors.CYAN}Running tests in vulnerable environment...{Colors.ENDC}")
    result = await executor.run_tests(
        working_dir=cve_dir,
        cve_id=cve_id,
        stage='vulnerable',
        restart_docker=True
    )

    # Check results
    if not result.get('success', False):
        print(f"\n{Colors.RED}✗ FAILED: {result.get('error', 'Unknown error')}{Colors.ENDC}")
        if verbose and result.get('raw_output'):
            print(f"\n{Colors.DIM}Raw output (last 1000 chars):{Colors.ENDC}")
            print(result['raw_output'][-1000:])
        return False

    validation = result.get('validation', {})
    is_valid = validation.get('valid', False)

    # Print test results
    func_results = validation.get('func_results', {})
    vuln_results = validation.get('vuln_results', {})

    print(f"\n{Colors.BOLD}Test Results:{Colors.ENDC}")

    # Func tests
    func_passed = func_results.get('passed', 0)
    func_failed = func_results.get('failed', 0)
    func_total = func_passed + func_failed
    if func_failed == 0 and func_passed > 0:
        print(f"  {Colors.GREEN}✓ Functionality tests: {func_passed}/{func_total} passed{Colors.ENDC}")
    else:
        print(f"  {Colors.RED}✗ Functionality tests: {func_passed}/{func_total} passed, {func_failed} failed{Colors.ENDC}")

    # Vuln tests
    vuln_passed = vuln_results.get('passed', 0)
    vuln_failed = vuln_results.get('failed', 0)
    vuln_total = vuln_passed + vuln_failed
    if vuln_failed > 0 and vuln_passed == 0:
        print(f"  {Colors.GREEN}✓ Vulnerability tests: {vuln_failed}/{vuln_total} failed (vulnerability exists){Colors.ENDC}")
    else:
        print(f"  {Colors.RED}✗ Vulnerability tests: {vuln_passed}/{vuln_total} passed (should all fail){Colors.ENDC}")

    # Issues
    issues = validation.get('issues', [])
    if issues:
        print(f"\n{Colors.YELLOW}Issues:{Colors.ENDC}")
        for issue in issues:
            print(f"  - {issue}")

    # Final result
    print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
    if is_valid:
        print(f"{Colors.GREEN}✓ VALIDATOR CHECK PASSED{Colors.ENDC}")
        print(f"{Colors.DIM}Vulnerable environment is correctly set up.{Colors.ENDC}")
    else:
        print(f"{Colors.RED}✗ VALIDATOR CHECK FAILED{Colors.ENDC}")
        print(f"{Colors.DIM}Fix the issues above and run this check again.{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}\n")

    return is_valid


async def main():
    parser = argparse.ArgumentParser(
        description="Check vulnerable environment (for Validator agent)"
    )
    parser.add_argument("cve_id", nargs="?", help="CVE ID (auto-detected if in CVE directory)")
    parser.add_argument("--dir", default="cve_tasks_1209", help="CVE tasks directory")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

    # Determine CVE directory
    if args.cve_id:
        cve_dir = PROJECT_ROOT / args.dir / args.cve_id
        cve_id = args.cve_id
    elif Path.cwd().name.startswith("CVE-"):
        cve_dir = Path.cwd()
        cve_id = cve_dir.name
    else:
        print(f"{Colors.RED}Error: No CVE ID provided and not in CVE directory{Colors.ENDC}")
        print("Usage: python check_vulnerable.py CVE-2025-XXXXX")
        print("   or: cd cve_tasks/CVE-2025-XXXXX && python ../../scripts/check_vulnerable.py")
        sys.exit(1)

    if not cve_dir.exists():
        print(f"{Colors.RED}Error: CVE directory not found: {cve_dir}{Colors.ENDC}")
        sys.exit(1)

    success = await check_vulnerable_env(cve_dir, cve_id, args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
