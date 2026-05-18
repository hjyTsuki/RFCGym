# Agent Management

## Agents as Claude Code Sessions

Each agent in CVE-Factory is not a simple API call—it's a full Claude Code session. This distinction is crucial for understanding how the system works.

When you make a typical LLM API call, you send a prompt and receive a response. If you need the model to execute code, read files, or search the web, you have to implement all that infrastructure yourself: tool definitions, response parsing, error handling, state management between calls. It's a lot of work, and it's easy to get wrong.

Claude Code provides all of this natively. A Claude Code session is a fully functional development environment where the agent can read and write files, execute shell commands, search the web, and interact with Docker—all capabilities essential for CVE reproduction. The orchestrator simply starts a session with appropriate instructions and lets the agent work autonomously.

Instead of defining "how" to do the task, our system prompts define the agent by four core pillars:
1. **Role**: The expert persona (e.g., "You are a DevOps Engineer specializing in Docker").
2. **Goal**: The definition of done (e.g., "Create a Dockerfile that reproduces the vulnerability").
3. **Resources & Actions**: What files it can read, what tools it can use (e.g., "You can run docker build, but cannot see tests/").
4. **Verification**: How success is objectively measured (e.g., "Your output must pass the check_vulnerable.py script").

This structure enables the agent to handle the "how" dynamically—exploring the codebase, debugging build failures, or restructuring the solution—just as a human developer would.

Here's a concrete example. When the Builder needs to debug why a `pip install` is failing, it might:

1. Read the Dockerfile to understand the current configuration
2. Run `docker build` to see the error message
3. Analyze the error log to identify missing dependencies
4. Realize it's an Alpine vs Debian issue
5. Edit the Dockerfile to use a different base image
6. Run `docker build` again to verify the fix

In a traditional API approach, you'd need to implement each of these capabilities and handle the back-and-forth. With Claude Code, the Builder agent just does this naturally—it's what Claude Code was designed for.

The orchestrator manages these sessions through the Claude Agent SDK. When a CVE enters the pipeline, the orchestrator creates a session for each agent type, passing the agent's instruction file as the system prompt and the CVE working directory as the execution context. The agent works autonomously within that session until it completes or encounters an issue.

## Session Persistence and Follow-ups

Sessions persist across interactions, which enables efficient iteration. When the Validator discovers that the Builder's Dockerfile has a problem, the orchestrator doesn't create a new Builder session—it sends a follow-up message to the existing one.

This matters because the Builder retains its entire conversation history. It remembers:

- Why it chose Alpine Linux (smaller image size)
- Which dependencies it tried to install
- What worked and what didn't
- How it structured the multi-stage build

When it receives feedback about the Alpine compatibility issue, it can make an informed decision. "Ah, the cryptography package needs compilation, and Alpine doesn't have the build tools by default. I could either add the build dependencies or switch to Debian. Given the other packages I'm installing, Debian is probably cleaner." A fresh session would have to rediscover all this context.

Session persistence also enables agents to learn from their own mistakes within a single CVE. If the Validator tries a fix that doesn't work, it remembers what it tried and can adjust its approach.

## File Access Control

Beyond tool permissions, the orchestrator controls which files each agent can access. This is enforced through PreToolUse hooks in the Claude Agent SDK—when an agent attempts to read or write a file, the hook intercepts the request, validates it against access rules, and either allows it or returns a denial.

This enforcement happens at the SDK level, making it impossible to circumvent through creative prompting. If the Builder's prompt said "Don't read tests/", a clever agent might find ways around that instruction. But when the file access controller blocks the read operation, there's no workaround—the operation simply fails.

The file access rules are defined in `file_access_controller.py`:

**Most agents** can read and write any file within the CVE working directory, except `.logs/` which is reserved for the orchestrator's conversation logs.

**The Builder** has additional restrictions: it cannot read `tests/` or `solution.sh`. This implements the blind building principle—the Builder must create a functional environment without knowing what tests it will face.

The Builder can read `public.md` and `for_builder.md` from the Analyzer, and it can read `task.yaml` and `docker_requirements.md` from the Generator. But it cannot read `tests/` or `solution.sh`.

**All agents** are blocked from writing to system paths like `/tmp`, `/etc`, or `/home`. They're also blocked from running dangerous commands like `docker system prune` or `rm -rf /`. These protections prevent agents from affecting resources outside their intended scope.

Here's a concrete scenario. The Builder, working on CVE-2025-12345, tries to be helpful:

```
Builder: Let me check what tests I need to make pass...
[Attempts to read tests/test_vuln.py]
```

The file access controller intercepts this and returns:

```
DENIED: Builder cannot access tests/ - blind building constraint
```

