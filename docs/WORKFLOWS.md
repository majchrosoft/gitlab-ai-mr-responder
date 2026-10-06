# Workflow and Management Guide

This document describes every workflow the GitLab AI MR Responder can
perform and every management lever an operator has.

It is written against the current implementation in `app/`.

---

## 1. The central rule: nothing happens without `AI_APPROVED`

The responder performs **no automated code change** unless a comment in
the discussion explicitly contains the keyword:

```text
AI_APPROVED
```

- The keyword is matched as a plain substring (case-sensitive) inside
  the comment body.
- If a new human or reviewer comment does **not** contain `AI_APPROVED`,
  the decision engine returns `WAIT` with reason
  `missing_ai_approved_keyword`, and the AI does nothing.
- The keyword is required for the **code-changing** entry paths:

  | Trigger comment contains | Result |
  |---|---|
  | `AI_APPROVED` only | `FIX` — automatic iteration (code change) |
  | `AI_APPROVED` and `AI-CONTINUE` | `CONTINUE` — manual iteration (code change) |
  | `AI_REVIEW` | `REVIEW` — read-only verification, never changes code |
  | none of the above | `WAIT` — nothing happens |

`AI-CONTINUE` on its own no longer triggers anything. A comment must
contain `AI_APPROVED` for the `AI-CONTINUE` command in the same comment
to be recognized.

`AI_REVIEW` does **not** require `AI_APPROVED` and never grants write
permission: it always performs a read-only analysis pass against the
codebase and posts a verification report back into the discussion.
When a comment contains both `AI_REVIEW` and `AI_APPROVED`, review
wins (the safe default); if it turns out a change IS necessary, the
human posts a new comment with `AI_APPROVED` to authorize a fix.

### Writing a triggering comment

Fix a review point automatically:

```text
The retry logic should use exponential backoff. AI_APPROVED
```

Ask the AI for one more manual attempt on an existing discussion:

```text
AI_APPROVED
AI-CONTINUE
```

Notes:

- The keyword can appear anywhere in the comment body.
- `AI-CONTINUE` must be a line of its own (any surrounding
  whitespace, case is normalized by uppercasing the line; the line must
  equal `AI-CONTINUE` exactly).
- Comments written by the responder itself always carry the internal
  marker `<!-- gitlab-ai-mr-responder:v1 -->`. Such comments are
  classified as the responder's own output and **never** trigger a new
  iteration. This is the loop protection: a fix comment posted by the
  AI only moves the discussion back to `WAIT`.

---

## 2. Note classification

Every note in a discussion is classified into exactly one type, in this
order:

| Type | Detection | Behaviour |
|---|---|---|
| `SYSTEM` | GitLab system note (rename/merge/etc.) | Always ignored (marked processed) |
| `AI_RESPONDER` | Body contains `<!-- gitlab-ai-mr-responder:v1 -->` | Never triggers a new iteration; discussion goes to `WAIT` |
| `AI_REVIEWER` | Body contains `<!-- klickcheck-reviewer:` | External review input; can trigger `FIX` when `AI_APPROVED` is present |
| `HUMAN` | Anything else | Treated like review input; triggers `FIX` when `AI_APPROVED` is present |

---

## 3. Decision outcomes

For each discussion, the engine picks one action per scan cycle:

| Action | Reason examples | Meaning |
|---|---|---|
| `IGNORE` | `discussion_has_no_notes`, `discussion_resolved`, `system_note` | Nothing is ever done, ever |
| `WAIT` | `no_new_notes`, `missing_ai_approved_keyword`, `waiting_for_human_response_to_ai_comment`, `waiting_for_ai_continue` | No changes; a new qualifying comment is required |
| `FIX` | `new_reviewer_comment`, `new_human_review_comment` | Automatic iteration starts (first code-change attempt) |
| `CONTINUE` | `ai_continue_command` | Manual iteration starts (code change authorized by a human) |
| `REVIEW` | `ai_review_command` | Read-only verification pass; never changes code |
| `BLOCKED` | `maximum_total_iterations_reached`, `maximum_ai_commits_per_merge_request_reached` | Hard limit reached; a human must take over |

