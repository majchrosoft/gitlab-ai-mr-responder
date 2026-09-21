# GitLab AI MR Responder

AI-powered automation for reviewing and fixing GitLab Merge Request discussions with local LLMs and coding agents.

GitLab AI MR Responder connects GitLab Merge Requests with an AI coding agent, local LLM infrastructure, automated tests, and a controlled Git workflow.

> Read actionable review feedback, let an AI coding agent implement the fix, run the project's tests, and push the resulting commit back to the Merge Request — while keeping Git history and remote writes under the responder's control.

## Why this project exists

Coding agents can already inspect repositories, edit files, run commands, and reason about code. A real Merge Request workflow needs an orchestration layer around them:

```text
GitLab MR / discussion
        |
        v
 Decision engine
        |
   +----+----+
   |         |
 IGNORE     FIX / CONTINUE
             |
             v
      Prepare exact MR SHA
             |
             v
        Coding agent
             |
             v
        Detect changes
             |
             v
          Run tests
          /       \
       PASS       FAIL
        |           |
        v           v
      Commit     Retry / wait
        |
        v
      Push branch
        |
        v
       GitLab
```

## Features

### GitLab integration

- Inspect open Merge Requests.
- Read discussion threads and notes.
- Decide whether a discussion should be ignored, waited on, fixed, continued, or blocked.
- Track AI iterations and AI-created commits.
- Retry failed test runs without blindly starting another coding iteration.
- Enforce configurable per-discussion and per-MR limits.

### AI coding agent integration

The responder delegates source-code work to an external coding agent such as OpenCode.

The responder:

1. prepares the repository,
2. checks out the exact MR revision,
3. sends a focused task to the agent,
4. allows the agent to inspect and modify the repository,
5. verifies that the agent did not create the final Git commit,
6. collects the resulting changes.

### Local LLM support

Designed for self-hosted AI stacks such as:

```text
GitLab AI MR Responder
        |
        v
     OpenCode
        |
        v
      Ollama
        |
        v
 Local GPU / local LLM
```

This enables a workflow where repository source code can remain on infrastructure you control.

### Automated testing

After the coding agent changes the repository:

```text
AI changes -> test command -> PASS -> commit -> push
                         \-> FAIL -> record/retry
```

The test runner captures stdout and stderr and can detect false-positive success cases, such as a command returning exit code `0` while only displaying CLI help instead of running PHPUnit.

### Controlled Git workflow

The AI agent is not responsible for final Git history management.

The responder controls:

- commit creation,
- commit messages,
- branch pushing,
- remote branch validation,
- detection of unexpected agent-created commits,
- commit limits.

### Retry handling

Test failures can be recorded and retried later. This is useful for transient Docker, dependency, database, or service-readiness problems.

### Persistent state

Runtime state can include:

- Merge Request state,
- discussion state,
- iteration counters,
- AI commits,
- failed-test state,
- retry status.

## Architecture

```text
+-----------------------+
|        GitLab         |
| MRs / Discussions     |
+-----------+-----------+
            |
            v
+-----------------------+
|   Decision Engine     |
| IGNORE / WAIT / FIX   |
| CONTINUE / BLOCKED    |
+-----------+-----------+
            |
            v
+-----------------------+
| Repository Manager    |
| clone / fetch / SHA   |
| permissions / checkout|
+-----------+-----------+
            |
            v
+-----------------------+
|    OpenCode Runner    |
| local coding agent    |
+-----------+-----------+
            |
            v
+-----------------------+
|      Test Runner      |
| project-specific test |
| commands              |
+-----------+-----------+
            |
       +----+----+
       |         |
      PASS      FAIL
       |         |
       v         v
+----------+  +-----------+
|   Git    |  | Retry /   |
| Manager  |  | State     |
+----+-----+  +-----------+
     |
     v
+-----------------------+
|       GitLab          |
| source branch push    |
+-----------------------+
```

## Security model

The intended boundary is:

```text
AI agent:
  inspect files
  modify files
  run development commands

AI agent should NOT:
  create the final Git commit
  push the source branch

Responder:
  validate repository state
  create the commit
  verify the remote branch
  perform the push
```

For production use, consider a dedicated Unix account, least-privilege GitLab credentials, isolated repository workspaces, restricted Docker access, external secret storage, and conservative iteration/commit limits.

## Requirements

Typical requirements:

- Linux
- Python 3.11+
- Git
- GitLab with API access
- Docker
- Docker Compose
- OpenCode or another compatible coding agent
- Ollama or another local/self-hosted LLM backend

See `requirements.txt`, `pyproject.toml`, and `.env.example` for the current project configuration.

## Installation

```bash
git clone https://github.com/<YOUR_GITHUB_USERNAME>/gitlab-ai-mr-responder.git
cd gitlab-ai-mr-responder

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
vim .env
```

