# Architecture

## What CVE-Factory Does

CVE-Factory takes vulnerability information and produces a complete, runnable reproduction environment. 

The input is a simple Markdown file containing a CVE description and a few reference links—often just a paragraph from NVD and some PoC URLs. 
The output is a fully functional Docker environment with tests that demonstrate the vulnerability and a solution script that fixes it. This output format is designed to be compatible with [terminal-bench](https://github.com/claude-institute/terminal-bench), a benchmark for evaluating AI agents on real-world software engineering tasks.

Here's a real example. The input file for CVE-2025-3248:

```markdown
# CVE-2025-3248

Langflow versions prior to 1.3.0 are susceptible to code injection in
the /api/v1/validate/code endpoint. A remote and unauthenticated attacker
can send crafted HTTP requests to execute arbitrary code.

## References
- https://github.com/langflow-ai/langflow/pull/6911
- https://www.horizon3.ai/attack-research/disclosures/unsafe-at-any-speed-abusing-python-exec-for-unauth-rce-in-langflow-ai/
```

CVE-Factory processes this and produces:

```
cve_tasks/CVE-2025-3248/
├── task.yaml              # Problem description (no CVE/security terms)
├── tests/
│   ├── test_func.py       # Tests that the API works normally
│   ├── test_vuln.py       # Tests that code injection is possible
│   └── run-tests.sh       # Test runner script
├── solution.sh            # Script that patches the vulnerability
├── task-deps/             # Configs and dependencies
│   └── config.json 
├── Dockerfile             # Container environment
├── docker-compose.yaml    # Container orchestration
└── final_report.md        # Summary of reproduction results
```

This transformation—from a paragraph of text to a working reproduction—is extremely difficult. The agent needs to search the web for vulnerability details, understand the exploit mechanism, write working test code, set up a Docker environment with correct dependencies, and ensure everything works together. Giving this entire task to a single Code Agent typically fails because it requires too many different skills in one context.

## Design Philosophy

CVE-Factory addresses this through three principles:

**Task Decouple & Couple**: Directly employing existing code agent frameworks like Claude Code to reproduce a CVE often fails. These tasks require transforming sparse descriptions into a complete environment, test suite, and solution. The high volume of required files and the long verification cycles typically overwhelm the agents. We address this by splitting the workflow into six stages, consisting of three isolated decoupling stages and three progressive coupling stages. The three decoupling stages break the original mission into independent tasks: Information Collection, File Generation, and Environment Construction. The three coupling stages then restore the original holistic task through progressively aligning former isolated components. Stage 4 aligns the environment with the tests to verify the vulnerability trigger. Stage 5 aligns the solution with the verified environment to fix the vulnerability. By the time Stage 6 performs the final end-to-end verification, the task difficulty is significantly reduced because the core components are already synchronized. By replacing the high-risk approach of building everything at once with this sequence of isolated generation and incremental alignment, our design ensures that the cognitive burden remains low while successfully restoring the full-scale task.

**Context Isolation & Reuse**: Each stage in CVE-Factory is assigned a specialized agent with its own independent context. To maintain focus, agents do not share dialogue histories. Instead, essential technical knowledge is transferred via distilled Markdown files. This isolation prevents technical noise from distracting agents in later stages. For instance, the extensive Docker logs generated during the Environment Construction stage are largely irrelevant to subsequent verification tasks, and including such data would quickly fill the agent's limited context window. However, we also implement a feedback mechanism for scenarios where reusing previous information improves efficiency. When an agent identifies a fundamental flaw, such as the need to reconstruct the `Dockerfile`, the system routes the failure via a "pause" signal back to the original session that created the file. This allows the original agent to leverage its memory of previous attempts and exploration history to perform intelligent fixes instead of starting the process from scratch.

**Agent Autonomy & Control**: Our agents are not restricted to predefined workflows or a limited set of operations. Instead, each agent is a full Claude Code session defined by its **Role, Goal, Resources, and Verification Criteria**. They have the liberty to explore the workspace, execute commands, and adapt to feedback autonomously. However, such high autonomy requires strict constraints to prevent agents from being misled by prior information, executing unsafe operations, or making subjective judgments. First, we maintain information asymmetry by preventing the environment-build agent from viewing tests or solutions. This "blind building" approach prevents agents from mocking the expected results and stops error propagation from the file generation stage. Second, the system strictly limits the agent's operational scope. Agents are prohibited from reading or writing files outside the designated working directory and cannot execute dangerous system commands. Finally, task completion is determined by objective static scripts rather than an agent's self-assessment.

## Orchestrator

A central Python-based Orchestrator manages the entire reproduction process. It controls the flow of CVE states, executes verification scripts, and decides when to activate specific agents. Agents interact with the Orchestrator through a structured `agent-res.xml`, which is specifically chosen to prevent syntax escaping. Upon completing a session, an agent returns one of three signals. A **continue** signal indicates that the agent considers its task complete. The Orchestrator then performs static script validation, like ensuring required files are generated. If this validation fails, the error detail is sent back to the agent for further refinement. **Error** means that the agent has determined the CVE is irreproducible, prompting the Orchestrator to terminate the process. Finally, **pause** triggers the feedback mechanism when a file requires a substantial rewrite. In this case, the agent identifies the problematic file and the reason for the revision. The Orchestrator, which maintains a file ownership map by scanning the working directory after each agent's execution, routes this request to the original creator of the file. Once the creator resolves the issue and returns its own `continue` signal, the Orchestrator notifies the paused agent that it should resume. This setup allows agents to focus purely on the output files without needing to know which other agents they are working with. The Orchestrator handles all the background work of tracking file history and connecting the right agents.

## Pipeline Execution

The multi-agent reproduction pipeline contains six stages, each with a specialized agent. Information flows between stages via structured Markdown files. A central Orchestrator governs the entire lifecycle by managing CVE state transitions, activating agents, and executing automated validation scripts to verify each stage's output.

### Stage 1: Information Collection (Analyzer)

The Analyzer agent is directly triggered to process the initial CVE description. Using web-search and fetch tools, it gathers technical details from external links and distills them into a shared `public.md` for all agents and four role-specific documents: `for_generator.md`, `for_builder.md`, `for_validator.md`, and `for_solver.md`. If the Analyzer determines that the available information is insufficient for a faithful reproduction, it issues an "error" signal via `analyzer-res.xml`, and the Orchestrator will terminate the process.

### Stage 2: File Generation (Generator)

The Generator agent utilizes the extracted intelligence to synthesize the task's logical components. It produces `task.yaml`, `test_func.py` and `test_vuln.py`, `solution.sh`, and `run-tests.sh`. Additionally, it generates a `docker_requirements.md` file, which provides technical guidance, such as file placement and service configurations, for the subsequent environment construction stage.

### Stage 3: Environment Construction (Builder)

The Builder agent focuses on infrastructure setup by exploring and executing various Docker commands to converge on a valid environment. Ultimately, it produces the `Dockerfile` and `docker-compose.yaml`. To ensure a rigorous reproduction, the Builder operates under a "blind building" constraint where it cannot access the test cases or the solution.

### Stage 4: Vulnerability Verification (Validator)

The Orchestrator initiates this stage by executing `check_env_ready`. A successful verification requires `test_vuln.py` to fail (confirming the vulnerability is present) and `test_func.py` to pass (confirming environmental stability). If these specific conditions are not met, the Validator agent is activated to diagnose and fix the environment, using `check_env_ready` as a self-check tool during its session. After the Validator issues a `continue` signal, the Orchestrator re-executes `check_env_ready`. If the environment still fails to meet the expected "fail-pass" state, the failure details are fed back to the Validator for further refinement, with a maximum of three retry attempts.

### Stage 5: Solution Verification (Solver)

Once the vulnerability is verified, the Orchestrator executes `check_fix_ready` by applying the `solution.sh` and rerun the `run-tests.sh`. Success is defined only when both `test_vuln.py` and `test_func.py` pass. If this is not achieved, the Solver agent is triggered to adjust the fix or the environment. Upon the agent's completion, the Orchestrator re-runs `check_fix_ready` to confirm the results. The pipeline only proceeds if both tests return success.

### Stage 6: Holistic Validation (Checker)

This stage begins with a `check_cve_ready` call. Regardless of the outcome, the Checker agent is activated: if the check fails, the Checker fixes the identified errors; if it passes, the agent performs a quality assurance review to remove mock code, static test suites, or potential data leakage. After the Checker completes its task, the Orchestrator performs a final end-to-end `check_cve_ready`. If successful, the CVE is officially marked as reproduced.

### Feedback Routing

Throughout the pipeline, when an agent reports problems with files from another agent, the orchestrator routes feedback precisely. The file state manager tracks which agent created or modified each file.

Example flow:
1. Validator discovers `Dockerfile` uses Alpine which lacks required build tools
2. Validator writes `validator-res.xml` with status "pause", feedback pointing to `Dockerfile`
3. Orchestrator looks up `Dockerfile` creator → Builder
4. Orchestrator sends feedback to Builder's existing session
5. Builder remembers its Alpine choice, understands the tradeoff, switches to Debian
6. Builder writes updated `Dockerfile` and reports success
7. Orchestrator notifies the paused agent (Validator) that the issue is fixed so it can resume.

This targeted routing is more efficient than restarting the pipeline or having the Validator fix Docker configuration (which isn't its expertise).

## Directory Structure

```
CVE-Factory/
├── agents/                        # Agent prompt files
│   ├── analyzer.md
│   ├── generator.md
│   ├── builder.md
│   ├── validator.md
│   ├── solver.md
│   └── checker.md
├── orchestrator/                  # Pipeline coordination
│   ├── run.py                     # Single-CVE entry point
│   ├── async_run.py               # Batch processing entry point
│   ├── async_orchestrator.py      # Multi-CVE parallel processing
│   ├── agent_runner.py            # Claude Agent SDK integration
│   ├── models.py                  # Data models and types
│   ├── script_executor.py         # Verification script runner
│   ├── tool_controller.py         # Per-agent tool permissions
│   ├── file_access_controller.py  # File visibility enforcement
│   ├── feedback_processor.py      # Issue routing between agents
│   └── file_state_manager.py      # File ownership tracking
├── scripts/                       # Verification and utility scripts
│   ├── check_vulnerable.py        # Verify vulnerability exists
│   ├── check_fixed.py             # Verify solution works
│   ├── check_cve_ready.py         # Check if CVE has required files
│   ├── check_phase1.py            # Verify Phase 1 completion
│   └── clean_phase2.py            # Clean up Phase 2 artifacts
├── reproduce_cves/                # Input: CVE information files
└── cve_tasks/                     # Output: Per-CVE working directories
    └── CVE-2025-3248/
        ├── .agent_state/          # Inter-agent communication
        │   ├── analyzer_output/
        │   │   ├── public.md
        │   │   ├── for_generator.md
        │   │   ├── for_builder.md
        │   │   ├── for_validator.md
        │   │   └── for_solver.md
        │   ├── validator_output/
        │   │   └── test_results.md
        │   ├── analyzer-res.xml
        │   ├── generator-res.xml
        │   └── ...
        ├── .logs/                 # Conversation logs (orchestrator only)
        ├── task.yaml
        ├── task-deps/
        ├── tests/                 # Hidden from Builder
        ├── solution.sh            # Hidden from Builder
        ├── Dockerfile
        ├── docker-compose.yaml
        └── final_report.md
```

## Success Criteria

A CVE reproduction succeeds when:

1. **Vulnerable state**: `test_func.py` passes (application works), `test_vuln.py` fails (exploit succeeds)

2. **Fixed state**: All tests pass (functionality intact, vulnerability patched)

3. **Quality**: No CVE identifiers in `task.yaml`, no unnecessary files, solution is minimal

The vulnerability test "failure" in step 1 is intentional—the test checks for secure behavior, which is absent when the vulnerability exists.