Precedence inside `decide()`: system notes are skipped entirely; a
resolved discussion is always `IGNORE`; the responder's own comments
only `WAIT`; then `AI_REVIEW` is checked **before** `AI_APPROVED`, so a
comment containing both keywords is safely treated as review-only.

A `WAIT` caused by limits is different from a `WAIT` caused by a missing
keyword: a missing keyword stays `WAIT` (a human can still approve
later), while exhausted limits produce `BLOCKED`, which never recovers
on more comments.

---

## 4. End-to-end workflow (what happens after a trigger)

```text
human posts "<review text> ... AI_APPROVED"
        |
        v
scan picks up the note -> Decision: FIX
        |
        v
clone/fetch + checkout exact MR SHA (repos/<project>/<iid>/.git, detached)
        |
        v
verify working tree is clean, record base SHA
        |
        v
OpenCode agent edits the code (may not commit, may not push)
        |
        v
verify agent did not change the commit (still on base SHA)
        |
        +--> no file changes  -> post "no change" explanation comment -> done
        |
        v
run project tests (TEST_COMMAND)
        |
        +--> tests FAILED -> post failure comment (summary + agent report)
        |                  status: tests_failed, retry_pending: true
        |
        v
tests PASSED
        |
        v
read "BRANCH: <slug>" line from the agent report (kebab-case, sanitized)
        |
        v
create commit "fix: <slug> (MR !<iid>)"
        |
        v
git checkout -b <slug>        <-- NEW branch from the MR source commit
        |
        v
optional TEST_COMMAND_AFTER_RUN
        |
        v
git push --set-upstream --force origin HEAD:refs/heads/<slug>
        |
        v
create a NEW GitLab merge request:  <slug>  ->  <original source branch>
title "fix: <slug>", description = Problem (original comment) +
Solution (agent report) + Changed files
        |
        v
post a reply comment on the original discussion linking the new MR,
mark the original note as processed, save state
```

Important properties of this workflow:

- The AI never commits to the original MR source branch. The commit
  always lives on a fresh branch cut from the MR source commit, and the
  change reaches the source branch only through a human review of the
  new merge request.
- The branch name comes from the agent's report line
`BRANCH: <short-kebab-case-slug>` (e.g.
`BRANCH: fix-null-user-check`). It is sanitized to lowercase
kebab-case and capped at 60 characters. If the report has no usable
`BRANCH:` line, the responder posts a "no change" style comment
explaining that and pushes nothing.
- The original discussion is answered in all three outcomes (fix, test
  failure, no change), so humans always see why nothing changed.

---

## 5. Iteration model and limits

Two counters per discussion, one per merge request:

| Counter | Limit env var | Default | Behaviour when exhausted |
|---|---|---|---|
| `automatic_iterations` | `AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION` | 1 | Further comments only `WAIT` (`waiting_for_ai_continue`) — human must post `AI_APPROVED` + `AI-CONTINUE` |
| `iterations` (total) | `AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION` | 10 | Discussion becomes `BLOCKED` |
| `ai_commits` per MR | `AI_MAX_COMMITS_PER_MERGE_REQUEST` | 5 | Manual `CONTINUE` becomes `BLOCKED` |

Meaning:

- By default the AI makes **one automatic attempt** per discussion.
  After that, every further attempt requires a human who explicitly
  authorizes it by posting `AI_APPROVED` followed by an `AI-CONTINUE`
  line.
- `CONTINUE` also checks the per-MR AI commit budget; `FIX` does not
  (the MR-wide commit budget mainly throttles manual retries).
- Every processed note (and every system note) is stored by ID in the
  state file, so a note is only ever acted on once, even if the scan
  runs again (cron runs every hour).

---

## 6. Possible day-to-day workflows (recipes)

### 6.1 Normal review flow

1. Reviewer or developer evaluates the AI reviewer's comment and agrees.
2. They append the keyword to the discussion:
   `... please guard for null. AI_APPROVED`
3. On the next scan the responder fixes it, runs tests, opens a new MR
   from the freshly named branch into the original source branch, and
   links it in the thread.
4. Humans review and merge the new MR the normal way.

