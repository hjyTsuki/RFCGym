# Communication

## Why Two Protocols

CVE-Factory uses two different file formats for two different purposes. Agents share information with each other through Markdown files, and they report their execution status to the orchestrator through XML files. This dual-protocol design exists because the consumers are fundamentally different.

When the Analyzer writes guidance for the Generator, it's producing content that another AI agent will read and interpret. Markdown works perfectly for this: it handles code blocks with proper syntax highlighting, supports tables and headers for structure, and Claude processes it naturally without any special parsing. When the Generator needs to include a SQL injection payload like `' OR 1=1 --` in its guidance, it just puts it in a code block. No escaping, no special handling.

When an agent reports its status to the orchestrator, the orchestrator needs to make automated decisions: did this stage succeed? Should we proceed to the next stage? Do we need to route feedback to a previous agent? This requires reliable, structured parsing. The orchestrator is Python code, not an AI—it needs to extract specific fields predictably. XML provides this structure with CDATA sections that allow arbitrary text content without escaping concerns.

Attempting to use a single format for both purposes would compromise one or the other. If we used JSON everywhere, embedding code examples would require careful escaping that's easy to get wrong. If we used Markdown everywhere, the orchestrator would need fragile regex parsing to extract status information.

## How Agents Share Information

When an agent produces information that another agent needs, it writes that information to a Markdown file in its designated output directory. The directory structure makes the source clear, and the filename indicates the intended audience.

The Analyzer produces several files after researching a CVE:

```
.agent_state/analyzer_output/
├── public.md           # General CVE info, readable by all agents
├── for_generator.md    # Test strategy, exploit payloads, solution approach
├── for_builder.md      # Environment requirements, dependencies, versions
├── for_validator.md    # Expected test behavior, debugging hints
└── for_solver.md       # Fix mechanism, files to modify
```

Consider a concrete example. The Analyzer researches CVE-2025-12345, a SQL injection in a Python web application. It discovers:

- The vulnerability is in version 2.1.0 of ExampleApp
- The PoC shows the payload `admin'--` bypasses authentication
- The fix (in version 2.1.1) uses parameterized queries
- The application requires Python 3.8+, Flask, and SQLite

The Analyzer writes `for_generator.md`:

```markdown
## Test Strategy

The vulnerability is in the login endpoint. The test should:
1. Attempt login with a SQL injection payload like `admin'--`
2. Verify that authentication is bypassed (returns 200 instead of 401)

## Solution Approach

Replace string concatenation with parameterized queries:
```python
# Vulnerable
cursor.execute(f"SELECT * FROM users WHERE username='{username}'")

# Fixed
cursor.execute("SELECT * FROM users WHERE username=?", (username,))
```
```

And `for_builder.md`:

```markdown
## Environment Requirements

- Python 3.8 or higher
- Flask web framework
- SQLite database
- The application runs on port 5000

## Source Code Location

The vulnerable code is in `app/auth.py`. You'll need to:
1. Set up a virtual environment
2. Install dependencies from requirements.txt
3. Initialize the database with the provided schema
```

The Generator reads `for_generator.md` and creates the test files. The Builder reads `for_builder.md` and creates the Dockerfile. Each agent gets focused, relevant information without being overwhelmed by details it doesn't need.

## Information Isolation for the Builder

The Builder operates under deliberate information constraints. It can read `public.md` and `for_builder.md` from the Analyzer, and it can read `task.yaml` and `docker_requirements.md` from the Generator. But it cannot read `tests/` or `solution.sh`.

This restriction is enforced at the SDK level, not only through prompt instructions. When the Builder attempts to read a file in `tests/`, the file access controller intercepts the request and returns a denial. The Builder literally cannot see those files, no matter what it tries.

Why this constraint? Consider what happens without it. The Generator might write a test that checks for a specific error message. If the Builder can see this test, it might set up the environment to produce exactly that error message without actually running the vulnerable application. The tests would pass, but the reproduction would be fake—the vulnerability was never actually present.

By keeping the Builder blind to tests and solutions, we force it to build a genuinely functional environment. The Builder has to make the application actually work, not just satisfy specific test assertions. If there's a mismatch between what the Generator expected and what the Builder produced, the Validator will discover it later.

## How Agents Report Status

When an agent finishes its work, it writes an XML result file indicating what happened. The file uses a simple schema:

```xml
<result>
    <status>success</status>
    <message>Created Dockerfile and docker-compose.yaml. Environment ready.</message>
