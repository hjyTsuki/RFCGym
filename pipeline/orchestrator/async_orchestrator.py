"""
Async Orchestrator - Main CVE Reproduction Pipeline

This orchestrator coordinates all agents and automatic scripts to reproduce CVEs.
It handles the complete workflow without requiring agents to make decisions
about tool usage or file access.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import yaml

from .agent_runner import AgentRunner
from .tool_controller import AgentType
from .script_executor import ScriptExecutor
from .file_state_manager import FileStateManager
from .feedback_processor import FeedbackProcessor, FeedbackIssue
from .models import (
    AgentResult,
    PhaseDefinition,
    PhaseRecord,
    CVETaskStatus,
    PHASE_DEFINITIONS,
)


class AsyncOrchestrator:
    """
    Main orchestrator for Multi-Agent CVE reproduction.

    Responsibilities:
    - Load CVE information from reproduce_cves/
    - Create working directories
    - Run agents in sequence
    - Execute automatic scripts (docker_auto_start, test_validator)
    - Handle failures and retries
    - Track progress
    """

    def __init__(self, config_path: str = "config.yaml", cve_input_dir: str = None):
        """
        Initialize orchestrator.

        Args:
            config_path: Path to configuration file
            cve_input_dir: Directory containing CVE input files (default: reproduce_cves/)
        """
        self.config = self._load_config(config_path)

        # Paths from config
        self.base_dir = Path(__file__).parent.parent
        self.cve_input_dir = Path(cve_input_dir) if cve_input_dir else self.base_dir / "reproduce_cves"
        self.cve_tasks_dir = Path(self.config['paths']['cve_tasks_dir'])
        self.logs_dir = Path(self.config['paths']['logs_dir'])  # Global orchestrator logs

        # Create directories
        self.cve_tasks_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Setup logging with file handler
        self._setup_logging()
        self.logger = logging.getLogger(__name__)

        # Initialize components
        self.agent_runner = AgentRunner(self.config)
        self.script_executor = ScriptExecutor(self.config)

        # Task tracking
        self.tasks: Dict[str, CVETaskStatus] = {}

        # Session pool for feedback loops
        self.session_pools: Dict[str, Dict[AgentType, str]] = {}  # cve_id -> {agent_type -> session_id}

        # File state managers per CVE (one per CVE to maintain consistent state)
        self.file_state_managers: Dict[str, FileStateManager] = {}  # cve_id -> FileStateManager

        # Agent concurrency limits using semaphores
        self.agent_semaphores: Dict[AgentType, asyncio.Semaphore] = {}
        self._init_agent_semaphores()

        # Background cleanup task handle
        self._cleanup_task: Optional[asyncio.Task] = None

        # Test results storage (for phase decision making)
        self._vulnerable_test_results: Dict[str, Dict[str, Any]] = {}
        self._solution_test_results: Dict[str, Dict[str, Any]] = {}
        self._expert_test_results: Dict[str, Dict[str, Any]] = {}

        self.logger.info("AsyncOrchestrator initialized")

    async def start_background_cleanup(self) -> None:
        """
        Start the background cleanup task for Docker resources.

        This task runs periodically based on docker.cleanup_interval config.
        """
        if self._cleanup_task is not None:
            self.logger.warning("Background cleanup task already running")
            return

        cleanup_interval = self.config.get('docker', {}).get('cleanup_interval', 300)
        self.logger.info(f"Starting background cleanup task (interval={cleanup_interval}s)")

        async def cleanup_loop():
            while True:
                await asyncio.sleep(cleanup_interval)
                try:
                    self.logger.debug("Running periodic Docker cleanup...")
                    result = await self.script_executor.cleanup_stale_containers()
                    self.logger.debug(f"Cleanup result: {result}")
                except Exception as e:
                    self.logger.error(f"Background cleanup error: {e}")

        self._cleanup_task = asyncio.create_task(cleanup_loop())

    async def stop_background_cleanup(self) -> None:
        """Stop the background cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            self.logger.info("Background cleanup task stopped")

    def _init_agent_semaphores(self) -> None:
        """
        Initialize semaphores for agent concurrency control.

        Each agent type has a limit on how many can run concurrently.
        """
        agent_limits = self.config.get('agents', {}).get('limits', {})

        for agent_type in AgentType:
            limit = agent_limits.get(agent_type.value, 5)  # Default limit: 5
            self.agent_semaphores[agent_type] = asyncio.Semaphore(limit)
            self.logger.debug(f"Agent semaphore: {agent_type.value} = {limit}")

    def _setup_logging(self) -> None:
        """
        Setup logging with both console and file handlers.

        If logging is already configured (by run.py), skip setup to avoid duplicates.
        """
        root_logger = logging.getLogger()

        # Check if logging is already configured (has file handler)
        has_file_handler = any(
            isinstance(h, logging.FileHandler) for h in root_logger.handlers
        )
        if has_file_handler:
            # Logging already configured by run.py, skip setup
            return

        # Set up logging (standalone mode or direct import)
        log_level = self.config.get('logging', {}).get('level', 'INFO')
        level = getattr(logging, log_level.upper(), logging.INFO)

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        root_logger.setLevel(level)

        # Clear existing handlers to avoid duplicates
        root_logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File handler - orchestrator_YYYYMMDD_HHMMSS.log
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = self.logs_dir / f"orchestrator_{timestamp}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def _log_phase(self, cve_id: str, phase_num: int, phase_name: str) -> None:
        """
        Log phase start timestamp.

        Args:
            cve_id: CVE identifier
            phase_num: Phase number
            phase_name: Phase name
        """
        timestamp = datetime.now().isoformat()
        log_file = self.cve_tasks_dir / cve_id / ".logs" / "timestamps.log"

        log_file.parent.mkdir(parents=True, exist_ok=True)

        with open(log_file, 'a') as f:
            f.write(f"[{timestamp}] Phase {phase_num}: {phase_name}\n")

        self.logger.info(f"[{cve_id}] Phase {phase_num}: {phase_name}")

    async def _get_or_create_session(
        self,
        agent_type: AgentType,
        cve_id: str,
        working_dir: Path
    ) -> str:
        """
        Get existing session or create new one for agent.

        Note: This only creates the session. Use _run_agent_with_semaphore()
        to run agent with proper concurrency control.

        Args:
            agent_type: Type of agent
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            Session ID
        """
        # Initialize session pool for this CVE if needed
        if cve_id not in self.session_pools:
            self.session_pools[cve_id] = {}

        session_pool = self.session_pools[cve_id]

        # Return existing session if available and SDK client is still alive
        if agent_type in session_pool:
            session_id = session_pool[agent_type]
            if session_id in self.agent_runner.sessions:
                session = self.agent_runner.sessions[session_id]
                # Check if SDK client's underlying process is still running
                is_alive = False
                try:
                    if (session.sdk_client and
                        hasattr(session.sdk_client, '_transport') and
                        session.sdk_client._transport and
                        hasattr(session.sdk_client._transport, '_process') and
                        session.sdk_client._transport._process and
                        session.sdk_client._transport._process.returncode is None):
                        is_alive = True
                except Exception:
                    pass

                if is_alive:
                    self.logger.debug(f"Reusing existing session for {agent_type.value}: {session_id}")
                    return session_id
                else:
                    # SDK client is dead, clean up the stale session
                    self.logger.warning(f"[{cve_id}] SDK client for {agent_type.value} is dead, creating new session")
                    try:
                        await self.agent_runner.close_session(session_id)
                    except Exception as e:
                        self.logger.debug(f"Error closing dead session: {e}")
                    del session_pool[agent_type]

        # Create new session
        # self.logger.info(f"Creating new session for {agent_type.value}")
        session_id = await self.agent_runner.create_session(
            agent_type=agent_type,
            cve_id=cve_id,
            working_dir=working_dir,
            metadata={'phase': agent_type.value, 'cve_id': cve_id}
        )

        # Store in session pool
        session_pool[agent_type] = session_id

        return session_id

    def _record_phase(
        self,
        cve_id: str,
        name: str,
        started_at: datetime,
        status: str,
        attempts: int = 1,
        feedbacks: List[FeedbackIssue] = None
    ) -> None:
        """Record a phase execution result."""
        self.tasks[cve_id].add_phase(
            name=name,
            started_at=started_at,
            completed_at=datetime.now(),
            attempts=attempts,
            status=status,
            feedbacks=feedbacks or []
        )

    def _save_task_status(self, cve_id: str) -> None:
        """Save task status to .logs/task_status.json"""
        task = self.tasks.get(cve_id)
        if task and task.working_dir:
            logs_dir = task.working_dir / ".logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            with open(logs_dir / "task_status.json", 'w') as f:
                json.dump(task.to_dict(), f, indent=2)

    def _get_file_state_manager(self, cve_id: str, working_dir: Path) -> FileStateManager:
        """
        Get or create FileStateManager for a CVE.

        Each CVE has exactly one FileStateManager to maintain consistent state.

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            FileStateManager instance
        """
        if cve_id not in self.file_state_managers:
            self.file_state_managers[cve_id] = FileStateManager(working_dir)
            self.logger.debug(f"[{cve_id}] Created FileStateManager")

        return self.file_state_managers[cve_id]

    async def _execute_agent_message(
        self,
        agent_type: AgentType,
        cve_id: str,
        working_dir: Path,
        message: str
    ) -> Dict[str, Any]:
        """
        Execute an agent message with semaphore control - the lowest level abstraction.

        This is the single entry point for ALL agent interactions:
        - Initial messages (from _run_agent)
        - Missing files retry messages (from _run_agent)
        - Feedback messages (from _process_agent_feedback)
        - Retry messages (from _process_agent_feedback)
        - Validator/Solver messages (from verification loops)

        Flow:
        1. Acquire semaphore
        2. Get or create session
        3. Send message via agent_runner.run_message (query + wait_for_completion)
        4. Release semaphore

        Args:
            agent_type: Type of agent
            cve_id: CVE identifier
            working_dir: Working directory
            message: Message to send to agent

        Returns:
            Agent execution result from run_message
        """
        semaphore = self.agent_semaphores[agent_type]

        # Get timeout from config (default: 600s)
        timeout = self.config.get('agents', {}).get('timeouts', {}).get(agent_type.value, 600)

        self.logger.debug(f"[{cve_id}] Waiting for {agent_type.value} semaphore (available: {semaphore._value})")

        async with semaphore:
            self.logger.debug(f"[{cve_id}] Acquired {agent_type.value} semaphore")

            # Get or create session
            session_id = await self._get_or_create_session(
                agent_type=agent_type,
                cve_id=cve_id,
                working_dir=working_dir
            )

            # Send message and wait for completion
            result = await self.agent_runner.run_message(session_id, message, timeout)

            self.logger.debug(f"[{cve_id}] Released {agent_type.value} semaphore")
            return result

    async def _run_agent(
        self,
        phase_key: str,
        cve_id: str,
        working_dir: Path,
        cve_content: Optional[str] = None,
        custom_message: Optional[str] = None,
        is_retry: bool = False,
    ) -> AgentResult:
        """
        Unified agent execution with config-driven retry logic.

        This method handles ALL agent phases including Validator and Solver.
        It internally manages:
        1. Creating output directories
        2. Retry loop (max_retries from config.yaml)
        3. Message preparation (initial, custom, or missing_files)
        4. Agent execution via _execute_agent_message
        5. Feedback processing
        6. Output file verification

        Args:
            phase_key: Key in PHASE_DEFINITIONS ('protocol_analyzer', 'scenario_generator', 'scenario_builder', 'attack_verifier', 'env_finalizer', 'fuzzer', 'poc_judger')
            cve_id: CVE identifier
            working_dir: Working directory
            cve_content: CVE content (only needed for analyzer)
            custom_message: Custom message to send instead of default initial message
            is_retry: If True, skip logging phase start (for verification loop retries)

        Returns:
            AgentResult with success status and error details
        """
        definition = PHASE_DEFINITIONS[phase_key]
        agent_type = definition.agent_type

        # Get max_retries from config.yaml (single source of truth)
        max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)

        # 1. Create output directories
        for dir_path in definition.output_dirs:
            (working_dir / dir_path).mkdir(parents=True, exist_ok=True)

        # 2. Retry loop
        missing_files: List[str] = []
        last_error: Optional[str] = None

        for attempt in range(1, max_retries + 1):
            phase_started_at = datetime.now()

            # 3. Prepare message
            if attempt == 1:
                if custom_message:
                    message = custom_message
                else:
                    message = self._prepare_initial_message(agent_type, cve_id, working_dir, cve_content)
                # Log phase start only on first attempt (unless it's a retry in verification loop)
                if not is_retry:
                    self._log_phase(cve_id, definition.phase_num, definition.phase_name)
            else:
                # Retry: use missing files message
                message = self._format_missing_files_message(missing_files)
                self.logger.info(f"[{cve_id}] Retrying {phase_key} (attempt {attempt}/{max_retries}) for missing files: {missing_files}")

            # 4. Execute agent message
            result = await self._execute_agent_message(
                agent_type=agent_type,
                cve_id=cve_id,
                working_dir=working_dir,
                message=message,
            )

            # 5. Verify output files FIRST (including XML) - missing files trigger retry
            if definition.required_files:
                missing_files = [f for f in definition.required_files if not (working_dir / f).exists()]
                if missing_files:
                    self.logger.warning(f"[{cve_id}] {phase_key} missing output files: {missing_files}")
                    last_error = f"Missing required files: {missing_files}"
                    continue  # Retry loop will send missing_files message

            # 6. Process feedback (only after all required files exist)
            feedback_success = await self._process_agent_feedback(
                agent_type=agent_type,
                cve_id=cve_id,
                working_dir=working_dir,
                phase_started_at=phase_started_at,
                attempts=attempt
            )

            if not feedback_success:
                last_error = f"{phase_key} agent reported error or has unresolved issues"
                self.logger.error(f"[{cve_id}] {last_error}")
                return AgentResult.fail(last_error, phase_key, attempt)

            # All files exist and feedback processed successfully
            self.logger.info(f"[{cve_id}] {phase_key} completed successfully")
            return AgentResult.ok(phase_key, attempt)

        # All retries exhausted
        last_error = f"{phase_key} failed after {max_retries} attempts, missing: {missing_files}"
        self.logger.error(f"[{cve_id}] {last_error}")
        return AgentResult.fail(last_error, phase_key, max_retries, missing_files)

    def _prepare_initial_message(
        self,
        agent_type: AgentType,
        cve_id: str,
        working_dir: Path,
        cve_content: Optional[str] = None
    ) -> str:
        """
        Prepare the initial message for an agent.

        Args:
            agent_type: Type of agent
            cve_id: CVE identifier
            working_dir: Working directory for this CVE task
            cve_content: CVE information content (for Analyzer)

        Returns:
            Initial message string
        """
        # Common header with working directory info (use absolute path)
        # NOTE: parameter cve_id is reused as scenario_id in RFCGym (e.g. SCN-HTTP3-CDN-RANGE)
        workdir_info = f"Your working directory is `{working_dir.resolve()}`. All files you need to read or create are relative to this path.\n\n"

        # Shared constraints (system prompt is long, agents may drift)
        no_mock = "CRITICAL: Do NOT use mock, monkeypatch, or any form of mocking/stubbing. All tests must run against the real protocol services."
        dynamic_tests = "CRITICAL: Tests must be DYNAMIC - they must send real protocol traffic (HTTP/3, SMTP, DNS, etc.) to the running services, NOT static source-code analysis."
        real_attack = "CRITICAL: Known-attack tests must produce observable wire-level evidence (response code, body diff, traffic amplification ratio, log entry) - never assert on source-code patterns."

        messages = {
            AgentType.PROTOCOL_ANALYZER: f"""{workdir_info}You are analyzing scenario {cve_id} for protocol environment construction (Stage 1).

Below is the scenario description:

---
{cve_content if cve_content else '[Scenario content will be provided]'}
---

This pipeline ONLY builds the test environment. The evaluation/fuzzing stage
is out of scope here. Produce exactly 6 files:

1. `public.md` - Protocol overview, RFC anchors, vendor landscape, bug_layer (L1/L2/L3)
2. `for_scenario_generator.md` - Test strategy: how to verify each service is alive,
   and how to assert each known attack reproduces (wire-level oracles)
3. `for_scenario_builder.md` - Multi-vendor Docker stack requirements: which open-source
   implementations to deploy, which versions, how they connect
4. `for_attack_verifier.md` - Expected behavior for each known attack (amplification
   factor, header transformations, log signatures, etc.)
5. `vendor_matrix.md` - For each vendor: type (open-source/api/blackbox), version, role
6. `known_attacks.yaml` - Machine-readable attack specs (>=1 if scenario requires)

For protocol specifications, the authoritative source is https://www.rfc-editor.org/
(index: https://www.rfc-editor.org/rfc-index.html). Always cite RFCs by number
and section (e.g. RFC 9110 Section 14.2), never by URL alone.

{no_mock} You must point to real implementations, not synthetic protocol code.
""",

            AgentType.SCENARIO_GENERATOR: f"""{workdir_info}You are generating test artifacts for scenario {cve_id}.

The protocol analyzer has completed their work. Read their output and create:
1. `task.yaml` - Fuzzer-facing task description (CRITICAL: do NOT mention specific
   known-attack mechanics; reveal only the ambiguity dimensions allowed by config)
2. `tests/test_service_alive.py` - Verifies each protocol service responds to normal
   requests (HTTP/3 GET /, SMTP HELO, DNS query, etc.)
3. `tests/test_known_attacks.py` - For each entry in `known_attacks.yaml`, sends the
   attack and asserts the EXPECTED VULNERABLE BEHAVIOR is observed (e.g.,
   amplification factor > N, header dropped, cache poisoned). Assertion is INVERTED
   relative to CVE-Factory: pass = attack succeeds = environment is ready.
4. `tests/run-tests.sh` - Runner

DO NOT create solution.sh. RFCGym does not verify fixes.

{dynamic_tests}
{real_attack}
""",

            AgentType.SCENARIO_BUILDER: f"""{workdir_info}You are building the protocol service stack for scenario {cve_id}.

Read `.agent_state/analyzer_output/public.md`, `for_scenario_builder.md`, and
`vendor_matrix.md`. Build a Docker stack that:
1. Runs each vendor implementation as a separate service (so cross-vendor diff
   testing is possible)
2. Wires services together with realistic topology (ingress -> origin, etc.)
3. Exposes observation points: a `pcaps/` volume, structured logs, debug endpoints
4. Does NOT bake `tests/` or `known_attacks.yaml` into any image - those are
   mounted/copied at test time only

CRITICAL: Tests must run against the real protocol services. No mocks, no stubs.
""",

            AgentType.ATTACK_VERIFIER: f"""{workdir_info}You are verifying environment readiness for scenario {cve_id}.

Oracle:
- All services in `docker-compose.yaml` respond to liveness probes
  (`tests/test_service_alive.py` passes)
- At least 1 attack from `known_attacks.yaml` reproduces successfully
  (`tests/test_known_attacks.py` shows the expected vulnerable behavior)

If oracle fails, adjust the Docker stack and/or the test scripts. Do NOT modify
`known_attacks.yaml` - those expectations come from the source paper/CVE and
are ground truth.

{no_mock}
{dynamic_tests}
""",

            # Note: AgentType.ENV_FINALIZER uses custom_message in _run_phase_check()

            AgentType.POC_JUDGER: f"""{workdir_info}You are the POC quality judge for scenario {cve_id}.

For each POC in `pocs/`:
1. Read `description.md` + `attack_primitive.md` + `attack_script.py` + `evidence.pcap`
2. Classify bug layer (L1 design / L2 impl-variance / L3 cross-protocol)
3. Validity: re-run `attack_script.py` and confirm the wire-level effect
4. Novelty: compare against `datasets/known_cves.jsonl` and the source paper's POC set
5. Severity: CVSS-style rough estimate (do not invent precise scores)

Output `.agent_state/poc_judger_output/poc_scores.json` with per-POC scoring.
Output `.agent_state/poc_judger_output/poc_report.md` with a human-readable summary.
""",

            AgentType.COMPARER: f"""{workdir_info}You are the test completeness comparison evaluator.

Compare the following two files:
1. `tests/test_vuln.py` - Our reproduced vulnerability test
2. `tests/patcheval_test.patch` - Expert-written official test (diff format)

Evaluation workflow:
1. Analyze the official test (patcheval_test.patch), identify test types
2. Analyze our test (test_vuln.py), identify test types
3. Check whether we cover all official test types
4. If there are uncovered types, assess their necessity
5. Assess whether we have additional strictness or test types

Output comparison report to {working_dir}/.agent_state/comparer_output/comparison_report.md
""",

            AgentType.EXPERT: f"""{workdir_info}You are the solution adaptation expert for {cve_id}.

Goal: Use the expert-provided `solution.sh` to verify whether our environment (Dockerfile + tests) correctly reproduces the CVE.

Current state: Test results are in `.agent_state/expert_output/test_results.md`

Your tasks:
1. Read the test results to understand why solution.sh execution failed
2. Compare `solution.sh` (expert solution) with `solution_origin.sh` (our reference solution)
3. Identify adaptation operations in `solution_origin.sh` that make solution work (paths, restarts, builds, etc.)
4. Add the necessary adaptation operations to `solution.sh`
5. Run `python ../../scripts/check_fixed.py` to verify

Notes:
- You may only add adaptation operations necessary for solution.sh to execute correctly
- You must not modify the core vulnerability fix logic (sed patterns, diff content, etc.)
- If adaptation is not possible, it indicates our environment is incompatible with the expert solution
""",
        }

        return messages.get(agent_type, f"{workdir_info}Process {cve_id}.").strip()

    def _format_missing_files_message(self, missing_files: List[str]) -> str:
        """
        Format a retry message for missing files.

        Args:
            missing_files: List of missing file paths

        Returns:
            Formatted message string
        """
        return f"""
Your previous attempt did not create all required files. Please create the missing files:

Missing files:
{chr(10).join(f'- {f}' for f in missing_files)}

Please continue and create these files now.
""".strip()

    async def _process_agent_feedback(
        self,
        agent_type: AgentType,
        cve_id: str,
        working_dir: Path,
        phase_started_at: datetime,
        attempts: int,
        feedback_depth: int = 0
    ) -> bool:
        """
        Process feedback from agent, record phase, route issues to responsible agents, and retry if needed.

        This is a unified feedback processing method that handles:
        1. Parse agent result XML and record phase
        2. If agent reports issues, send to responsible agents
        3. If issues were resolved, retry the original agent
        4. Recursively process feedback from retry (with depth limit)

        Args:
            agent_type: Agent that generated feedback
            cve_id: CVE identifier
            working_dir: Working directory
            phase_started_at: When this phase started
            attempts: Number of attempts for this phase
            feedback_depth: Current feedback recursion depth (default: 0)

        Returns:
            True if no issues found or all issues resolved
        """
        # Check feedback depth limit to prevent infinite loops
        max_feedback_depth = self.config.get('orchestrator', {}).get('max_feedback_depth', 3)
        if feedback_depth >= max_feedback_depth:
            self.logger.error(
                f"[{cve_id}] Feedback depth limit ({max_feedback_depth}) exceeded for {agent_type.value}. "
                f"Stopping to prevent infinite loop."
            )
            return False

        self.logger.debug(f"[{cve_id}] Processing feedback for {agent_type.value} (depth: {feedback_depth}/{max_feedback_depth})")

        # Get file state manager (one per CVE) and feedback processor
        file_state_manager = self._get_file_state_manager(cve_id, working_dir)
        feedback_processor = FeedbackProcessor(working_dir)

        # Update file states after agent execution
        modified_files = file_state_manager.update_file_states(agent_type)
        self.logger.debug(f"[{cve_id}] {agent_type.value} modified {len(modified_files)} files")

        # Parse agent result XML
        agent_result = feedback_processor.parse_agent_result_xml(agent_type)
        feedbacks = agent_result.issues or []

        # Record this agent's phase immediately (agent execution is done)
        phase_status = 'completed' if agent_result.status == "success" else 'failed'
        if agent_result.status == "pause":
            phase_status = 'paused'

        self._record_phase(cve_id, agent_type.value, phase_started_at, phase_status, attempts, feedbacks)

        if agent_result.status == "success":
            self.logger.info(f"[{cve_id}] {agent_type.value} finished with success status")
            return True

        if agent_result.status == "error":
            # Check if this is an XML parse error - give agent one chance to fix it
            is_xml_error = any(
                issue.name == "xml_parse_error" or "xml" in issue.name.lower()
                for issue in (agent_result.issues or [])
            )

            if is_xml_error:
                self.logger.warning(f"[{cve_id}] {agent_type.value} XML parse error, asking agent to regenerate")

                # Ask agent to regenerate the XML
                retry_message = f"""
Your result XML file has a parsing error: {agent_result.message}

Please regenerate the {agent_type.value}-res.xml file with valid XML format.
IMPORTANT: Wrap all text content in <![CDATA[...]]> to avoid XML parsing issues.

Example:
<result>
    <status>success</status>
    <message><![CDATA[Your message text here, can contain any characters like <, >, &]]></message>
</result>

Regenerate the XML file now.
""".strip()

                try:
                    result = await self._execute_agent_message(
                        agent_type=agent_type,
                        cve_id=cve_id,
                        working_dir=working_dir,
                        message=retry_message
                    )

                    if result['status'] == 'completed':
                        # Re-parse the XML after agent regenerates it
                        agent_result = feedback_processor.parse_agent_result_xml(agent_type)
                        if agent_result.status == "success":
                            self.logger.info(f"[{cve_id}] {agent_type.value} fixed XML and succeeded")
                            return True
                        elif agent_result.status != "error":
                            # Agent regenerated XML but with issues - continue processing below
                            self.logger.info(f"[{cve_id}] {agent_type.value} regenerated XML with status: {agent_result.status}")
                        else:
                            self.logger.error(f"[{cve_id}] {agent_type.value} still has XML error after retry")
                            return False
                    else:
                        self.logger.error(f"[{cve_id}] {agent_type.value} failed to regenerate XML")
                        return False
                except Exception as e:
                    self.logger.error(f"[{cve_id}] Error asking agent to fix XML: {e}")
                    return False
            else:
                self.logger.error(f"[{cve_id}] {agent_type.value} encountered error: {agent_result.message}")
                return False

        if agent_result.status == "pause" and agent_result.issues:
            self.logger.info(f"[{cve_id}] {agent_type.value} paused with {len(agent_result.issues)} issues")

            # Process each issue - send to responsible agents
            resolved_count = 0
            for issue in agent_result.issues:
                # Find responsible agent for this file/issue
                responsible_agent = file_state_manager.find_responsible_agent(issue.name)

                if responsible_agent is None:
                    self.logger.warning(f"[{cve_id}] No responsible agent found for: {issue.name}")
                    continue

                if responsible_agent == agent_type:
                    self.logger.debug(f"[{cve_id}] Issue with own file, {agent_type.value} should self-fix")
                    continue

                # Send feedback to responsible agent
                feedback_started_at = datetime.now()
                self.logger.info(f"[{cve_id}] Sending feedback from {agent_type.value} to {responsible_agent.value}: {issue.name}")

                try:
                    message = feedback_processor.format_feedback_message(issue, agent_type, responsible_agent)
                    result = await self._execute_agent_message(
                        agent_type=responsible_agent,
                        cve_id=cve_id,
                        working_dir=working_dir,
                        message=message
                    )

                    if result['status'] == 'completed':
                        self.logger.info(f"[{cve_id}] {responsible_agent.value} processed feedback successfully")
                        file_state_manager.update_file_states(responsible_agent)
                        self._record_phase(cve_id, responsible_agent.value, feedback_started_at, 'completed', 1, [issue])
                        resolved_count += 1
                    else:
                        self.logger.warning(f"[{cve_id}] {responsible_agent.value} failed to process feedback: {result.get('error')}")
                        self._record_phase(cve_id, responsible_agent.value, feedback_started_at, 'failed', 1, [issue])
                except Exception as e:
                    self.logger.error(f"[{cve_id}] Error sending feedback to {responsible_agent.value}: {e}")
                    self._record_phase(cve_id, responsible_agent.value, feedback_started_at, 'failed', 1, [issue])

            self.logger.info(f"[{cve_id}] Resolved {resolved_count}/{len(agent_result.issues)} issues")

            # If we resolved some issues, retry the original agent
            if resolved_count > 0:
                retry_started_at = datetime.now()
                self.logger.info(f"[{cve_id}] Retrying {agent_type.value} after feedback processing")

                try:
                    message = """
The issues you reported have been addressed by the responsible agents.
Please continue with your original task and verify that the problems are resolved.
""".strip()

                    result = await self._execute_agent_message(
                        agent_type=agent_type,
                        cve_id=cve_id,
                        working_dir=working_dir,
                        message=message
                    )

                    if result['status'] == 'completed':
                        # Recursively process feedback from retry (increment depth)
                        return await self._process_agent_feedback(
                            agent_type, cve_id, working_dir,
                            phase_started_at=retry_started_at,
                            attempts=1,
                            feedback_depth=feedback_depth + 1
                        )
                    else:
                        self.logger.warning(f"[{cve_id}] {agent_type.value} retry failed: {result.get('error')}")
                        self._record_phase(cve_id, agent_type.value, retry_started_at, 'failed')
                        return False

                except Exception as e:
                    self.logger.error(f"[{cve_id}] Error retrying {agent_type.value}: {e}")
                    self._record_phase(cve_id, agent_type.value, retry_started_at, 'failed')
                    return False

        return False

    async def _close_all_sessions_for_cve(self, cve_id: str) -> None:
        """
        Close all sessions and clean up resources for a specific CVE.

        Args:
            cve_id: CVE identifier
        """
        # Close all agent sessions
        if cve_id in self.session_pools:
            session_pool = self.session_pools[cve_id]

            for agent_type, session_id in session_pool.items():
                try:
                    await self.agent_runner.close_session(session_id)
                    self.logger.debug(f"[{cve_id}] Closed session for {agent_type.value}")
                except Exception as e:
                    self.logger.warning(f"[{cve_id}] Error closing session for {agent_type.value}: {e}")

            # Clear session pool for this CVE
            del self.session_pools[cve_id]

        # Clean up FileStateManager
        if cve_id in self.file_state_managers:
            del self.file_state_managers[cve_id]
            self.logger.debug(f"[{cve_id}] Cleaned up FileStateManager")

    async def _run_phase_vulnerable_verification(
        self, cve_id: str, working_dir: Path
    ) -> AgentResult:
        """
        Phase 4: Docker Build, Start, Test and Validate (with retry loop)

        Flow for each attempt:
        1. Run tests with restart_docker=True (stop, rebuild, start, then test)
        2. If tests pass (func PASS, vuln FAIL) → success
        3. If tests fail → run Validator agent to fix, loop back

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            AgentResult with success status and error details
        """
        phase_started_at = datetime.now()
        self._log_phase(cve_id, 4, "Vulnerable Environment Verification")

        max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)
        last_error: Optional[str] = None

        for validator_attempt in range(max_retries + 1):  # +1 for initial test without validator
            # Run tests with Docker restart (stop, rebuild, start, then test)
            self.logger.info(f"[{cve_id}] Running tests on vulnerable environment (attempt {validator_attempt + 1})...")
            test_result = await self.script_executor.run_tests(
                working_dir, cve_id, stage='vulnerable', restart_docker=True
            )
            self._vulnerable_test_results[cve_id] = test_result

            if test_result['success']:
                # func PASS + vuln FAIL = vulnerability confirmed
                self.logger.info(f"[{cve_id}] Vulnerable environment verified: func PASS, vuln FAIL")
                # Clean up test_results.md since verification passed
                test_results_file = working_dir / ".agent_state" / "validator_output" / "test_results.md"
                if test_results_file.exists():
                    test_results_file.unlink()
                self._record_phase(cve_id, 'vulnerable_verification', phase_started_at, 'completed', validator_attempt + 1)
                return AgentResult.ok('vulnerable_verification', validator_attempt + 1)

            # Tests failed - collect issues
            issues = test_result.get('validation', {}).get('issues', [])
            last_error = f"Tests failed: {'; '.join(issues)}" if issues else test_result.get('error', 'Tests did not match expected pattern')
            self.logger.warning(f"[{cve_id}] {last_error}")

            # Check if we've exhausted validator retries
            if validator_attempt >= max_retries:
                self.logger.error(f"[{cve_id}] Validator retries exhausted ({max_retries})")
                break

            # Run Validator agent to fix issues
            self.logger.info(f"[{cve_id}] Running Validator agent (attempt {validator_attempt + 1}/{max_retries})...")
            test_info_path = working_dir / ".agent_state" / "validator_output" / "test_results.md"
            test_info_path.parent.mkdir(parents=True, exist_ok=True)
            test_info_path.write_text(self._format_test_results_for_agent(test_result, stage='vulnerable'))

            message = None if validator_attempt == 0 else """
The previous fix did not resolve the issue. Tests still failed.

Please review the updated test results in:
.agent_state/validator_output/test_results.md

Analyze what went wrong and try a different approach to fix the environment.
""".strip()

            agent_result = await self._run_agent('attack_verifier', cve_id, working_dir, custom_message=message, is_retry=(validator_attempt > 0))

            # If validator reported error, stop retrying
            if not agent_result.success:
                self.logger.error(f"[{cve_id}] Validator agent reported error: {agent_result.error}")
                last_error = agent_result.error
                break

        # All retries exhausted - full cleanup
        await self.script_executor.docker_stop_and_cleanup(working_dir, cve_id, remove_images=True)
        self._record_phase(cve_id, 'vulnerable_verification', phase_started_at, 'failed', max_retries + 1)
        return AgentResult.fail(last_error or "Vulnerable verification failed after all retries", 'vulnerable_verification', max_retries + 1)

    def _format_test_results_for_agent(self, test_result: dict, stage: str) -> str:
        """Format test results as markdown for agent to read, with diagnostic hints."""
        if not test_result:
            return f"# Test Results ({stage})\n\nNo test results available.\n"

        lines = [
            f"# Test Results ({stage} environment)",
            "",
            "## Summary",
            f"- **Success**: {test_result.get('success', 'N/A')}",
            f"- **Stage**: {test_result.get('stage', stage)}",
            f"- **Script Exit Code**: {test_result.get('script_exit_code', 'N/A')}",
            "",
        ]

        validation = test_result.get('validation', {})
        raw_output = test_result.get('raw_output', '')

        if validation:
            lines.extend([
                "## Validation",
                f"- **Valid**: {validation.get('valid', 'N/A')}",
                "",
                "### Expected",
            ])
            expected = validation.get('expected', {})
            for key, val in expected.items():
                lines.append(f"- {key}: {val}")

            lines.extend(["", "### Actual"])
            actual = validation.get('actual', {})
            func_total = 0
            vuln_total = 0
            for test_type, data in actual.items():
                if isinstance(data, dict):
                    total = data.get('total', 0)
                    lines.append(f"- **{test_type}**: {data.get('passed', 0)} passed, {data.get('failed', 0)} failed (total: {total})")
                    if 'func' in test_type:
                        func_total = total
                    elif 'vuln' in test_type:
                        vuln_total = total

            issues = validation.get('issues', [])
            if issues:
                lines.extend(["", "## Issues"])
                for issue in issues:
                    lines.append(f"- {issue}")

            # Diagnostic hints based on common failure patterns
            hints = self._generate_test_hints(test_result, validation, raw_output, stage, func_total, vuln_total)
            if hints:
                lines.extend(["", "## Diagnostic Hints"])
                for hint in hints:
                    lines.append(f"- {hint}")

        if raw_output:
            # Show more context: last 4000 chars, and first 1000 for build errors
            lines.extend(["", "## Raw Test Output (tail)"])
            lines.append("```")
            lines.append(raw_output[-4000:] if len(raw_output) > 4000 else raw_output)
            lines.append("```")

            # If output is long, also show the head (often contains build/import errors)
            if len(raw_output) > 4000:
                lines.extend(["", "## Raw Test Output (head - may contain build/import errors)"])
                lines.append("```")
                lines.append(raw_output[:2000])
                lines.append("```")

        return "\n".join(lines)

    def _generate_test_hints(self, test_result: dict, validation: dict, raw_output: str, stage: str, func_total: int, vuln_total: int) -> list:
        """Generate diagnostic hints based on test failure patterns."""
        hints = []
        exit_code = test_result.get('script_exit_code', -1)
        actual = validation.get('actual', {})

        # No tests parsed at all
        if func_total == 0 and vuln_total == 0:
            hints.append(
                "**ZERO tests parsed.** The pytest output format may be wrong. "
                "Ensure run-tests.sh uses `pytest ... -rA` flag so the summary section "
                "'short test summary info' is printed. Without `-rA`, the parser cannot extract individual test results."
            )
            if 'ERROR' in raw_output.upper() or exit_code != 0:
                if 'ModuleNotFoundError' in raw_output or 'ImportError' in raw_output:
                    hints.append(
                        "**Import/module error detected.** A required Python package is missing in the container. "
                        "Check Dockerfile or run-tests.sh to ensure all dependencies (pytest, requests, etc.) are installed."
                    )
                if 'Connection refused' in raw_output or 'ConnectionError' in raw_output:
                    hints.append(
                        "**Connection error detected.** The test tried to connect to a service that is not running or not ready. "
                        "Check docker-compose.yaml service dependencies, health checks, and startup order."
                    )
                if 'No such file or directory' in raw_output:
                    hints.append(
                        "**File not found error.** A script or file referenced in run-tests.sh or tests does not exist in the container. "
                        "Check file paths and COPY commands in Dockerfile."
                    )

        # Docker build/start failure
        if 'Docker restart failed' in raw_output or 'Docker build failed' in str(test_result.get('validation', {}).get('issues', [])):
            hints.append(
                "**Docker build or start failed.** This is NOT a test issue - the Docker image failed to build or containers failed to start. "
                "Check Dockerfile syntax, base image availability, and docker-compose.yaml configuration."
            )

        # Only func or only vuln tests found
        if func_total == 0 and vuln_total > 0:
            hints.append(
                "**No func tests found.** Parser looks for files matching `test_func*.py` or `test_functionality*.py`. "
                "Ensure the functionality test file is named correctly."
            )
        if vuln_total == 0 and func_total > 0:
            hints.append(
                "**No vuln tests found.** Parser looks for files matching `test_vuln*.py` or `test_vulnerability*.py`. "
                "Ensure the vulnerability test file is named correctly."
            )

        # Timeout
        if 'Timeout' in raw_output or exit_code == -1:
            if 'Timeout after' in raw_output:
                hints.append(
                    "**Test execution timed out.** The tests or service startup took too long. "
                    "Consider adding health check waits in run-tests.sh, or the service may be hanging/crashing on startup."
                )

        return hints

    async def _run_phase_solution_verification(
        self, cve_id: str, working_dir: Path
    ) -> AgentResult:
        """
        Phase 5: Apply Solution, Test and Verify (with retry loop)

        Flow for each attempt:
        1. Apply solution with restart_docker=True (stop, rebuild, start, then apply)
        2. Run tests on fixed environment (no restart needed)
        3. If all tests pass (func PASS, vuln PASS) → success, stop docker
        4. If tests fail → run Solver agent to fix, loop back

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            AgentResult with success status and error details
        """
        phase_started_at = datetime.now()
        self._log_phase(cve_id, 5, "Solution Verification")

        # Get max retries from config (default: 3)
        max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)

        # Solution → Test → Solve loop
        last_error: Optional[str] = None
        for solver_attempt in range(max_retries + 1):  # +1 for initial test without solver
            # Step 1: Apply solution
            # First attempt: Docker is already running from vulnerable verification, no need to restart
            # Subsequent attempts: Need to restart to get clean state after solver modifications
            restart_docker = solver_attempt > 0
            self.logger.info(f"[{cve_id}] Applying solution.sh (attempt {solver_attempt + 1}, restart_docker={restart_docker})...")
            solution_result = await self.script_executor.apply_solution(
                working_dir=working_dir,
                cve_id=cve_id,
                restart_docker=True
            )

            if not solution_result['success']:
                last_error = f"Solution application failed: {solution_result.get('error', 'unknown error')}"
                self.logger.warning(f"[{cve_id}] {last_error}")
                self._solution_test_results[cve_id] = {
                    'success': False,
                    'solution_applied': False,
                    'error': solution_result.get('error'),
                    'output': solution_result.get('output', '')
                }
            else:
                self.logger.info(f"[{cve_id}] Solution applied successfully")

                # Step 2: Run tests on fixed environment (no restart needed)
                self.logger.info(f"[{cve_id}] Running tests on fixed environment...")
                test_result = await self.script_executor.run_tests(
                    working_dir=working_dir,
                    cve_id=cve_id,
                    stage='fixed',
                    restart_docker=False
                )

                self._solution_test_results[cve_id] = {
                    'success': test_result.get('success', False),
                    'solution_applied': True,
                    'solution_output': solution_result.get('output', ''),
                    'test_result': test_result
                }

                if test_result.get('success'):
                    # All tests pass!
                    self.logger.info(f"[{cve_id}] Solution verified: all tests PASS")
                    # Clean up test_results.md since verification passed
                    test_results_file = working_dir / ".agent_state" / "solver_output" / "test_results.md"
                    if test_results_file.exists():
                        test_results_file.unlink()
                    # Stop docker but keep images - Checker phase still needs them
                    # Full cleanup (remove_images=True) happens in Phase 7 (Cleanup)
                    await self.script_executor.docker_stop_and_cleanup(working_dir, cve_id, remove_images=False)
                    self._record_phase(cve_id, 'solution_verification', phase_started_at, 'completed', solver_attempt + 1)
                    return AgentResult.ok('solution_verification', solver_attempt + 1)

                # Tests failed - collect issues
                issues = test_result.get('validation', {}).get('issues', [])
                last_error = f"Tests failed after solution: {'; '.join(issues)}" if issues else "Tests failed after applying solution"
                self.logger.warning(f"[{cve_id}] {last_error}")

            # Check if we've exhausted solver retries
            if solver_attempt >= max_retries:
                self.logger.error(f"[{cve_id}] Solver retries exhausted ({max_retries})")
                break

            # Run Solver agent to fix issues
            self.logger.info(f"[{cve_id}] Running Solver agent (attempt {solver_attempt + 1}/{max_retries})...")
            test_info_path = working_dir / ".agent_state" / "solver_output" / "test_results.md"
            test_info_path.parent.mkdir(parents=True, exist_ok=True)
            test_info_path.write_text(self._format_solution_results_for_agent(self._solution_test_results[cve_id]))

            message = None if solver_attempt == 0 else """
The previous fix did not resolve the issue. Tests still failed after applying solution.sh.

Please review the updated test results in:
.agent_state/solver_output/test_results.md

Analyze what went wrong and try a different approach to fix the solution.
""".strip()

            agent_result = await self._run_agent('solver', cve_id, working_dir, custom_message=message, is_retry=(solver_attempt > 0))

            # If solver reported error, stop retrying
            if not agent_result.success:
                self.logger.error(f"[{cve_id}] Solver agent reported error: {agent_result.error}")
                last_error = agent_result.error
                break

        # All retries exhausted - full cleanup
        await self.script_executor.docker_stop_and_cleanup(working_dir, cve_id, remove_images=True)
        self._record_phase(cve_id, 'solution_verification', phase_started_at, 'failed', max_retries + 1)
        return AgentResult.fail(last_error or "Solution verification failed after all retries", 'solution_verification', max_retries + 1)

    async def _run_phase_expert_verification(
        self, cve_id: str, working_dir: Path
    ) -> AgentResult:
        """
        Phase: Expert Verification - Adapt expert solution.sh to environment

        This phase is used when we have an expert-provided solution.sh that needs
        adaptation to work with our specific environment. The expert's fix logic
        is correct, but may need environmental adaptations (service restarts,
        path adjustments, build commands, etc.)

        Flow for each attempt:
        1. Run check_fixed to test current solution.sh
        2. If all tests pass → success, skip expert phase
        3. If tests fail → run Expert agent to adapt solution.sh
        4. Expert references solution_origin.sh for adaptation patterns
        5. Loop back to step 1

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            AgentResult with success status and error details
        """
        phase_started_at = datetime.now()
        self._log_phase(cve_id, 10, "Expert Verification")

        # Check if solution_origin.sh exists (required for expert phase)
        solution_origin_path = working_dir / "solution_origin.sh"
        if not solution_origin_path.exists():
            self.logger.error(f"[{cve_id}] solution_origin.sh not found, cannot run expert phase")
            return AgentResult.fail("solution_origin.sh not found", 'expert_verification', 0)

        # Get max retries from config (default: 3)
        max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)

        # Expert adaptation loop
        last_error: Optional[str] = None
        for expert_attempt in range(max_retries + 1):  # +1 for initial test without expert
            # Step 1: Apply solution and run tests
            self.logger.info(f"[{cve_id}] Running check_fixed (attempt {expert_attempt + 1})...")

            solution_result = await self.script_executor.apply_solution(
                working_dir=working_dir,
                cve_id=cve_id,
                restart_docker=True
            )

            if not solution_result['success']:
                last_error = f"Solution application failed: {solution_result.get('error', 'unknown error')}"
                self.logger.warning(f"[{cve_id}] {last_error}")
                self._expert_test_results[cve_id] = {
                    'success': False,
                    'solution_applied': False,
                    'error': solution_result.get('error'),
                    'output': solution_result.get('output', '')
                }
            else:
                self.logger.info(f"[{cve_id}] Solution applied successfully")

                # Run tests on fixed environment
                test_result = await self.script_executor.run_tests(
                    working_dir=working_dir,
                    cve_id=cve_id,
                    stage='fixed',
                    restart_docker=False
                )

                self._expert_test_results[cve_id] = {
                    'success': test_result.get('success', False),
                    'solution_applied': True,
                    'solution_output': solution_result.get('output', ''),
                    'test_result': test_result
                }

                if test_result.get('success'):
                    # All tests pass - expert adaptation successful (or not needed)
                    self.logger.info(f"[{cve_id}] Expert verification passed: all tests PASS")
                    # Clean up test_results.md
                    test_results_file = working_dir / ".agent_state" / "expert_output" / "test_results.md"
                    if test_results_file.exists():
                        test_results_file.unlink()
                    # Stop docker but keep images
                    await self.script_executor.docker_stop_and_cleanup(working_dir, cve_id, remove_images=False)
                    self._record_phase(cve_id, 'expert_verification', phase_started_at, 'completed', expert_attempt + 1)
                    return AgentResult.ok('expert_verification', expert_attempt + 1)

                # Tests failed - collect issues
                issues = test_result.get('validation', {}).get('issues', [])
                last_error = f"Tests failed after solution: {'; '.join(issues)}" if issues else "Tests failed after applying solution"
                self.logger.warning(f"[{cve_id}] {last_error}")

            # Check if we've exhausted expert retries
            if expert_attempt >= max_retries:
                self.logger.error(f"[{cve_id}] Expert retries exhausted ({max_retries})")
                break

            # Run Expert agent to adapt solution.sh
            self.logger.info(f"[{cve_id}] Running Expert agent (attempt {expert_attempt + 1}/{max_retries})...")

            # Prepare test results for expert
            test_info_path = working_dir / ".agent_state" / "expert_output" / "test_results.md"
            test_info_path.parent.mkdir(parents=True, exist_ok=True)
            test_info_path.write_text(self._format_expert_results_for_agent(self._expert_test_results.get(cve_id, {})))

            message = None if expert_attempt == 0 else """
Previous adaptation did not resolve the issue. Tests still fail after applying solution.sh.

Please review the updated test results:
.agent_state/expert_output/test_results.md

Analyze the failure cause and try a different adaptation approach. Notes:
- You may only add adaptation operations necessary for solution.sh to execute correctly (restarts, builds, path adjustments)
- You must not modify the core vulnerability fix logic
- Refer to solution_origin.sh for effective adaptation patterns in our environment
""".strip()

            agent_result = await self._run_agent('expert', cve_id, working_dir, custom_message=message, is_retry=(expert_attempt > 0))

            # If expert reported error, stop retrying
            if not agent_result.success:
                self.logger.error(f"[{cve_id}] Expert agent reported error: {agent_result.error}")
                last_error = agent_result.error
                break

        # All retries exhausted - full cleanup
        await self.script_executor.docker_stop_and_cleanup(working_dir, cve_id, remove_images=True)
        self._record_phase(cve_id, 'expert_verification', phase_started_at, 'failed', max_retries + 1)
        return AgentResult.fail(last_error or "Expert verification failed after all retries", 'expert_verification', max_retries + 1)

    def _format_expert_results_for_agent(self, result: dict) -> str:
        """Format test results as markdown for expert agent to read."""
        if not result:
            return "# Expert Test Results\n\nNo results yet.\n"

        lines = [
            "# Expert Test Results",
            "",
            "## Summary",
            f"- **Overall Success**: {result.get('success', 'N/A')}",
            f"- **Solution Applied**: {result.get('solution_applied', 'N/A')}",
            "",
        ]

        # Solution application error
        if not result.get('solution_applied'):
            lines.extend([
                "## Solution Application Failed",
                f"- **Error**: {result.get('error', 'Unknown error')}",
                "",
                "### Solution Output",
                "```",
                result.get('output', 'No output'),
                "```",
            ])
            return "\n".join(lines)

        # Solution output
        solution_output = result.get('solution_output', '')
        if solution_output:
            lines.extend([
                "## Solution Output",
                "```",
                solution_output[-2000:] if len(solution_output) > 2000 else solution_output,
                "```",
                "",
            ])

        # Test results
        test_result = result.get('test_result', {})
        if test_result:
            lines.append(self._format_test_results_for_agent(test_result, stage='fixed'))

        return "\n".join(lines)

    def _format_solution_results_for_agent(self, result: dict) -> str:
        """Format solution test results as markdown for solver agent to read."""
        if not result:
            return "# Solution Test Results\n\nNo results available.\n"

        lines = [
            "# Solution Test Results",
            "",
            "## Summary",
            f"- **Overall Success**: {result.get('success', 'N/A')}",
            f"- **Solution Applied**: {result.get('solution_applied', 'N/A')}",
            "",
        ]

        # Solution application error
        if not result.get('solution_applied'):
            solution_output = result.get('output', '') or result.get('errors', '')
            lines.extend([
                "## Solution Application Failed",
                f"- **Error**: {result.get('error', 'Unknown error')}",
                "",
                "### Diagnostic Hints",
                "- solution.sh failed to execute inside the container.",
                "- Common causes: wrong file paths in sed/patch commands, missing target files, syntax errors in the script.",
                "- solution.sh must modify source code (sed/patch/diff), NOT use pip install --upgrade.",
                "",
                "### Solution Output",
                "```",
                solution_output[-3000:] if len(solution_output) > 3000 else (solution_output or 'No output'),
                "```",
            ])
            return "\n".join(lines)

        # Solution output
        solution_output = result.get('solution_output', '')
        if solution_output:
            lines.extend([
                "## Solution Output",
                "```",
                solution_output[-3000:] if len(solution_output) > 3000 else solution_output,
                "```",
                "",
            ])

        # Test results (includes diagnostic hints via _format_test_results_for_agent)
        test_result = result.get('test_result', {})
        if test_result:
            lines.append(self._format_test_results_for_agent(test_result, stage='fixed'))

        return "\n".join(lines)

    def _format_check_results_for_checker(self, check_result: dict) -> str:
        """Format CVE check results as markdown for Checker agent to read."""
        lines = [
            "# CVE Check Results",
            "",
            "## Summary",
            f"- **Ready**: {check_result.get('ready', False)}",
            f"- **CVE ID**: {check_result.get('cve_id', 'N/A')}",
            "",
        ]

        checks = check_result.get('checks', {})

        # Files check
        files_check = checks.get('files', {})
        lines.append("## File Check")
        lines.append(f"- **Success**: {files_check.get('success', False)}")
        if files_check.get('missing'):
            lines.append("- **Missing files**:")
            for f in files_check['missing']:
                lines.append(f"  - {f}")
        lines.append("")

        # Vulnerable test
        vuln_check = checks.get('vulnerable_test', {})
        if vuln_check:
            lines.append("## Vulnerable Environment Test")
            lines.append(f"- **Success**: {vuln_check.get('success', False)}")
            if vuln_check.get('skipped'):
                lines.append("- **Skipped**: Yes")
            elif not vuln_check.get('success'):
                details = vuln_check.get('details', {})
                issues = details.get('validation', {}).get('issues', [])
                if issues:
                    lines.append("- **Issues**:")
                    for issue in issues:
                        lines.append(f"  - {issue}")

                # Add diagnostic hints
                raw_output = details.get('raw_output', '')
                hints = self._generate_checker_hints(details, raw_output, 'vulnerable')
                if hints:
                    lines.extend(["", "### Diagnostic Hints"])
                    for hint in hints:
                        lines.append(f"  - {hint}")

                if raw_output:
                    lines.extend([
                        "",
                        "### Test Output (tail)",
                        "```",
                        raw_output[-4000:] if len(raw_output) > 4000 else raw_output,
                        "```"
                    ])
                    if len(raw_output) > 4000:
                        lines.extend([
                            "",
                            "### Test Output (head - may contain build/import errors)",
                            "```",
                            raw_output[:2000],
                            "```"
                        ])
            lines.append("")

        # Solution check
        solution_check = checks.get('solution', {})
        if solution_check:
            lines.append("## Solution Application")
            lines.append(f"- **Success**: {solution_check.get('success', False)}")
            if not solution_check.get('success') and not solution_check.get('skipped'):
                details = solution_check.get('details', {})
                if details.get('error'):
                    lines.append(f"- **Error**: {details['error']}")
                lines.extend([
                    "",
                    "### Diagnostic Hints",
                    "  - solution.sh failed to execute inside the container.",
                    "  - Common causes: wrong file paths in sed/patch commands, missing target files, syntax errors.",
                    "  - solution.sh must modify source code (sed/patch/diff), NOT use pip install --upgrade.",
                ])
                output = details.get('output', '') or details.get('errors', '')
                if output:
                    lines.extend([
                        "",
                        "### Solution Output",
                        "```",
                        output[-3000:] if len(output) > 3000 else output,
                        "```"
                    ])
            lines.append("")

        # Fixed test
        fixed_check = checks.get('fixed_test', {})
        if fixed_check:
            lines.append("## Fixed Environment Test")
            lines.append(f"- **Success**: {fixed_check.get('success', False)}")
            if not fixed_check.get('success') and not fixed_check.get('skipped'):
                details = fixed_check.get('details', {})
                issues = details.get('validation', {}).get('issues', [])
                if issues:
                    lines.append("- **Issues**:")
                    for issue in issues:
                        lines.append(f"  - {issue}")

                raw_output = details.get('raw_output', '')
                hints = self._generate_checker_hints(details, raw_output, 'fixed')
                if hints:
                    lines.extend(["", "### Diagnostic Hints"])
                    for hint in hints:
                        lines.append(f"  - {hint}")

                if raw_output:
                    lines.extend([
                        "",
                        "### Test Output (tail)",
                        "```",
                        raw_output[-4000:] if len(raw_output) > 4000 else raw_output,
                        "```"
                    ])
                    if len(raw_output) > 4000:
                        lines.extend([
                            "",
                            "### Test Output (head - may contain build/import errors)",
                            "```",
                            raw_output[:2000],
                            "```"
                        ])
            lines.append("")

        return "\n".join(lines)

    def _generate_checker_hints(self, details: dict, raw_output: str, stage: str) -> list:
        """Generate diagnostic hints for checker based on failure patterns."""
        hints = []
        validation = details.get('validation', {})
        actual = validation.get('actual', {})
        func_total = actual.get('func_tests', {}).get('total', 0) if isinstance(actual.get('func_tests'), dict) else 0
        vuln_total = actual.get('vuln_tests', {}).get('total', 0) if isinstance(actual.get('vuln_tests'), dict) else 0

        if func_total == 0 and vuln_total == 0:
            hints.append(
                "**ZERO tests parsed.** run-tests.sh must use `pytest ... -rA` flag. "
                "Without `-rA`, the 'short test summary info' section is not printed and the parser cannot extract results."
            )
            if 'ModuleNotFoundError' in raw_output or 'ImportError' in raw_output:
                hints.append("**Import error detected.** A required package is missing in the container.")
            if 'Connection refused' in raw_output or 'ConnectionError' in raw_output:
                hints.append("**Connection error.** Service not running or not ready. Check health checks and startup order.")
            if 'No such file or directory' in raw_output:
                hints.append("**File not found.** Check file paths in Dockerfile COPY and run-tests.sh.")
        elif func_total == 0:
            hints.append("**No func tests found.** File must be named `test_func*.py` or `test_functionality*.py`.")
        elif vuln_total == 0:
            hints.append("**No vuln tests found.** File must be named `test_vuln*.py` or `test_vulnerability*.py`.")

        if 'Docker restart failed' in raw_output or 'Docker build failed' in raw_output:
            hints.append("**Docker build/start failure.** Not a test issue - fix Dockerfile or docker-compose.yaml first.")

        if 'Timeout after' in raw_output:
            hints.append("**Timeout.** Tests or service startup took too long. Add health check waits or investigate hangs.")

        return hints

    async def _run_phase_check(
        self, cve_id: str, working_dir: Path
    ) -> AgentResult:
        """
        Phase 6: CVE Ready Check with Checker agent

        Flow:
        1. Run check_cve_ready.py
        2. If fails → Checker fixes functional issues + reviews format → loop until pass
        3. If passes → Checker reviews format requirements only → run check again to verify

        Checker always runs to ensure both functional correctness and format compliance.

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            AgentResult with success status and error details
        """
        phase_started_at = datetime.now()
        self._log_phase(cve_id, 6, "CVE Ready Check")

        max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)
        last_error: Optional[str] = None
        check_results_path = working_dir / ".agent_state" / "checker_output" / "check_results.md"
        check_results_path.parent.mkdir(parents=True, exist_ok=True)

        workdir_info = f"Your working directory is `{working_dir.resolve()}`. All files you need to read or create are relative to this path.\n\n"
        checker_constraints = """
CRITICAL CONSTRAINTS (must enforce):
- Do NOT use mock, monkeypatch, or any form of mocking/stubbing. All tests must run against the real application.
- Tests must be DYNAMIC - they must call real running services via HTTP/network/CLI, NOT static analysis, NOT isolated function calls, NOT just importing modules.
- solution.sh must modify source code (sed/patch/diff) to fix the vulnerability. Do NOT use pip install --upgrade, apt upgrade, or any package manager upgrade.
"""

        for checker_attempt in range(max_retries + 1):
            # Step 1: Run check_cve_ready.py
            self.logger.info(f"[{cve_id}] Running CVE ready check (attempt {checker_attempt + 1})...")
            check_result = await self.script_executor.run_cve_check(
                working_dir=working_dir,
                cve_id=cve_id
            )

            check_passed = check_result['ready']

            if check_passed:
                self.logger.info(f"[{cve_id}] CVE ready check PASSED")
                # If not first attempt, this means Checker's previous work didn't break anything
                if checker_attempt > 0:
                    self.logger.info(f"[{cve_id}] Checker verification PASSED - CVE check complete!")
                    if check_results_path.exists():
                        check_results_path.unlink()
                    self._record_phase(cve_id, 'cve_check', phase_started_at, 'completed', checker_attempt + 1)
                    return AgentResult.ok('cve_check', checker_attempt + 1)
                # First attempt passed - still need Checker for format review
            else:
                checks = check_result.get('checks', {})
                failed_steps = [k for k, v in checks.items() if not v.get('success') and not v.get('skipped')]
                last_error = f"Check failed at: {', '.join(failed_steps)}"
                self.logger.warning(f"[{cve_id}] {last_error}")

            # Step 2: Prepare message for Checker based on check result
            if check_passed:
                # Check passed (first attempt) - Checker reviews format requirements only
                message = f"""{workdir_info}check_cve_ready.py has PASSED. Now review the "Other Requirements" section in your instructions:

1. Reproduction must use real source code (not mocked/simplified implementations)
2. Tests must call real running services (not static analysis or isolated function calls)
3. Dockerfile format requirements (no COPY tests/, no hardcoded proxy, etc.)
4. task.yaml must be a realistic user report (no CVE identifiers)
5. Clean up unused files in task-deps/

Review each requirement carefully and fix any issues found. When done, run check_cve_ready.py to verify nothing is broken.
{checker_constraints}"""
            else:
                # Check failed - Checker fixes functional issues + reviews format
                check_results_path.write_text(self._format_check_results_for_checker(check_result))

                if checker_attempt == 0:
                    message = f"""{workdir_info}check_cve_ready.py has FAILED. Please:

1. Read .agent_state/checker_output/check_results.md to understand what failed
2. Fix the functional issues to make the check pass
3. Also review "Other Requirements" (real source code, real service tests, Dockerfile format, etc.)

When done, run check_cve_ready.py to verify all checks pass.
{checker_constraints}"""
                else:
                    message = f"""{workdir_info}The previous fixes did not resolve all issues. check_cve_ready.py still FAILED.

Please review the updated results in .agent_state/checker_output/check_results.md

Analyze what went wrong, try a different approach, and fix the issues.
Don't forget to also check "Other Requirements" (real source code, real service tests, etc.).

When done, run check_cve_ready.py to verify.
{checker_constraints}"""

            # Check if we've exhausted retries (only for failed checks)
            if not check_passed and checker_attempt >= max_retries:
                self.logger.error(f"[{cve_id}] Checker retries exhausted ({max_retries})")
                break

            # Step 3: Run Checker agent
            self.logger.info(f"[{cve_id}] Running Checker agent (check_passed={check_passed}, attempt {checker_attempt + 1})...")

            agent_result = await self._run_agent(
                'env_finalizer', cve_id, working_dir,
                custom_message=message,
                is_retry=(checker_attempt > 0)
            )

            if not agent_result.success:
                self.logger.error(f"[{cve_id}] Checker agent reported error: {agent_result.error}")
                last_error = agent_result.error
                break

            # Continue to next iteration - check will run at the start of the loop

        # All retries exhausted
        self._record_phase(cve_id, 'cve_check', phase_started_at, 'failed', max_retries + 1)
        return AgentResult.fail(
            last_error or "CVE ready check failed after all retries",
            'cve_check',
            max_retries + 1
        )

    async def _run_phase_cleanup(self, cve_id: str, working_dir: Path) -> bool:
        """
        Phase 7: Cleanup - Orchestrator executes cleanup script

        Records phase manually since there's no agent feedback.

        Args:
            cve_id: CVE identifier
            working_dir: Working directory

        Returns:
            True if successful
        """
        phase_started_at = datetime.now()
        self._log_phase(cve_id, 7, "Cleanup")

        result = await self.script_executor.cleanup_cve_task(
            working_dir=working_dir,
            cve_id=cve_id
        )

        if not result['success']:
            self.logger.warning(f"[{cve_id}] Cleanup had issues: {result.get('error')}")

        # Record phase (cleanup always succeeds even with warnings)
        self._record_phase(cve_id, 'cleanup', phase_started_at, 'completed', 1)
        self.logger.info(f"[{cve_id}] Cleanup completed")
        return True

    def _mark_phase_failed(self, cve_id: str, result: AgentResult) -> bool:
        """Mark current phase as failed with error details and save status. Returns False for easy chaining."""
        self.tasks[cve_id].status = 'failed'
        self.tasks[cve_id].error = result.error or f"Failed at phase: {result.phase_key}"
        self._save_task_status(cve_id)
        return False

    async def _run_phase(
        self,
        phase_key: str,
        cve_id: str,
        working_dir: Path,
        cve_content: Optional[str] = None,
        custom_message: Optional[str] = None,
        is_retry: bool = False
    ) -> bool:
        """
        Run a phase and handle failure marking automatically.

        This is a convenience wrapper that:
        1. Updates task current_phase
        2. Runs _run_agent
        3. Marks failure if needed

        Returns:
            True if phase succeeded, False if failed
        """
        self.tasks[cve_id].current_phase = phase_key
        result = await self._run_agent(phase_key, cve_id, working_dir, cve_content, custom_message, is_retry)
        if not result.success:
            return self._mark_phase_failed(cve_id, result)
        return True

    async def process_cve(self, cve_id: str) -> bool:
        """
        Process a single CVE through the complete pipeline.

        Args:
            cve_id: CVE identifier (e.g., CVE-2024-12345)

        Returns:
            True if successful
        """
        self.logger.info(f"[{cve_id}] Starting CVE processing")

        # Create working directory
        working_dir = self.cve_tasks_dir / cve_id
        working_dir.mkdir(parents=True, exist_ok=True)

        # Load CVE content for analyzer
        cve_file = self.cve_input_dir / f"{cve_id}.md"
        if not cve_file.exists():
            self.logger.error(f"[{cve_id}] CVE file not found: {cve_file}")
            return False
        cve_content = cve_file.read_text()

        # Create task status with working_dir for persistence
        self.tasks[cve_id] = CVETaskStatus(
            cve_id=cve_id,
            status='in_progress',
            current_phase='initialization',
            started_at=datetime.now(),
            working_dir=working_dir
        )

        try:
            # Phase 1-3: protocol_analyzer, scenario_generator, scenario_builder
            for phase_key in ['protocol_analyzer', 'scenario_generator', 'scenario_builder']:
                content = cve_content if phase_key == 'protocol_analyzer' else None
                if not await self._run_phase(phase_key, cve_id, working_dir, content):
                    return False

            # Phase 4: Attack verification (env ready = service alive + >=1 known attack)
            # Replaces CVE-Factory's vulnerable_verification.
            self.tasks[cve_id].current_phase = 'attack_verification'
            result = await self._run_phase_vulnerable_verification(cve_id, working_dir)
            if not result.success:
                return self._mark_phase_failed(cve_id, result)

            # NOTE: solution_verification phase removed - RFCGym does not verify fixes.

            # Phase 5: Env finalizer (compliance + sanity, replaces CVE-Factory's cve_check)
            self.tasks[cve_id].current_phase = 'env_finalize'
            result = await self._run_phase_check(cve_id, working_dir)
            if not result.success:
                return self._mark_phase_failed(cve_id, result)

            # Phase 6: Cleanup
            self.tasks[cve_id].current_phase = 'cleanup'
            await self._run_phase_cleanup(cve_id, working_dir)

            # Success
            self.tasks[cve_id].status = 'completed'
            self.tasks[cve_id].completed_at = datetime.now()
            self._save_task_status(cve_id)
            self.logger.info(f"[{cve_id}] Successfully completed all phases")
            return True

        except Exception as e:
            self.tasks[cve_id].status = 'failed'
            self.tasks[cve_id].error = str(e)
            self._save_task_status(cve_id)
            self.logger.error(f"[{cve_id}] Exception: {e}", exc_info=True)
            return False

        finally:
            # Always close all sessions for this CVE
            # Catch ALL exceptions (including CancelledError) to prevent overriding the return value
            try:
                await self._close_all_sessions_for_cve(cve_id)
            except BaseException as e:
                self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

    async def process_multiple_cves(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Process multiple CVEs in parallel using sliding window (respecting max_concurrent_cves).

        Uses semaphore-based sliding window instead of batch processing:
        - As soon as one CVE completes, the next CVE starts immediately
        - More efficient than waiting for entire batch to complete

        Args:
            cve_ids: List of CVE identifiers

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        self.logger.info(f"Processing {len(cve_ids)} CVEs (max concurrent: {max_concurrent}, sliding window)")

        # Semaphore for CVE-level concurrency control
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results: Dict[str, bool] = {}

        async def process_with_semaphore(cve_id: str) -> tuple:
            """Process a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore (available: {cve_semaphore._value})")
                try:
                    result = await self.process_cve(cve_id)
                    return cve_id, result
                except Exception as e:
                    self.logger.error(f"[{cve_id}] Exception: {e}")
                    return cve_id, False

        # Launch all CVEs concurrently - semaphore controls actual parallelism
        tasks = [process_with_semaphore(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        for item in completed:
            if isinstance(item, BaseException):
                # CancelledError is BaseException, not Exception in Python 3.8+
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        return results

    def get_task_status(self, cve_id: str) -> Optional[CVETaskStatus]:
        """Get status of a CVE task"""
        return self.tasks.get(cve_id)

    def get_all_tasks(self) -> Dict[str, CVETaskStatus]:
        """Get all task statuses"""
        return self.tasks.copy()

    # ========== Two-Phase Execution Methods ==========

    async def run_phase_check(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Run Check Phase only (check_cve_ready.py + Checker agent fix loop).

        This assumes Phase 1 (Analyzer + Generator) and Phase 2 (Builder + Validator + Solver)
        have already completed. It only runs the CVE ready check with Checker agent loop.

        Args:
            cve_ids: List of CVE identifiers

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Check Phase: Running CVE ready check for {len(cve_ids)} CVEs ===")

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_check_for_cve(cve_id: str) -> tuple:
            """Run check phase for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for check phase")
                working_dir = self.cve_tasks_dir / cve_id

                # Check if working directory exists
                if not working_dir.exists():
                    self.logger.error(f"[{cve_id}] Working directory not found: {working_dir}")
                    return cve_id, False

                # Restore FileStateManager from .logs/ if exists
                file_states_path = working_dir / ".logs" / "file_states.json"
                if file_states_path.exists():
                    self.file_state_managers[cve_id] = FileStateManager.load_from_logs(working_dir)
                    self.logger.debug(f"[{cve_id}] Restored FileStateManager from .logs/")
                else:
                    self.file_state_managers[cve_id] = FileStateManager(working_dir)

                # Initialize or restore task status
                existing_status = CVETaskStatus.load_from_logs(working_dir)
                if existing_status is not None:
                    self.tasks[cve_id] = existing_status
                    self.tasks[cve_id].status = 'in_progress'
                    self.tasks[cve_id].working_dir = working_dir
                else:
                    self.tasks[cve_id] = CVETaskStatus(
                        cve_id=cve_id,
                        status='in_progress',
                        current_phase='cve_check',
                        started_at=datetime.now(),
                        working_dir=working_dir
                    )

                try:
                    # Run CVE Ready Check (check_cve_ready + Checker agent fix loop)
                    self.tasks[cve_id].current_phase = 'cve_check'
                    result = await self._run_phase_check(cve_id, working_dir)

                    if result.success:
                        self.tasks[cve_id].status = 'completed'
                        self.tasks[cve_id].completed_at = datetime.now()
                        self._save_task_status(cve_id)
                        return cve_id, True
                    else:
                        self._mark_phase_failed(cve_id, result)
                        return cve_id, False

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_check_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Check Phase Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_phase1_analysis(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Phase 1: Run Analyzer for all CVEs.

        This phase can be run separately, and results are persisted to .logs/
        for later resumption with run_phase2_remaining().

        Phase 1 includes:
        - Analyzer: Information gathering and analysis

        Args:
            cve_ids: List of CVE identifiers

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Phase 1: Running Analyzer for {len(cve_ids)} CVEs ===")

        results = {}

        # Prepare working directories and load CVE content
        working_dirs = {}
        cve_contents = {}
        for cve_id in cve_ids:
            working_dir = self.cve_tasks_dir / cve_id
            working_dir.mkdir(parents=True, exist_ok=True)
            working_dirs[cve_id] = working_dir

            # Load CVE content
            cve_file = self.cve_input_dir / f"{cve_id}.md"
            if cve_file.exists():
                cve_contents[cve_id] = cve_file.read_text()
            else:
                self.logger.error(f"[{cve_id}] CVE file not found: {cve_file}")
                cve_contents[cve_id] = None

            # Initialize task status with working_dir for persistence
            self.tasks[cve_id] = CVETaskStatus(
                cve_id=cve_id,
                status='in_progress',
                current_phase='protocol_analyzer',
                started_at=datetime.now(),
                working_dir=working_dir
            )

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)

        async def run_phase1_for_cve(cve_id: str) -> tuple:
            """Run phase 1 for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for phase 1")
                working_dir = working_dirs[cve_id]
                cve_content = cve_contents.get(cve_id)

                if cve_content is None:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = "CVE file not found"
                    self._save_task_status(cve_id)
                    return cve_id, False

                try:
                    # Phase 1: Analyzer only
                    if not await self._run_phase('protocol_analyzer', cve_id, working_dir, cve_content):
                        return cve_id, False

                    # Phase 1 completed successfully
                    # Save FileStateManager to .logs/ for phase 2
                    file_state_manager = self._get_file_state_manager(cve_id, working_dir)
                    file_state_manager.save_to_logs()
                    self._save_task_status(cve_id)
                    self.logger.info(f"[{cve_id}] Phase 1 (Analyzer) completed, state saved to .logs/")
                    return cve_id, True

                except Exception as e:
                    self.logger.error(f"[{cve_id}] Phase 1 exception: {e}")
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    return cve_id, False

                finally:
                    # Close sessions for this CVE after phase 1
                    # Catch ALL exceptions (including CancelledError) to prevent overriding the return value
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_phase1_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                # CancelledError is BaseException, not Exception in Python 3.8+
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Phase 1 Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_phase2_remaining(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Phase 2: Run remaining phases (Generator → Checker) for CVEs.

        This assumes Phase 1 (Analyzer) has already completed.
        FileStateManager is restored from .logs/file_states.json.

        Phase 2 includes:
        - Generator: Task and test creation
        - Builder: Docker environment construction
        - Vulnerable Verification: Docker + Validator loop
        - Solution Verification: Solution + Solver loop
        - Checker: Final validation
        - Cleanup: Remove temporary files
        - Judger: Quality audit

        Args:
            cve_ids: List of CVE identifiers that passed Phase 1

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Phase 2: Running remaining phases for {len(cve_ids)} CVEs ===")

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_remaining_for_cve(cve_id: str) -> tuple:
            """Run phase 2 for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for phase 2")
                working_dir = self.cve_tasks_dir / cve_id

                # Check if phase 1 outputs exist (analyzer only)
                if not (working_dir / ".agent_state" / "analyzer_output").exists():
                    self.logger.error(f"[{cve_id}] Analyzer output not found, skipping")
                    return cve_id, False

                # Restore FileStateManager from .logs/
                self.file_state_managers[cve_id] = FileStateManager.load_from_logs(working_dir)
                self.logger.debug(f"[{cve_id}] Restored FileStateManager from .logs/")

                # Try to load existing task status from .logs/ or create new one
                existing_status = CVETaskStatus.load_from_logs(working_dir)
                if existing_status is not None:
                    self.tasks[cve_id] = existing_status
                    self.tasks[cve_id].status = 'in_progress'
                    self.tasks[cve_id].working_dir = working_dir
                    self.logger.debug(f"[{cve_id}] Restored task status from .logs/")
                elif cve_id not in self.tasks:
                    self.tasks[cve_id] = CVETaskStatus(
                        cve_id=cve_id,
                        status='in_progress',
                        current_phase='scenario_generator',
                        started_at=datetime.now(),
                        working_dir=working_dir
                    )
                else:
                    self.tasks[cve_id].status = 'in_progress'
                    self.tasks[cve_id].working_dir = working_dir

                try:
                    # Phase: scenario_generator
                    if not await self._run_phase('scenario_generator', cve_id, working_dir):
                        return cve_id, False

                    file_state_manager = self._get_file_state_manager(cve_id, working_dir)
                    file_state_manager.save_to_logs()

                    # Phase: scenario_builder
                    if not await self._run_phase('scenario_builder', cve_id, working_dir):
                        return cve_id, False

                    file_state_manager.save_to_logs()

                    # Phase: attack verification (env ready oracle)
                    self.tasks[cve_id].current_phase = 'attack_verification'
                    result = await self._run_phase_vulnerable_verification(cve_id, working_dir)
                    if not result.success:
                        return cve_id, self._mark_phase_failed(cve_id, result)

                    file_state_manager.save_to_logs()

                    # solution_verification phase removed in RFCGym.

                    # Phase: env_finalize (compliance + sanity)
                    self.tasks[cve_id].current_phase = 'env_finalize'
                    result = await self._run_phase_check(cve_id, working_dir)
                    if not result.success:
                        return cve_id, self._mark_phase_failed(cve_id, result)

                    file_state_manager.save_to_logs()

                    # Phase: Cleanup
                    self.tasks[cve_id].current_phase = 'cleanup'
                    await self._run_phase_cleanup(cve_id, working_dir)

                    # NOTE: fuzzer / poc_judger are evaluation-stage phases and
                    # are NOT invoked here. The env construction pipeline ends
                    # at cleanup. Run them separately via `--fuzz` / `--judge`
                    # entrypoints once they are wired up.

                    file_state_manager.save_to_logs()

                    # Success
                    self.tasks[cve_id].status = 'completed'
                    self.tasks[cve_id].completed_at = datetime.now()
                    self._save_task_status(cve_id)
                    return cve_id, True

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    # Catch ALL exceptions (including CancelledError) to prevent overriding the return value
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_remaining_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                # CancelledError is BaseException, not Exception in Python 3.8+
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Phase 2 Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_poc_judger(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Run Judger agent for quality audit on completed CVEs.

        Judger is a read-only audit agent that:
        - Reviews CVE reproduction quality
        - Checks source code authenticity
        - Validates POC correctness
        - Audits compliance with standards
        - Outputs detailed Chinese audit report

        Args:
            cve_ids: List of CVE identifiers

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Judger Phase: Running quality audit for {len(cve_ids)} CVEs ===")

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_poc_judger_for_cve(cve_id: str) -> tuple:
            """Run judger for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for judger")
                working_dir = self.cve_tasks_dir / cve_id

                # Check if working directory exists
                if not working_dir.exists():
                    self.logger.error(f"[{cve_id}] Working directory not found: {working_dir}")
                    return cve_id, False

                # Check if CVE has been processed (has final_report.md or checker output)
                if not (working_dir / "final_report.md").exists():
                    self.logger.warning(f"[{cve_id}] final_report.md not found, CVE may not be fully processed")

                # Initialize task status
                self.tasks[cve_id] = CVETaskStatus(
                    cve_id=cve_id,
                    status='in_progress',
                    current_phase='poc_judger',
                    started_at=datetime.now(),
                    working_dir=working_dir
                )

                try:
                    # Run Judger agent
                    self.tasks[cve_id].current_phase = 'poc_judger'
                    result = await self._run_agent('poc_judger', cve_id, working_dir)

                    if result.success:
                        self.tasks[cve_id].status = 'completed'
                        self.tasks[cve_id].completed_at = datetime.now()
                        self._save_task_status(cve_id)
                        self.logger.info(f"[{cve_id}] Judger audit completed successfully")
                        return cve_id, True
                    else:
                        self.tasks[cve_id].status = 'failed'
                        self.tasks[cve_id].error = result.error
                        self._save_task_status(cve_id)
                        self.logger.error(f"[{cve_id}] Judger audit failed: {result.error}")
                        return cve_id, False

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Judger exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_poc_judger_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Judger Phase Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_changer(self, cve_ids: List[str]) -> Dict[str, bool]:
        """
        Run Changer agent to transform CVE tasks to Terminal Bench format.

        Changer workflow:
        1. Run static transformation script (tb_transformer.py)
        2. Run static format validation (tb_validator.py)
        3. Run tb nop test
        4. Run tb oracle test
        5. Run Changer agent to review and fix any issues
        6. Re-run tb tests to confirm success

        Args:
            cve_ids: List of CVE identifiers

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Changer Phase: Transforming {len(cve_ids)} CVEs to Terminal Bench format ===")

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_changer_for_cve(cve_id: str) -> tuple:
            """Run changer for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for changer")
                working_dir = self.cve_tasks_dir / cve_id

                # Check if working directory exists
                if not working_dir.exists():
                    self.logger.error(f"[{cve_id}] Working directory not found: {working_dir}")
                    return cve_id, False

                # Check required files exist
                required_files = ['Dockerfile', 'docker-compose.yaml', 'task.yaml', 'solution.sh']
                missing = [f for f in required_files if not (working_dir / f).exists()]
                if missing:
                    self.logger.error(f"[{cve_id}] Missing required files: {missing}")
                    return cve_id, False

                # Create changer output directory
                changer_output_dir = working_dir / ".agent_state" / "changer_output"
                changer_output_dir.mkdir(parents=True, exist_ok=True)

                # Initialize task status
                self.tasks[cve_id] = CVETaskStatus(
                    cve_id=cve_id,
                    status='in_progress',
                    current_phase='changer',
                    started_at=datetime.now(),
                    working_dir=working_dir
                )

                try:
                    # Step 1: Run static transformation
                    # Creates: working_dir/cve-xxxx/ (lowercase subdirectory)
                    # self.logger.info(f"[{cve_id}] Step 1: Running static transformation...")
                    transform_result = await asyncio.to_thread(
                        self.script_executor.run_tb_transform,
                        working_dir,
                        cve_id
                    )

                    if not transform_result['success']:
                        self.logger.warning(f"[{cve_id}] Static transformation had errors: {transform_result.get('errors', [])}")
                        self.tasks[cve_id].status = 'failed'
                        self.tasks[cve_id].error = f"Transform failed: {transform_result.get('errors', [])}"
                        self._save_task_status(cve_id)
                        return cve_id, False

                    # Get output directory (the lowercase subdirectory)
                    output_dir = Path(transform_result['output_dir'])
                    task_id = output_dir.name  # e.g., "cve-2025-1234"
                    # self.logger.info(f"[{cve_id}] Transformed to: {output_dir}")

                    # Step 2: Run static format validation on output_dir
                    # self.logger.info(f"[{cve_id}] Step 2: Running static format validation...")
                    validate_result = await asyncio.to_thread(
                        self.script_executor.run_tb_validate,
                        output_dir,
                        cve_id
                    )

                    if not validate_result['valid']:
                        self.logger.warning(f"[{cve_id}] Static format validation failed: {validate_result.get('checks_failed', [])}")

                    # Step 3: Changer retry loop with integrated testing
                    # Loop: test -> pass? return : changer -> next iteration
                    # range(max_retries + 1) ensures we test once more after the last changer run
                    self.tasks[cve_id].current_phase = 'changer'
                    max_retries = self.config.get('orchestrator', {}).get('max_retries', 3)

                    for attempt in range(max_retries + 1):
                        # Run nop/oracle tests
                        self.logger.info(f"[{cve_id}] Running tb tests (round {attempt}/{max_retries})...")
                        current_nop = await self.script_executor.run_tb_nop_test(
                            working_dir=working_dir,
                            cve_id=task_id
                        )
                        current_oracle = await self.script_executor.run_tb_oracle_test(
                            working_dir=working_dir,
                            cve_id=task_id
                        )

                        nop_passed = current_nop.get('test_passed')
                        oracle_passed = current_oracle.get('test_passed')

                        if nop_passed:
                            self.logger.info(f"[{cve_id}] tb nop test PASSED")
                        else:
                            self.logger.warning(f"[{cve_id}] tb nop test FAILED")

                        if oracle_passed:
                            self.logger.info(f"[{cve_id}] tb oracle test PASSED")
                        else:
                            self.logger.warning(f"[{cve_id}] tb oracle test FAILED")

                        # Check if all tests passed
                        if nop_passed and oracle_passed:
                            self.tasks[cve_id].status = 'completed'
                            self.tasks[cve_id].completed_at = datetime.now()
                            self._save_task_status(cve_id)
                            self.logger.info(f"[{cve_id}] All tests passed on round {attempt}")
                            return cve_id, True

                        # Tests failed - run changer if we have retries left
                        if attempt >= max_retries:
                            break  # No more retries

                        # Build custom_message with test results
                        nop_validation = current_nop.get('validation', {})
                        oracle_validation = current_oracle.get('validation', {})
                        nop_error = current_nop.get('error')
                        oracle_error = current_oracle.get('error')
                        workdir_info = f"Your working directory is `{working_dir.resolve()}`, all file paths are relative to this directory.\n\n"

                        if nop_passed:
                            nop_summary = "Passed"
                        elif nop_error:
                            nop_summary = f"Error - {nop_error}"
                        else:
                            nop_summary = f"Failed - {nop_validation.get('issues', ['check details'])}"

                        if oracle_passed:
                            oracle_summary = "Passed"
                        elif oracle_error:
                            oracle_summary = f"Error - {oracle_error}"
                        else:
                            oracle_summary = f"Failed (is_resolved: {oracle_validation.get('is_resolved', False)})"

                        custom_message = f"""{workdir_info}You are fixing Terminal Bench conversion issues for {task_id} (attempt {attempt + 1}/{max_retries}).

## tb nop Test Results
- **Result**: {nop_summary}
- **Expected**: func all PASS, vuln all FAIL

## tb oracle Test Results
- **Result**: {oracle_summary}
- **Expected**: is_resolved: true

Please fix issues and run tb tests to verify.
"""

                        self.logger.info(f"[{cve_id}] Running changer agent (attempt {attempt + 1}/{max_retries})...")
                        result = await self._run_agent('changer', cve_id, working_dir, custom_message=custom_message)

                        if not result.success:
                            self.logger.error(f"[{cve_id}] Changer agent error: {result.error}")
                            self.tasks[cve_id].status = 'failed'
                            self.tasks[cve_id].error = f"Changer agent error: {result.error}"
                            self._save_task_status(cve_id)
                            return cve_id, False

                    # Loop exhausted - tests still failing
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = f"Tests failing after {max_retries} changer attempts"
                    self._save_task_status(cve_id)
                    self.logger.warning(f"[{cve_id}] Changer failed after {max_retries} attempts")
                    return cve_id, False

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Changer exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_changer_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Changer Phase Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_comparer(self, cve_ids: List[str], tests_replace_dir: Path = None) -> Dict[str, bool]:
        """
        Run Comparer agent to compare test_vuln.py with patcheval_test.patch.

        Comparer evaluates test completeness:
        - BETTER: Our tests cover all official types + have extra types or stricter validation
        - EQUAL: Our tests cover all official types (or uncovered types are not necessary)
        - WORSE: Missing necessary official test types

        Args:
            cve_ids: List of CVE identifiers (e.g., ['cve-2016-1000232'])
            tests_replace_dir: Directory containing tests_replace/{cve-id}/ structure
                             Defaults to {project_root}/replace/tests_replace/

        Returns:
            Dictionary mapping CVE IDs to success status (BETTER/EQUAL = True, WORSE = False)
        """
        self.logger.info(f"=== Comparer Phase: Comparing tests for {len(cve_ids)} CVEs ===")

        # Determine tests_replace directory
        if tests_replace_dir is None:
            project_root = Path(__file__).parent.parent
            tests_replace_dir = project_root / "replace" / "tests_replace"

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_comparer_for_cve(cve_id: str) -> tuple:
            """Run comparer for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for comparer")

                # Working directory is tests_replace/{cve-id}/
                working_dir = tests_replace_dir / cve_id

                # Check if working directory exists
                if not working_dir.exists():
                    self.logger.error(f"[{cve_id}] Working directory not found: {working_dir}")
                    return cve_id, False

                # Check required files exist
                test_vuln = working_dir / "tests" / "test_vuln.py"
                patch_file = working_dir / "tests" / "patcheval_test.patch"

                if not test_vuln.exists():
                    self.logger.error(f"[{cve_id}] test_vuln.py not found: {test_vuln}")
                    return cve_id, False

                if not patch_file.exists():
                    self.logger.error(f"[{cve_id}] patcheval_test.patch not found: {patch_file}")
                    return cve_id, False

                # Create comparer output directory
                comparer_output_dir = working_dir / ".agent_state" / "comparer_output"
                comparer_output_dir.mkdir(parents=True, exist_ok=True)

                # Initialize task status
                self.tasks[cve_id] = CVETaskStatus(
                    cve_id=cve_id,
                    status='in_progress',
                    current_phase='comparer',
                    started_at=datetime.now(),
                    working_dir=working_dir
                )

                try:
                    # Run Comparer agent
                    self.tasks[cve_id].current_phase = 'comparer'
                    result = await self._run_agent('comparer', cve_id, working_dir)

                    if result.success:
                        self.tasks[cve_id].status = 'completed'
                        self.tasks[cve_id].completed_at = datetime.now()
                        self._save_task_status(cve_id)
                        self.logger.info(f"[{cve_id}] Comparer completed successfully")
                        return cve_id, True
                    else:
                        self.tasks[cve_id].status = 'failed'
                        self.tasks[cve_id].error = result.error
                        self._save_task_status(cve_id)
                        self.logger.error(f"[{cve_id}] Comparer failed: {result.error}")
                        return cve_id, False

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Comparer exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_comparer_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Comparer Phase Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results

    async def run_expert(self, cve_ids: List[str], test_and_env_replace_dir: Path = None) -> Dict[str, bool]:
        """
        Run Expert agent to adapt expert-provided solution.sh to our environment.

        Expert adapts solution.sh by referencing solution_origin.sh:
        - Can add adaptation operations (restarts, builds, path adjustments)
        - Cannot modify core vulnerability fix logic

        Args:
            cve_ids: List of CVE identifiers (e.g., ['cve-2015-1326'])
            test_and_env_replace_dir: Directory containing test_and_env_replace/{cve-id}/ structure
                                     Defaults to {project_root}/replace/test_and_env_replace/

        Returns:
            Dictionary mapping CVE IDs to success status
        """
        self.logger.info(f"=== Expert Phase: Adapting solutions for {len(cve_ids)} CVEs ===")

        # Determine test_and_env_replace directory
        if test_and_env_replace_dir is None:
            project_root = Path(__file__).parent.parent
            test_and_env_replace_dir = project_root / "replace" / "test_and_env_replace"

        # Semaphore for CVE-level concurrency control (sliding window)
        max_concurrent = self.config['orchestrator']['max_concurrent_cves']
        cve_semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def run_expert_for_cve(cve_id: str) -> tuple:
            """Run expert for a single CVE with semaphore control."""
            async with cve_semaphore:
                self.logger.debug(f"[{cve_id}] Acquired CVE semaphore for expert")

                # Working directory is test_and_env_replace/{cve-id}/
                working_dir = test_and_env_replace_dir / cve_id

                # Check if working directory exists
                if not working_dir.exists():
                    self.logger.error(f"[{cve_id}] Working directory not found: {working_dir}")
                    return cve_id, False

                # Check required files exist
                solution_sh = working_dir / "solution.sh"
                solution_origin = working_dir / "solution_origin.sh"

                if not solution_sh.exists():
                    self.logger.error(f"[{cve_id}] solution.sh not found: {solution_sh}")
                    return cve_id, False

                if not solution_origin.exists():
                    self.logger.error(f"[{cve_id}] solution_origin.sh not found: {solution_origin}")
                    return cve_id, False

                # Create expert output directory
                expert_output_dir = working_dir / ".agent_state" / "expert_output"
                expert_output_dir.mkdir(parents=True, exist_ok=True)

                # Initialize task status
                self.tasks[cve_id] = CVETaskStatus(
                    cve_id=cve_id,
                    status='in_progress',
                    current_phase='expert',
                    started_at=datetime.now(),
                    working_dir=working_dir
                )

                try:
                    # Run Expert verification phase
                    self.tasks[cve_id].current_phase = 'expert_verification'
                    result = await self._run_phase_expert_verification(cve_id, working_dir)

                    if result.success:
                        self.tasks[cve_id].status = 'completed'
                        self.tasks[cve_id].completed_at = datetime.now()
                        self._save_task_status(cve_id)
                        self.logger.info(f"[{cve_id}] Expert completed successfully")
                        return cve_id, True
                    else:
                        self.tasks[cve_id].status = 'failed'
                        self.tasks[cve_id].error = result.error
                        self._save_task_status(cve_id)
                        self.logger.error(f"[{cve_id}] Expert failed: {result.error}")
                        return cve_id, False

                except Exception as e:
                    self.tasks[cve_id].status = 'failed'
                    self.tasks[cve_id].error = str(e)
                    self._save_task_status(cve_id)
                    self.logger.error(f"[{cve_id}] Expert exception: {e}", exc_info=True)
                    return cve_id, False

                finally:
                    try:
                        await self._close_all_sessions_for_cve(cve_id)
                    except BaseException as e:
                        self.logger.debug(f"[{cve_id}] Error closing sessions: {type(e).__name__}")

        # Launch all CVEs - semaphore controls actual parallelism (sliding window)
        tasks = [run_expert_for_cve(cve_id) for cve_id in cve_ids]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, BaseException):
                self.logger.error(f"Unexpected gather exception: {type(item).__name__}: {item}")
            elif isinstance(item, tuple) and len(item) == 2:
                cve_id, result = item
                results[cve_id] = result
            else:
                self.logger.error(f"Unexpected gather result type: {type(item)}")

        # Summary
        success_count = sum(1 for v in results.values() if v)
        self.logger.info(f"=== Expert Phase Complete: {success_count}/{len(cve_ids)} succeeded ===")

        return results


async def main():
    """Example usage"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    orchestrator = AsyncOrchestrator("config.yaml")

    # Start background cleanup task
    await orchestrator.start_background_cleanup()

    try:
        # Process single CVE
        success = await orchestrator.process_cve("CVE-2024-EXAMPLE")

        logging.info(f"Result: {'Success' if success else 'Failed'}")
        logging.info(f"Status: {orchestrator.get_task_status('CVE-2024-EXAMPLE')}")
    finally:
        # Always stop background cleanup
        await orchestrator.stop_background_cleanup()


if __name__ == "__main__":
    asyncio.run(main())
