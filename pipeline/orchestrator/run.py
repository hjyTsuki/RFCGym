#!/usr/bin/env python3
"""
Main entry point for Multi-Agent CVE Reproduction System

Usage:
    python -m orchestrator.run --phase1
    python -m orchestrator.run --cve CVE-2024-12345
    python -m orchestrator.run --input-dir original_cves_md/ --cve CVE-2024-12345
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from typing import List
import yaml

from .async_orchestrator import AsyncOrchestrator, CVETaskStatus

# Custom hook to convert subprocess cleanup errors to warnings
_original_unraisablehook = sys.unraisablehook

def _custom_unraisablehook(unraisable):
    """Convert asyncio subprocess cleanup errors to logger warnings."""
    if (unraisable.exc_type is RuntimeError and
        'Event loop is closed' in str(unraisable.exc_value)):
        logger = logging.getLogger('orchestrator.cleanup')
        logger.warning(f"Asyncio cleanup: {unraisable.exc_value}")
    else:
        _original_unraisablehook(unraisable)

sys.unraisablehook = _custom_unraisablehook


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging and return logger for run.py"""
    level = logging.DEBUG if verbose else logging.INFO

    # Ensure logs directory exists
    logs_dir = Path('logs')
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler - same format as AsyncOrchestrator
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = logs_dir / f"orchestrator_{timestamp}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return logging.getLogger('orchestrator.run')


def _get_current_log_file() -> Path:
    """Get the log file path from the root logger's file handler."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename)
    return None


def generate_results_md(log_file: Path, phase: str, results: dict):
    """Generate a markdown summary table alongside the log file.

    Output: logs/orchestrator_{timestamp}.md (same stem as the .log)
    """
    from datetime import datetime
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    md_file = log_file.with_suffix('.md')

    lines = [
        f"# {phase} Results",
        "",
        "| CVE-ID | Status | Date |",
        "|--------|--------|------|",
    ]
    for cve_id, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        lines.append(f"| {cve_id} | {status} | {date_str} |")

    success_count = sum(1 for v in results.values() if v)
    fail_count = len(results) - success_count
    lines.append("")
    lines.append(f"**Total: {len(results)}, Success: {success_count}, Failed: {fail_count}**")

    md_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_available_cves(cve_input_dir: Path) -> List[str]:
    """List all available CVE files"""
    cve_files = sorted(cve_input_dir.glob("CVE-*.md"))
    return [f.stem for f in cve_files]


async def process_cves(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Process CVEs (full pipeline)"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System")
    logger.info("=" * 60)
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Input dir: {cve_input_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    try:
        if len(cve_ids) == 1:
            success = await orchestrator.process_cve(cve_ids[0])
            results = {cve_ids[0]: success}
        else:
            results = await orchestrator.process_multiple_cves(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_phase1(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Process Phase 1 (Analyzer + Generator) for CVEs"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Phase 1")
    logger.info("=" * 60)
    logger.info("Phase 1: Analyzer")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Input dir: {cve_input_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    try:
        results = await orchestrator.run_phase1_analysis(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Phase 1 Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("Successful CVEs are ready for Phase 2. Run with --phase2 to continue.")
        logger.info("=" * 60)

        # Generate markdown summary
        log_file = _get_current_log_file()
        if log_file:
            generate_results_md(log_file, "Phase 1", results)
            logger.info(f"Results markdown saved to {log_file.with_suffix('.md')}")

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Phase 1 error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_check_phase(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Process Check Phase only (check_cve_ready.py + checker agent loop)"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Check Phase")
    logger.info("=" * 60)
    logger.info("Check Phase: check_cve_ready.py + Checker Agent Loop")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Input dir: {cve_input_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    try:
        results = await orchestrator.run_phase_check(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Check Phase Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Check Phase error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_judger(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Run Judger agent for quality audit on completed CVEs"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Judger Phase")
    logger.info("=" * 60)
    logger.info("Judger Phase: Quality Audit")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    try:
        results = await orchestrator.run_poc_judger(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Judger Phase Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Judger Phase error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_changer(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Run Changer agent to transform CVE tasks to Terminal Bench format"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Changer Phase")
    logger.info("=" * 60)
    logger.info("Changer Phase: Terminal Bench Transformation")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    try:
        results = await orchestrator.run_changer(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Changer Phase Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Changer Phase error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_comparer(cve_ids: List[str], config_path: str, verbose: bool, tests_replace_dir: str):
    """Run Comparer agent to compare test_vuln.py with patcheval_test.patch"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Comparer Phase")
    logger.info("=" * 60)
    logger.info("Comparer Phase: Test Completeness Comparison")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Tests replace dir: {tests_replace_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path)

    try:
        results = await orchestrator.run_comparer(cve_ids, Path(tests_replace_dir))

        # Log summary
        logger.info("=" * 60)
        logger.info("Comparer Phase Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "BETTER/EQUAL" if success else "WORSE"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success (BETTER/EQUAL): {success_count}, Failed (WORSE): {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Comparer Phase error: {e}")
        logging.exception("Fatal error")
        return 1