</result>
```

There are three possible status values:

**success**: The agent completed its task. The orchestrator should proceed to the next stage.

**error**: The agent encountered an unrecoverable problem. The orchestrator should stop processing this CVE.

**pause**: The agent found issues with files from a previous stage. The orchestrator should route feedback to the responsible agent.

A pause result includes detailed feedback about what went wrong:

```xml
<result>
    <status>pause</status>
    <feedback>
        <file>
            <name>Dockerfile</name>
            <reason><![CDATA[
The base image python:3.9-alpine doesn't include the necessary
build tools to compile the cryptography package. The pip install
fails with "error: could not build wheels for cryptography".

Need to switch to python:3.9 (Debian-based) which includes
gcc and other build dependencies, or add apk install commands
for build-base and libffi-dev.
            ]]></reason>
        </file>
    </feedback>
</result>
```

The CDATA section allows the agent to write detailed explanations with code snippets, error messages, and formatting without worrying about XML escaping. The orchestrator parses this structure and routes the feedback appropriately.

## How the Orchestrator Communicates with Agents

The orchestrator communicates with agents through the Claude Agent SDK's messaging interface. When starting an agent, the orchestrator provides:

1. **System prompt**: The agent's instruction file (e.g., `agents/builder.md`) defines its role, capabilities, and constraints.

2. **Working directory**: The CVE-specific directory where the agent reads inputs and writes outputs.

3. **Initial message**: Task-specific instructions, like "Build the Docker environment for this CVE" along with references to input files.

When an agent reports pause status with feedback, the orchestrator needs to route that feedback to the right agent. The file ownership map tracks which agent created each file. When the Validator reports that `Dockerfile` has problems, the orchestrator looks up the file's creator (the Builder) and sends a follow-up message to the Builder's existing session:

```
The Validator found issues with the Dockerfile you created:

The base image python:3.9-alpine doesn't include the necessary
build tools to compile the cryptography package. The pip install
fails with "error: could not build wheels for cryptography".

Need to switch to python:3.9 (Debian-based) which includes
gcc and other build dependencies, or add apk install commands
for build-base and libffi-dev.

Please fix the Dockerfile and rebuild.
```

Because this is a follow-up message to an existing session, the Builder retains its full conversation history. It remembers why it chose Alpine (smaller image size), understands the tradeoff now that it sees the problem, and can make an informed decision about how to fix it. This is much more effective than starting a fresh Builder session that would have to rediscover all the context.

## Feedback Routing in Practice

Let's walk through the complete feedback flow for the example above.

The Validator activates and discovers that `pytest` fails because the cryptography package couldn't compile. It identifies the root cause: the Alpine-based Python image lacks the `gcc` compiler and headers.

The Validator writes this result:

```xml
<result>
    <status>pause</status>
    <feedback>
        <file>
            <name>Dockerfile</name>
            <reason><![CDATA[
The base image python:3.9-alpine doesn't include the necessary
build tools to compile the cryptography package. The pip install
fails with "error: could not build wheels for cryptography".

Need to switch to python:3.9 (Debian-based) which includes
gcc and other build dependencies, or add apk install commands
for build-base and libffi-dev.
            ]]></reason>
        </file>
    </feedback>
</result>
```

The orchestrator reads this, looks up who created `Dockerfile` (the Builder), and sends the feedback to the Builder's session. The Builder, which remembers why it chose Alpine initially, can now update the file. It writes a corrected `Dockerfile` using the Debian-based image and reports success. The orchestrator notifies the Validator to resume its verification.

This targeted feedback mechanism is more efficient than alternatives. We could restart the entire pipeline, but that wastes the Analyzer's research. We could have the Validator fix the Dockerfile itself, but the Builder has more context about the environment setup decisions. By routing feedback to the original author, we get corrections informed by full context.

## Test Results Communication

For verification stages, the orchestrator provides test results through a specific file. Before activating the Validator, it runs the test suite and writes results to `.agent_state/validator_output/test_results.md`:

```markdown
# Test Execution Results

## test_func.py
- PASSED: test_valid_login (0.12s)
- PASSED: test_invalid_password (0.08s)
- PASSED: test_empty_fields (0.05s)

## test_vuln.py
- PASSED: test_sql_injection_blocked (0.15s)  # Should FAIL!
- PASSED: test_parameterized_queries (0.11s)  # Should FAIL!

## Summary
- test_func.py: 3 passed, 0 failed
- test_vuln.py: 2 passed, 0 failed (UNEXPECTED)

## Analysis
All vulnerability tests passed, but they should fail in a vulnerable
environment. This suggests either:
1. The application is already patched (wrong version)
2. The vulnerability tests aren't triggering the bug correctly
3. The exploit payload isn't reaching the vulnerable code path
```

The Validator reads this file and understands exactly what's wrong: the vulnerability tests passed when they should have failed. It investigates why—maybe the installed version is 2.1.1 (patched) instead of 2.1.0 (vulnerable). It adjusts the Dockerfile to pin the correct version and reports the fix.

This pattern—orchestrator executes tests and writes results, agent reads results and diagnoses issues—keeps the test execution deterministic (controlled by the orchestrator's scripts) while leveraging the agent's intelligence for diagnosis and correction.
