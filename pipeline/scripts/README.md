# CVE-Factory Scripts

Utility scripts for verifying and managing CVE reproductions.

## Overview

| Script | Purpose | Used By |
|--------|---------|---------|
| `check_cve_ready.py` | Full end-to-end verification (files + vulnerable + fixed) | Checker agent, manual QA |
| `check_vulnerable.py` | Verify vulnerable environment setup | Validator agent |
| `check_fixed.py` | Verify solution correctly fixes vulnerability | Solver agent |
| `check_phase1.py` | List CVEs with incomplete Phase 1 | Manual |
| `clean_phase2.py` | Remove Phase 2 artifacts for re-run | Manual |

## Scripts

### check_cve_ready.py

Complete verification that a CVE reproduction is ready for use. Performs all checks in sequence:

1. **File check** - Verifies all required files exist (Dockerfile, tests/, solution.sh, etc.)
2. **Vulnerable environment test** - Builds Docker, runs tests (expects: func PASS, vuln FAIL)
3. **Apply solution** - Executes solution.sh inside container
4. **Fixed environment test** - Runs tests again (expects: func PASS, vuln PASS)

```bash
# Check a specific CVE
python scripts/check_cve_ready.py CVE-2025-12345

# Check all CVEs in a directory
python scripts/check_cve_ready.py --all --dir cve_tasks

# Skip Docker tests (file check only)
python scripts/check_cve_ready.py CVE-2025-12345 --skip-docker

# Skip vulnerable test (faster iteration on solution)
python scripts/check_cve_ready.py CVE-2025-12345 --skip-vuln

# Verbose output
python scripts/check_cve_ready.py CVE-2025-12345 -v
```

---

### check_vulnerable.py

Verifies that the vulnerable environment is correctly set up. Used by the **Validator agent** after Builder creates the Docker environment.

**Success criteria:**
- Functionality tests: ALL PASS (application works)
- Vulnerability tests: ALL FAIL (vulnerability exists and is exploitable)

```bash
# Specify CVE explicitly
python scripts/check_vulnerable.py CVE-2025-12345 --dir cve_tasks

# Verbose output
python scripts/check_vulnerable.py CVE-2025-12345 -v
```

---

### check_fixed.py

Verifies that the solution correctly fixes the vulnerability. Used by the **Solver agent** after applying solution.sh.

**Success criteria:**
- Solution applies without errors
- Functionality tests: ALL PASS (application still works)
- Vulnerability tests: ALL PASS (vulnerability is fixed)

```bash
# Specify CVE explicitly
python scripts/check_fixed.py CVE-2025-12345 --dir cve_tasks

# Verbose output
python scripts/check_fixed.py CVE-2025-12345 -v
```

---

### check_phase1.py

Lists CVE IDs that have not completed Phase 1 (Analyzer + Generator). A CVE is considered Phase 1 complete when both `analyzer-res.xml` and `generator-res.xml` exist in `.agent_state/`.

```bash
python scripts/check_phase1.py
```

Output: Space-separated list of incomplete CVE IDs (for use in shell scripts).

---

### clean_phase2.py

Removes all Phase 2 artifacts (Builder, Validator, Solver, Checker outputs) while preserving Phase 1 results. Useful for re-running Phase 2 with updated configurations or after fixing issues.

**Files removed:**
- `Dockerfile`, `docker-compose.yaml`, `final_report.md`
- `.agent_state/{builder,validator,solver,checker}_output/`
- `.agent_state/{builder,validator,solver,checker}-res.xml`
- `.logs/{builder,validator,solver,checker}_conversation.{md,json}`
- Docker containers and images for the CVE

```bash
# Clean a single CVE
python scripts/clean_phase2.py CVE-2025-12345

# Clean multiple CVEs
python scripts/clean_phase2.py CVE-2025-12345 CVE-2025-12346

# Clean all CVEs in cve_tasks directory
python scripts/clean_phase2.py --all

# Clean CVEs from a file (one CVE ID per line)
python scripts/clean_phase2.py --file failed_cves.txt

# Skip Docker cleanup (faster, but leaves containers/images)
python scripts/clean_phase2.py CVE-2025-12345 --no-docker
```

## Common Workflows

### Debugging a Failed CVE

```bash
# 1. Check what's failing
python scripts/check_cve_ready.py CVE-2025-12345 -v

# 2. If vulnerable environment fails, use Validator check
python scripts/check_vulnerable.py CVE-2025-12345 -v

# 3. If solution fails, use Solver check
python scripts/check_fixed.py CVE-2025-12345 -v
```

### Re-running Phase 2

```bash
# Clean Phase 2 artifacts
python scripts/clean_phase2.py CVE-2025-12345

# Re-run Phase 2
python -m orchestrator.run --phase2 --cve CVE-2025-12345
```

### Batch Verification

```bash
# Check all CVEs and get summary
python scripts/check_cve_ready.py --all --dir cve_tasks

# Find incomplete Phase 1 CVEs
python scripts/check_phase1.py
```
