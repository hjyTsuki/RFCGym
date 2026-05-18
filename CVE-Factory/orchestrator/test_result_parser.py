"""
Test Result Parser - Parse pytest output and validate results

This module parses pytest -rA output and validates:
1. Vulnerable environment: func tests PASS, vuln tests FAIL
2. Fixed environment: func tests PASS, vuln tests PASS
"""

import re
import logging
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass
from enum import Enum


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class TestResult:
    """Single test result"""
    file: str
    name: str
    status: TestStatus


@dataclass
class ParsedResults:
    """Parsed test results"""
    func_tests: List[TestResult]
    vuln_tests: List[TestResult]
    other_tests: List[TestResult]
    total_passed: int
    total_failed: int
    total_error: int
    total_skipped: int
    raw_output: str


class TestResultParser:
    """
    Parse pytest -rA output and validate results.

    Expected output format:
    =========================== short test summary info ============================
    PASSED tests/test_functionality.py::test_basic
    PASSED tests/test_functionality.py::test_health
    FAILED tests/test_vulnerability.py::test_vuln_not_present
    FAILED tests/test_vulnerability.py::test_security
    ========================= 2 failed, 2 passed in 0.15s =========================
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Regex patterns for standard pytest format
        self.summary_line_pattern = re.compile(
            r'^(PASSED|FAILED|ERROR|SKIPPED)\s+(.+?)::(.+)$'
        )
        # Regex patterns for custom test runner format
        self.custom_line_pattern = re.compile(
            r'^[✓✗]\s+(PASSED|FAILED):\s+(.+?)::(.+?)(?:\s+-\s+.*)?$'
        )
        self.final_summary_pattern = re.compile(
            r'(\d+)\s+(failed|passed|error|skipped)'
        )

    def parse(self, pytest_output: str) -> ParsedResults:
        """
        Parse test output - supports both pytest format and custom test runner format.

        Args:
            pytest_output: Raw test output string

        Returns:
            ParsedResults with categorized test results
        """
        func_tests = []
        vuln_tests = []
        other_tests = []

        total_passed = 0
        total_failed = 0
        total_error = 0
        total_skipped = 0

        # Detect if this is pytest format or custom format
        lines = pytest_output.split('\n')
        is_pytest_format = any('short test summary info' in line for line in lines)

        if is_pytest_format:
            # Use original pytest parsing logic
            in_summary = False
            for line in lines:
                line = line.strip()

                # Detect summary section start
                if 'short test summary info' in line:
                    in_summary = True
                    continue

                # Detect summary section end (final stats line)
                if in_summary and ('passed' in line or 'failed' in line) and '=' in line and 'in' in line:
                    # Parse final summary: "2 failed, 2 passed, 1 skipped in 0.15s"
                    for match in self.final_summary_pattern.finditer(line):
                        count = int(match.group(1))
                        status = match.group(2)
                        if status == 'passed':
                            total_passed = count
                        elif status == 'failed':
                            total_failed = count
                        elif status == 'error':
                            total_error = count
                        elif status == 'skipped':
                            total_skipped = count
                    break

                # Parse individual test results
                if in_summary:
                    match = self.summary_line_pattern.match(line)
                    if match:
                        status_str, file_path, test_name = match.groups()
                        status = TestStatus[status_str]

                        result = TestResult(
                            file=file_path,
                            name=test_name,
                            status=status
                        )

                        # Categorize by file name
                        self._categorize_test_result(result, func_tests, vuln_tests, other_tests)
        else:
            # Use custom format parsing logic
            for line in lines:
                line = line.strip()

                # Parse custom format: "✓ PASSED: TestClass::test_name" or "✗ FAILED: TestClass::test_name - reason"
                match = self.custom_line_pattern.match(line)
                if match:
                    status_str, class_and_file, test_name = match.groups()
                    status = TestStatus[status_str]

                    # Extract file information - determine from the test output structure
                    # Look for file section headers like "==================== test_func.py ===================="
                    current_file = self._determine_current_file(lines, line, class_and_file)

                    result = TestResult(
                        file=current_file,
                        name=test_name,
                        status=status
                    )

                    # Categorize by file name
                    self._categorize_test_result(result, func_tests, vuln_tests, other_tests)

                    # Update counters
                    if status == TestStatus.PASSED:
                        total_passed += 1
                    elif status == TestStatus.FAILED:
                        total_failed += 1

                # Parse final summary in custom format: "6 failed, 7 passed"
                elif 'failed' in line and 'passed' in line and ('FAILED' in line or 'failures=' in line):
                    for match in self.final_summary_pattern.finditer(line):
                        count = int(match.group(1))
                        status = match.group(2)
                        if status == 'passed':
                            total_passed = count
                        elif status == 'failed':
                            total_failed = count
                        elif status == 'error':
                            total_error = count
                        elif status == 'skipped':
                            total_skipped = count

        return ParsedResults(
            func_tests=func_tests,
            vuln_tests=vuln_tests,
            other_tests=other_tests,
            total_passed=total_passed,
            total_failed=total_failed,
            total_error=total_error,
            total_skipped=total_skipped,
            raw_output=pytest_output
        )

    def _categorize_test_result(self, result: TestResult, func_tests: List[TestResult],
                               vuln_tests: List[TestResult], other_tests: List[TestResult]) -> None:
        """Categorize a test result by file name."""
        if 'test_func' in result.file or 'test_functionality' in result.file:
            func_tests.append(result)
        elif 'test_vuln' in result.file or 'test_vulnerability' in result.file:
            vuln_tests.append(result)
        else:
            other_tests.append(result)

    def _determine_current_file(self, lines: List[str], current_line: str, class_name: str) -> str:
        """Determine which file a test belongs to in custom format."""
        # Find the most recent file section header
        current_idx = -1
        for i, line in enumerate(lines):
            if current_line in line:
                current_idx = i
                break

        # Look backwards for file section header
        if current_idx >= 0:
            for i in range(current_idx, -1, -1):
                line = lines[i].strip()
                if line.startswith('=') and 'test_' in line and line.endswith('='):
                    # Extract filename from "==================== test_func.py ===================="
                    parts = line.split()
                    for part in parts:
                        if 'test_' in part and part.endswith('.py'):
                            return part

        # Fallback: guess from class name
        if 'Functionality' in class_name or 'Plugin' in class_name:
            return 'test_func.py'
        elif 'Sanitization' in class_name or 'XSS' in class_name or 'Vuln' in class_name:
            return 'test_vuln.py'
        else:
            return 'unknown_test.py'

    def validate_vulnerable_env(self, results: ParsedResults) -> Dict[str, Any]:
        """
        Validate results for vulnerable environment.

        Expected:
        - ALL func tests PASS
        - ALL vuln tests FAIL

        Args:
            results: Parsed test results

        Returns:
            Validation result dict
        """
        func_all_pass = all(t.status == TestStatus.PASSED for t in results.func_tests)
        vuln_all_fail = all(t.status == TestStatus.FAILED for t in results.vuln_tests)

        # Check we have tests in both categories
        has_func_tests = len(results.func_tests) > 0
        has_vuln_tests = len(results.vuln_tests) > 0

        is_valid = func_all_pass and vuln_all_fail and has_func_tests and has_vuln_tests

        # Build detailed report
        func_passed = [t for t in results.func_tests if t.status == TestStatus.PASSED]
        func_failed = [t for t in results.func_tests if t.status == TestStatus.FAILED]
        vuln_passed = [t for t in results.vuln_tests if t.status == TestStatus.PASSED]
        vuln_failed = [t for t in results.vuln_tests if t.status == TestStatus.FAILED]

        return {
            'valid': is_valid,
            'stage': 'vulnerable_environment',
            'expected': {
                'func_tests': 'ALL PASS',
                'vuln_tests': 'ALL FAIL'
            },
            'actual': {
                'func_tests': {
                    'total': len(results.func_tests),
                    'passed': len(func_passed),
                    'failed': len(func_failed),
                    'all_pass': func_all_pass
                },
                'vuln_tests': {
                    'total': len(results.vuln_tests),
                    'passed': len(vuln_passed),
                    'failed': len(vuln_failed),
                    'all_fail': vuln_all_fail
                }
            },
            'issues': self._get_vulnerable_env_issues(
                func_all_pass, vuln_all_fail,
                has_func_tests, has_vuln_tests,
                func_failed, vuln_passed
            )
        }

    def validate_fixed_env(self, results: ParsedResults) -> Dict[str, Any]:
        """
        Validate results for fixed environment (after solution applied).

        Expected:
        - ALL func tests PASS
        - ALL vuln tests PASS

        Args:
            results: Parsed test results

        Returns:
            Validation result dict
        """
        func_all_pass = all(t.status == TestStatus.PASSED for t in results.func_tests)
        vuln_all_pass = all(t.status == TestStatus.PASSED for t in results.vuln_tests)

        # Check we have tests in both categories
        has_func_tests = len(results.func_tests) > 0
        has_vuln_tests = len(results.vuln_tests) > 0

        is_valid = func_all_pass and vuln_all_pass and has_func_tests and has_vuln_tests

        # Build detailed report
        func_passed = [t for t in results.func_tests if t.status == TestStatus.PASSED]
        func_failed = [t for t in results.func_tests if t.status == TestStatus.FAILED]
        vuln_passed = [t for t in results.vuln_tests if t.status == TestStatus.PASSED]
        vuln_failed = [t for t in results.vuln_tests if t.status == TestStatus.FAILED]

        return {
            'valid': is_valid,
            'stage': 'fixed_environment',
            'expected': {
                'func_tests': 'ALL PASS',
                'vuln_tests': 'ALL PASS'
            },
            'actual': {
                'func_tests': {
                    'total': len(results.func_tests),
                    'passed': len(func_passed),
                    'failed': len(func_failed),
                    'all_pass': func_all_pass
                },
                'vuln_tests': {
                    'total': len(results.vuln_tests),
                    'passed': len(vuln_passed),
                    'failed': len(vuln_failed),
                    'all_pass': vuln_all_pass
                }
            },
            'issues': self._get_fixed_env_issues(
                func_all_pass, vuln_all_pass,
                has_func_tests, has_vuln_tests,
                func_failed, vuln_failed
            )
        }

    def _get_vulnerable_env_issues(
        self,
        func_all_pass: bool,
        vuln_all_fail: bool,
        has_func_tests: bool,
        has_vuln_tests: bool,
        func_failed: List[TestResult],
        vuln_passed: List[TestResult]
    ) -> List[str]:
        """Get list of issues for vulnerable environment validation."""
        issues = []

        if not has_func_tests:
            issues.append("No functionality tests found (test_func*.py or test_functionality*.py)")

        if not has_vuln_tests:
            issues.append("No vulnerability tests found (test_vuln*.py or test_vulnerability*.py)")

        if not func_all_pass and has_func_tests:
            failed_names = [f"{t.file}::{t.name}" for t in func_failed]
            issues.append(f"Functionality tests should PASS but failed: {failed_names}")

        if not vuln_all_fail and has_vuln_tests:
            passed_names = [f"{t.file}::{t.name}" for t in vuln_passed]
            issues.append(f"Vulnerability tests should FAIL but passed: {passed_names} "
                         "(environment may be patched or tests are incorrect)")

        return issues

    def _get_fixed_env_issues(
        self,
        func_all_pass: bool,
        vuln_all_pass: bool,
        has_func_tests: bool,
        has_vuln_tests: bool,
        func_failed: List[TestResult],
        vuln_failed: List[TestResult]
    ) -> List[str]:
        """Get list of issues for fixed environment validation."""
        issues = []

        if not has_func_tests:
            issues.append("No functionality tests found (test_func*.py or test_functionality*.py)")

        if not has_vuln_tests:
            issues.append("No vulnerability tests found (test_vuln*.py or test_vulnerability*.py)")

        if not func_all_pass and has_func_tests:
            failed_names = [f"{t.file}::{t.name}" for t in func_failed]
            issues.append(f"Functionality tests should PASS but failed: {failed_names} "
                         "(solution may have broken functionality)")

        if not vuln_all_pass and has_vuln_tests:
            failed_names = [f"{t.file}::{t.name}" for t in vuln_failed]
            issues.append(f"Vulnerability tests should PASS but failed: {failed_names} "
                         "(solution may be incomplete)")

        return issues


def validate_test_output(
    pytest_output: str,
    stage: str = 'vulnerable'
) -> Dict[str, Any]:
    """
    Convenience function to validate pytest output.

    Args:
        pytest_output: Raw pytest output
        stage: 'vulnerable' or 'fixed'

    Returns:
        Validation result dict
    """
    parser = TestResultParser()
    results = parser.parse(pytest_output)

    if stage == 'vulnerable':
        return parser.validate_vulnerable_env(results)
    elif stage == 'fixed':
        return parser.validate_fixed_env(results)
    else:
        raise ValueError(f"Unknown stage: {stage}. Use 'vulnerable' or 'fixed'")


if __name__ == "__main__":
    # Test with sample output
    sample_output = """
============================= test session starts ==============================
platform linux -- Python 3.10.0, pytest-7.4.0
collected 4 items

tests/test_functionality.py ..                                           [ 50%]
tests/test_vulnerability.py FF                                           [100%]

=========================== short test summary info ============================
PASSED tests/test_functionality.py::test_basic_functionality
PASSED tests/test_functionality.py::test_application_health
FAILED tests/test_vulnerability.py::test_vulnerability_not_present - AssertionError
FAILED tests/test_vulnerability.py::test_security_controls - AssertionError
========================= 2 failed, 2 passed in 0.15s =========================
"""

    print("=== Testing Vulnerable Environment Validation ===")
    result = validate_test_output(sample_output, stage='vulnerable')
    print(f"Valid: {result['valid']}")
    print(f"Issues: {result['issues']}")
    print()

    print("=== Testing Fixed Environment Validation ===")
    result = validate_test_output(sample_output, stage='fixed')
    print(f"Valid: {result['valid']}")
    print(f"Issues: {result['issues']}")