The Builder can't see the tests, so it focuses on what it can do: read the task description, understand the environment requirements, and build a functional system.

## Reading Project Scripts

Agents can read files from the project's `scripts/` directory, which contains verification scripts like `check_vulnerable.py` and `check_fixed.py`. This allows agents to understand what the orchestrator will check without being able to modify these scripts.

For example, the Validator might read `check_vulnerable.py` to understand exactly how tests are executed:

```python
# From scripts/check_vulnerable.py
def run_tests():
    # Copy tests into container
    subprocess.run(["docker", "cp", "tests/.", "container:/app/tests/"])
    # Run pytest and capture output
    result = subprocess.run(
        ["docker", "exec", "container", "pytest", "-rA", "/app/tests/"],
        capture_output=True
    )
    return parse_results(result.stdout)
```

The Validator now knows tests run from `/app/tests/` and uses pytest with the `-rA` flag. This knowledge helps it debug path issues or interpret test output correctly.

## Verification Scripts

The orchestrator doesn't trust agents to correctly report their own success. After each integration stage, it runs static verification scripts that objectively check results.

**check_vulnerable.py** runs after the Builder completes (or the Validator finishes adjustments). It:

1. Rebuilds the Docker image from the current Dockerfile
2. Starts the container
3. Copies test files into the container
4. Runs pytest on both test files
5. Parses the output to count passes and failures

Expected result for a correctly reproduced vulnerable environment:
- `test_func.py`: All tests pass (the application works)
- `test_vuln.py`: All tests fail (the vulnerability is present)

If the results don't match this pattern, the Validator activates to diagnose and fix issues.

**check_fixed.py** runs after solution.sh is applied (or the Solver finishes adjustments). It performs the same steps but expects:
- `test_func.py`: All tests pass (nothing broke)
- `test_vuln.py`: All tests pass (vulnerability is fixed)

This objective verification is crucial. An agent might misinterpret test output, make optimistic assumptions, or have bugs in its understanding. The verification scripts provide ground truth.

## Result File Parsing

Each agent writes an XML result file when it completes. The orchestrator parses these files to determine the next action.

The result file contains:
- **status**: "success", "error", or "pause"
- **message** (for success/error): Brief description of what happened
- **feedback** (for pause): Detailed information about what went wrong and which files are affected

When the status is "pause", the feedback section specifies which files have problems. The orchestrator uses the file ownership map to look up which agent created those files, then routes the feedback to that agent's session.

Here's the decision logic:

```
If status == "success":
    Proceed to next stage
If status == "error":
    Mark CVE as failed, stop processing
If status == "pause":
    For each file in feedback:
        Find the agent that created/modified this file
        Send feedback to that agent's session
        Wait for the agent to fix and report again
    Re-run verification
```

## Agent Prompt Configuration

Agent prompts are stored in the `agents/` directory and can be configured through `config.yaml`:

```yaml
agents:
  prompts:
    analyzer: "analyzer.md"
    generator: "generator.md"
    builder: "builder.md"
    validator: "validator.md"
    solver: "solver.md"
    checker: "checker.md"
```

Switching prompts requires only a configuration change:

```yaml
agents:
  prompts:
    builder: "builder_with_proxy.md"
```

The prompt loading logic falls back to default filenames if configuration is missing. If you don't specify a prompt for the builder, it uses `builder.md` by default.

## Model Configuration

Different agents can use different models. Complex analysis might benefit from more capable (but slower and more expensive) models, while straightforward tasks work well with faster models.

```yaml
models:
  default: "claude-opus-4-5-20251101"
  agent_models:
    analyzer: "claude-opus-4-5-20251101"
    generator: "claude-opus-4-5-20251101"
    builder: "claude-opus-4-5-20251101"
    validator: "claude-opus-4-5-20251101"
    solver: "claude-opus-4-5-20251101"
    checker: "claude-opus-4-5-20251101"
```

## Conversation Logging

Agent conversations are optionally saved to `.logs/` in each CVE working directory. When enabled, each agent's session produces two files:

- `{agent}_conversation.md` - Human-readable Markdown with proper formatting
- `{agent}_conversation.json` - Raw JSON for programmatic analysis

## Timeout and Concurrency

Each agent has a configurable timeout. If an agent runs too long without completing, its session is forcibly closed to prevent resource exhaustion. Concurrency limits control how many instances of each agent type can run simultaneously.

```yaml
agents:
  limits:
    analyzer: 3
    generator: 3
    builder: 10        # Docker builds are resource-intensive
    validator: 10
    solver: 10
    checker: 10

  timeouts:
    analyzer: 3600    # Web research can be slow
    generator: 1200
    builder: 1800     # Docker builds take time
    validator: 1800
    solver: 1800
    checker: 1800
```