### 6.2 Verifying whether a review comment is even true (`AI_REVIEW`)

Sometimes you get a review comment that claims a defect, but the tests
are green and you suspect the comment is simply wrong.

Example comment:

```text
The timestamp regexp for the datetime-in-filename is invalid and the
test will fail. AI_REVIEW
```

What happens:

1. The engine returns `REVIEW` (regardless of `AI_APPROVED`).
2. The agent runs in **read-only analysis mode**: it restates the
   claim, reverse-engineers the actual code and tests (which regex,
   which test), checks the claim against the real implementation and
   the real test result, and produces a structured verdict.
3. If the agent somehow altered the working tree anyway, the
   responder hard-resets the checkout — review mode never leaves
   changes, never commits, never pushes, and creates no MR.
4. The verification report is posted as a reply to the discussion,
   including: the claim, how it was verified, an explicit
   TRUE/FALSE verdict, file/function/regex/test-name evidence, and a
   recommendation.

Reading the result:

- Verdict **FALSE** (claim mistaken, e.g. the regex is actually valid
  and the test genuinely passes) → resolve the discussion, no change
  needed.
- Verdict **TRUE** (real bug) → you can then authorize a real fix by
  posting a new comment with `AI_APPROVED` (section 6.1).

### 6.3 Rejecting an AI suggestion

Do nothing. Without `AI_APPROVED` the discussion stays `WAIT`ing forever.
Optionally resolve the discussion to make the intent explicit. A
resolved discussion is `IGNORE`d permanently.

### 6.4 Retrying a failed fix

1. The responder replied that tests failed (`tests_failed`,
   `retry_pending: true`).
2. A human reads the failure summary, decides it is worth another try
   (or fixes the environment for a transient failure), and posts:
   `AI_APPROVED` + a line `AI-CONTINUE` (plus any extra guidance).
3. Next scan runs a manual iteration with that guidance.

### 6.5 Steering a retry that went wrong

Because `CONTINUE` prompts include both the original comment and the
human's continuation comment, the human can write:

```text
You changed the wrong module. Only touch app/auth/*. AI_APPROVED
AI-CONTINUE
```

### 6.6 Taking over manually

If limits cause `BLOCKED`, or the AI keeps changing the wrong thing, a
human pushes the real fix to the original source branch and resolves
the discussion. `IGNORE` on a resolved discussion stops all future
activity on that thread.

### 6.7 Watching without acting

```dotenv
DRY_RUN=true
```

The scan still clones/fetches, checks out the SHA, and prints the exact
OpenCode command and prompt it would run — it just never modifies the
repository, never pushes, and never creates MRs.

---

## 7. State and data management

### 7.1 State file

`STATE_FILE` (default `data/state.json`) is a JSON document:

```json
{
  "merge_requests": {
    "<project_id>:<mr_iid>": {
      "ai_commits": ["<sha>", "..."],
      "discussions": {
        "<discussion_id>": {
          "discussion_id": "...",
          "iterations": 1,
          "automatic_iterations": 1,
          "manual_iterations": 0,
          "processed_note_ids": [42],
          "processed_continue_note_ids": [47],
          "last_processed_sha": "abc123...",
          "status": "completed",
          "last_ai_commit": "def456...",
          "retry_pending": false
        }
      }
    }
  }
}
```

Observed `status` values (state.py):

| Status | Meaning |
|---|---|
| `new` | not yet acted on |
| `processing` | iteration started |
| `tests_passed` / `tests_failed` | test result of the last iteration |
| `replied` | explanation comment posted |
| `committing` / `pushing` / `pushed` / `push_failed` | Git stage tracking |
| `completed` | iteration finished; counters frozen at limits |
| `blocked:<reason>` | hard limit reached |
| `replied`/`completed` + `retry_pending: true` | awaiting human `AI-CONTINUE` |

### 7.2 Repo working copies

Each MR is a full clone under `repos/<project_path>/<mr_iid>` checked
out detached at the exact MR head SHA, refreshed (`fetch --all
--prune`, `reset --hard`, `clean -fd`) on every scan. `.env` files are
moved into each project checkout before wipes. ACLs for `www-data` are
applied with `setfacl` when present.