Never commit `.env`.

## Configuration

Example configuration:

```dotenv
GITLAB_URL=https://gitlab.example.com
GITLAB_PROJECTS=https://gitlab.example.com/group/project
GITLAB_TOKEN=your-token
GITLAB_SSH_PORT=22

OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=your-model

OPENCODE_COMMAND=/path/to/opencode
OPENCODE_MODEL=ollama/your-model
OPENCODE_AUTO_APPROVE=true

TEST_COMMAND="your test command"
TEST_COMMAND_AFTER_RUN="your cleanup command run after tests"

AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION=1
AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION=10
AI_MAX_COMMITS_PER_MERGE_REQUEST=5

STATE_FILE=data/state.json
DRY_RUN=false
```

### Test command placeholders

The test command (and the optional `TEST_COMMAND_AFTER_RUN` cleanup
command) can use:

```text
{project_dir}
{project_name}
{mr_dir}
{mr_iid}
{base_path}
```

`{project_name}` is the last directory name of `{project_dir}`.
`{base_path}` is the base path of this application.

Example:

```dotenv
TEST_COMMAND="cp {project_dir}/.env {mr_dir}/.env && cd {mr_dir} && ./runtask.sh test:feature"
```

## Running

Run manually first:

```bash
source .venv/bin/activate
python app/main.py
```

A successful workflow looks like:

```text
Scanning GitLab...

Merge Request !123
    Discussion abc123
    Action: FIX

    Preparing repository...
    Running OpenCode...

    Changes detected.
    Running tests...

    -> Tests PASSED

    Creating commit...
    Commit created: abc123...

    Pushing to branch...
    Push completed.
```

## Dry run

Use:

```dotenv
DRY_RUN=true
```

to inspect planned operations without executing the normal write workflow.

## Cron

Example:

```cron
30 * * * * flock -n /tmp/gitlab-ai-mr-responder.lock sh -c 'cd /home/majcher/gitlab-ai-mr-responder && /home/majcher/gitlab-ai-mr-responder/.venv/bin/python app/main.py' >> /home/majcher/gitlab-ai-mr-responder/logs/cron.log 2>&1
```

`flock` prevents overlapping runs.

## Runtime data

These paths are intentionally local-only:

```text
data/
logs/
repos/
.venv/
```

They should not be committed or published.

## Project layout

```text
gitlab-ai-mr-responder/
├── app/
├── config/
├── src/
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE
```

The internal module layout may evolve over time.

## Design principles

### Local-first

The project is designed to work with self-hosted models and tools.

### Agent does coding, orchestrator controls workflow

The coding agent focuses on solving the programming task. The responder focuses on GitLab, state, tests, Git, safety, and iteration control.

### Deterministic Git operations

The responder should be able to verify repository state before the agent runs, the current SHA, unexpected commits, changed files, and the remote branch being updated.

### Fail closed

Infrastructure and validation failures should not silently become successful AI iterations.

## Troubleshooting

### The AI agent starts but does not modify the repository

Check:

```bash
echo "$OPENCODE_COMMAND"
echo "$OPENCODE_MODEL"
```

Then run OpenCode manually in the MR checkout.

### Tests report success but no PHPUnit output exists

Inspect and run the configured test command manually. The responder is designed to detect some cases where a test command returns exit code `0` without actually executing tests.

### Docker test environment fails

```bash
docker ps
docker compose version
docker info
```

Then run the project test command manually.

### Git push fails

```bash
git remote -v
git status
git branch --show-current
git log --oneline -5
```

Verify that the configured credentials can update the MR source branch.

## Development

Basic syntax check:

```bash
python -m py_compile app/*.py
```

Before submitting a change:

```bash
git status
git diff
```

Do not include secrets, local MR checkouts, state files, logs, or `.venv`.

## Roadmap

Potential future work:

- richer GitLab discussion understanding,
- better structured test-failure summaries,
- multiple coding-agent backends,
- multiple LLM providers,
- repository-specific policies,
- approval gates,
- GitLab CI integration,
- structured event logging,
- metrics and observability,
- web dashboard,
- multi-project configuration,
- parallel MR processing,
- stronger sandboxing.

## Contributing

Contributions are welcome. See `CONTRIBUTING.md`.

AI-assisted contributions are welcome, but contributors remain responsible for reviewing generated code, tests, dependencies, and security implications.

## Security

See `SECURITY.md` for reporting guidance.

Never publish active credentials, GitLab tokens, private source code, or other secrets in issues or pull requests.

## License

Copyright (c) 2026 GitLab AI MR Responder contributors.

Licensed under the Apache License, Version 2.0. See `LICENSE` for the full text.

