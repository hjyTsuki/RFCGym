# Future Work

Planned enhancements for CVE-Factory.

## Intelligent Rollback

When an agent fails repeatedly (3+ times), trigger analysis-driven rollback:

1. **Failure Detection**: Track consecutive failures per agent
2. **Root Cause Analysis**: Spawn analysis agent to diagnose failure patterns
3. **Strategy Adjustment**: Suggest alternative approaches (different base image, simplified setup, etc.)
4. **Targeted Rollback**: Revert specific files rather than entire pipeline

```
Agent fails 3 times
    → Spawn RollbackAnalyzer
    → Analyze error patterns
    → Suggest new approach
    → Route to responsible agent with new strategy
```

## Execution Trace

Detailed tracking for debugging and analysis:

- Full execution path per CVE
- Decision points and agent choices
- Feedback history across iterations
- Performance metrics (tokens, time, cost)

Output: `agent_trace.json` with complete audit trail.

## Git Version Control

Per-CVE Git repositories for:

- **Commit after each agent**: Track changes precisely
- **Easy rollback**: Revert to any agent's state
- **Diff analysis**: Understand what each agent changed
- **Reproducibility**: Re-run from any checkpoint

```
cve_tasks/CVE-XXXX/
├── .git/                    # Per-CVE repo
│   └── ...
├── git_tracking.json        # Agent → commit mapping
└── ...
```

## Metrics Dashboard

Real-time monitoring:

- CVE processing status
- Agent success/failure rates
- Resource utilization
- Cost tracking per CVE

## Learning from Failures

Collect failure patterns to improve prompts:

- Common error categories
- Successful recovery strategies
- Prompt adjustments that helped

Use this data to refine agent prompts over time.