### 7.3 Resetting runtime data

```bash
python app/main.py clear
```

Removes `repos/`, `logs/`, and the state file, preserving per-project
`.env` files across the wipe. Use it to make every processed note and
every iteration counter count as brand new again.

### 7.4 Auditing

Every scan prints the per-discussion decisions, reasons, note IDs, and
iteration counters; cron output should be redirected into `logs/` (see
the cron example in README). The logs are the primary human-level audit
trail of what was triggered and why.

---

## 8. Scheduled management (cron)

The tool is a single-shot scanner intended to be repeated:

```cron
30 * * * * flock -n /tmp/gitlab-ai-mr-responder.lock \
  sh -c 'cd /home/majcher/gitlab-ai-mr-responder && \
  /home/majcher/gitlab-ai-mr-responder/.venv/bin/python app/main.py' \
  >> /home/majcher/gitlab-ai-mr-responder/logs/cron.log 2>&1
```

- `flock -n` prevents overlapping runs (single-shot design).
- The state file makes repeated scans idempotent: processed note IDs
  and iteration counters suppress re-fixing.

---

## 9. Security model recap (operational contract)

The boundary that the code enforces:

```text
AI agent (OpenCode):
  - inspect + modify files, run development/test commands
  - MUST NOT create commits, change remotes, or push

Responder (this project):
  - verifies clean tree before agent, and uncommitted base SHA after
  - creates the commit itself
  - cuts and force-pushes the new branch
  - opens the merge request into the original source branch
```

If the agent accidentally commits, `verify_agent_commit_unchanged`
raises and the run fails closed — no push happens and an error is
reported. Failures are not silently converted into successes.

---

## 10. Configuration reference

| Variable | Meaning | Default |
|---|---|---|
| `GITLAB_URL` | GitLab base URL (`https://gitlab.example.com`) | required |
| `GITLAB_PROJECTS` | `;`-separated project URLs to scan | required |
| `GITLAB_TOKEN` | API token used for all GitLab calls | required |
| `GITLAB_SSH_PORT` | SSH clone port | 2222 |
| `OLLAMA_URL`, `OLLAMA_MODEL` | local LLM backend config (passed to OpenCode) | required |
| `OPENCODE_COMMAND` | agent binary/command | required |
| `OPENCODE_MODEL` | agent model string | required |
| `OPENCODE_AUTO_APPROVE` | pass `--auto` to OpenCode (`true`/`false`) | false |
| `TEST_COMMAND` | project test command, supports `{project_dir}`, `{project_name}`, `{mr_dir}`, `{mr_iid}`, `{base_path}` | required |
| `TEST_COMMAND_AFTER_RUN` | optional cleanup command run after a successful fix commit | unset |
| `AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION` | automatic attempts before `AI-CONTINUE` is required | 1 |
| `AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION` | total attempts before the thread is `BLOCKED` | 10 |
| `AI_MAX_COMMITS_PER_MERGE_REQUEST` | AI commits per MR before `BLOCKED` | 5 |
| `STATE_FILE` | JSON state path (relative = relative to project root) | `data/state.json` |
| `DRY_RUN` | preview mode: no agent run, no git writes, no MR creation | true |

---

## 11. Quick troubleshooting for workflows

| Symptom | Likely cause / action |
|---|---|
| Nothing happens after a valid comment | Keyword missing; typo in `AI_APPROVED` (substring match is case-sensitive); or iteration already processed (check note IDs in state) |
| `Decision: WAIT (missing_ai_approved_keyword)` forever | The comment did not contain the keyword; the AI is behaving correctly |
| AI replies but won't try again | Automatic budget (`max_auto`) exhausted — post `AI_APPROVED` + `AI-CONTINUE` |
| `BLOCKED (maximum_ai_commits_per_merge_request_reached)` | Per‑MR AI commit limit hit; human takes over or raise limit |
| "Could not determine a branch name" comment | Agent report had no `BRANCH:` line; the change is intentionally not pushed — inspect the report in the reply |
| Wrong behavior on all discussions | Local `data/state.json`; `python app/main.py clear`, fix config, rerun |