async def process_expert(cve_ids: List[str], config_path: str, verbose: bool, test_and_env_replace_dir: str):
    """Run Expert agent to adapt expert solution.sh to our environment"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Expert Phase")
    logger.info("=" * 60)
    logger.info("Expert Phase: Solution Adaptation")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Test and env replace dir: {test_and_env_replace_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path)

    # Start background cleanup for Docker operations
    await orchestrator.start_background_cleanup()

    try:
        results = await orchestrator.run_expert(cve_ids, Path(test_and_env_replace_dir))

        # Log summary
        logger.info("=" * 60)
        logger.info("Expert Phase Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Expert Phase error: {e}")
        logging.exception("Fatal error")
        return 1

    finally:
        await orchestrator.stop_background_cleanup()


async def process_phase2(cve_ids: List[str], config_path: str, verbose: bool, cve_input_dir: str):
    """Process Phase 2 (Builder → Checker) for CVEs that completed Phase 1"""
    logger = setup_logging(verbose)

    logger.info("=" * 60)
    logger.info("Multi-Agent CVE Reproduction System - Phase 2")
    logger.info("=" * 60)
    logger.info("Phase 2: Generator → Builder → Docker → Validator → Solver → Checker → Cleanup → Judger")
    logger.info(f"Processing {len(cve_ids)} CVE(s): {' '.join(cve_ids)}")
    logger.info(f"Input dir: {cve_input_dir}")
    logger.info(f"Config: {config_path}")

    orchestrator = AsyncOrchestrator(config_path, cve_input_dir=cve_input_dir)

    # Start background cleanup
    await orchestrator.start_background_cleanup()

    try:
        results = await orchestrator.run_phase2_remaining(cve_ids)

        # Log summary
        logger.info("=" * 60)
        logger.info("Phase 2 Results")
        logger.info("=" * 60)

        success_count = sum(1 for r in results.values() if r)
        fail_count = len(results) - success_count

        for cve_id, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"{cve_id}: {status}")

        logger.info(f"Total: {len(results)}, Success: {success_count}, Failed: {fail_count}")
        logger.info("=" * 60)

        # Generate markdown summary
        log_file = _get_current_log_file()
        if log_file:
            generate_results_md(log_file, "Phase 2", results)
            logger.info(f"Results markdown saved to {log_file.with_suffix('.md')}")

        return 0 if fail_count == 0 else 1

    except Exception as e:
        logger.error(f"Phase 2 error: {e}")
        logging.exception("Fatal error")
        return 1

    finally:
        await orchestrator.stop_background_cleanup()


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Multi-Agent CVE Reproduction System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all CVEs in default directory (original_cves_md/)
  python -m orchestrator.run

  # Process specific CVE(s)
  python -m orchestrator.run --cve CVE-2024-12345
  python -m orchestrator.run --cve CVE-2024-12345 CVE-2024-67890

  # Use different input directory
  python -m orchestrator.run --input-dir original_cves_md/
  python -m orchestrator.run --input-dir original_cves_md/ --cve CVE-2024-12345

  # Phase 1 only (Analyzer + Generator, no Docker)
  python -m orchestrator.run --phase1
  python -m orchestrator.run --phase1 --cve CVE-2024-12345

  # Phase 2 only (Builder → Checker, requires Docker)
  python -m orchestrator.run --phase2
  python -m orchestrator.run --phase2 --cve CVE-2024-12345

  # Check Phase only (check_cve_ready.py + Checker agent loop)
  python -m orchestrator.run --check-phase
  python -m orchestrator.run --check-phase --cve CVE-2024-12345

  # Judger Phase only (quality audit on completed CVEs)
  python -m orchestrator.run --judger --cve CVE-2024-12345

  # Changer Phase only (transform to Terminal Bench format)
  python -m orchestrator.run --changer --cve CVE-2024-12345

  # Comparer Phase only (compare test_vuln.py with patcheval_test.patch)
  python -m orchestrator.run --comparer --cve cve-2016-1000232
  python -m orchestrator.run --comparer --tests-replace-dir replace/tests_replace --cve cve-2016-1000232

  # Expert Phase only (adapt expert solution.sh to our environment)
  python -m orchestrator.run --expert --cve cve-2015-1326
  python -m orchestrator.run --expert --test-and-env-replace-dir replace/test_and_env_replace --cve cve-2015-1326

  # List available CVEs
  python -m orchestrator.run --list

  # List CVEs ready for Phase 2
  python -m orchestrator.run --list-phase2

  # Enable verbose logging
  python -m orchestrator.run --verbose
        """
    )

    parser.add_argument(
        '--input-dir',
        type=Path,
        default='original_cves_md',
        help='Directory containing CVE files (default: original_cves_md/)'
    )

    parser.add_argument(
        '--cve',
        nargs='+',
        help='Specific CVE ID(s) to process (default: all CVEs in input-dir)'
    )

    parser.add_argument(
        '--list',
        action='store_true',
        help='List all available CVEs in input-dir'
    )

    parser.add_argument(
        '--list-phase2',
        action='store_true',
        help='List CVEs ready for Phase 2'
    )

    parser.add_argument(
        '--phase1',
        action='store_true',
        help='Run Phase 1 only (Analyzer + Generator)'
    )

    parser.add_argument(
        '--phase2',
        action='store_true',
        help='Run Phase 2 only (Builder → Checker)'
    )

    parser.add_argument(
        '--check-phase',
        action='store_true',
        help='Run Check Phase only (check_cve_ready.py + Checker agent loop)'
    )

    parser.add_argument(
        '--judger',
        action='store_true',
        help='Run Judger Phase only (quality audit on completed CVEs)'
    )

    parser.add_argument(
        '--changer',
        action='store_true',
        help='Run Changer Phase only (transform to Terminal Bench format)'
    )

    parser.add_argument(
        '--comparer',
        action='store_true',
        help='Run Comparer Phase only (compare test_vuln.py with patcheval_test.patch)'
    )

    parser.add_argument(
        '--tests-replace-dir',
        type=Path,
        default='replace/tests_replace',
        help='Directory containing tests_replace/{cve-id}/ structure (default: replace/tests_replace/)'
    )

    parser.add_argument(
        '--expert',
        action='store_true',
        help='Run Expert Phase only (adapt expert solution.sh to our environment)'
    )

    parser.add_argument(
        '--test-and-env-replace-dir',
        type=Path,
        default='replace/test_and_env_replace',
        help='Directory containing test_and_env_replace/{cve-id}/ structure (default: replace/test_and_env_replace/)'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Validate input_dir
    if not args.input_dir.exists():
        print(f"Error: Directory not found: {args.input_dir}")
        return 1

    cve_input_dir = str(args.input_dir)

    # Handle --list
    if args.list:
        available = list_available_cves(args.input_dir)
        print(f"\nAvailable CVEs in {args.input_dir} ({len(available)}):")
        for cve_id in available:
            print(f"  - {cve_id}")
        print()
        return 0

    # Handle --list-phase2
    if args.list_phase2:
        orchestrator = AsyncOrchestrator(args.config, cve_input_dir=cve_input_dir)
        ready = orchestrator.get_phase1_completed_cves()
        print(f"\nCVEs ready for Phase 2 ({len(ready)}):")
        for cve_id in ready:
            print(f"  - {cve_id}")
        print()
        return 0

    # Collect CVE IDs
    if args.cve:
        cve_ids = args.cve
    else:
        # All CVEs in input_dir
        cve_ids = list_available_cves(args.input_dir)
        if not cve_ids:
            print(f"Error: No CVE files found in {args.input_dir}")
            return 1

    # Handle --phase1
    if args.phase1:
        return asyncio.run(process_phase1(cve_ids, args.config, args.verbose, cve_input_dir))

    # Handle --phase2
    if args.phase2:
        return asyncio.run(process_phase2(cve_ids, args.config, args.verbose, cve_input_dir))

    # Handle --check-phase
    if args.check_phase:
        return asyncio.run(process_check_phase(cve_ids, args.config, args.verbose, cve_input_dir))

    # Handle --judger
    if args.judger:
        return asyncio.run(process_judger(cve_ids, args.config, args.verbose, cve_input_dir))

    # Handle --changer
    if args.changer:
        return asyncio.run(process_changer(cve_ids, args.config, args.verbose, cve_input_dir))

    # Handle --comparer
    if args.comparer:
        # For comparer, we use tests_replace_dir instead of input_dir
        # CVE IDs should be lowercase (e.g., cve-2016-1000232)
        tests_replace_dir = str(args.tests_replace_dir)
        return asyncio.run(process_comparer(cve_ids, args.config, args.verbose, tests_replace_dir))

    # Handle --expert
    if args.expert:
        # For expert, we use test_and_env_replace_dir instead of input_dir
        # CVE IDs should be lowercase (e.g., cve-2015-1326)
        test_and_env_replace_dir = str(args.test_and_env_replace_dir)
        return asyncio.run(process_expert(cve_ids, args.config, args.verbose, test_and_env_replace_dir))

    # Full pipeline
    return asyncio.run(process_cves(cve_ids, args.config, args.verbose, cve_input_dir))


if __name__ == "__main__":
    sys.exit(main())
