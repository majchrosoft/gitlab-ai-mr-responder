from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


from dotenv import load_dotenv

from discussion_decision import (
    AI_COMMENT_MARKER,
    DiscussionAction,
    DiscussionDecisionEngine,
)
from git_manager import GitManager
from gitlab_client import (
    GitLabApiError,
    GitLabClient,
)
from iteration_guard import IterationGuard
from opencode_runner import OpenCodeRunner
from prompts import (
    build_continue_prompt,
    build_fix_prompt,
    build_review_prompt,
)
from repository_manager import RepositoryManager
from state import DiscussionState, StateStore
from test_runner import TestResult, TestRunner


PROJECT_ROOT = APP_ROOT.parent


@dataclass(frozen=True)
class GitLabConfig:
    url: str
    token: str
    projects: list[str]
    ssh_port: int


@dataclass(frozen=True)
class OllamaConfig:
    url: str
    model: str


@dataclass(frozen=True)
class OpenCodeConfig:
    command: str
    model: str
    auto_approve: bool


@dataclass(frozen=True)
class LimitsConfig:
    max_total_iterations_per_discussion: int
    max_commits_per_merge_request: int


@dataclass(frozen=True)
class StateConfig:
    file: Path


@dataclass(frozen=True)
class RuntimeConfig:
    dry_run: bool


@dataclass(frozen=True)
class TestConfig:
    command: str
    after_run_command: str | None


@dataclass(frozen=True)
class Config:
    gitlab: GitLabConfig
    ollama: OllamaConfig
    opencode: OpenCodeConfig
    limits: LimitsConfig
    state: StateConfig
    runtime: RuntimeConfig
    test: TestConfig


def clear_runtime_data() -> None:
    print("=" * 80)
    print("CLEARING LOCAL RUNTIME DATA")
    print("=" * 80)
    print()

    repos_dir = PROJECT_ROOT / "repos"
    logs_dir = PROJECT_ROOT / "logs"

    load_dotenv(PROJECT_ROOT / ".env")

    state_file_value = os.getenv(
        "STATE_FILE",
        "data/state.json",
    )

    state_file = Path(state_file_value)

    if not state_file.is_absolute():
        state_file = PROJECT_ROOT / state_file

    preserved_env_files: dict[Path, Path] = {}
    temp_dir = Path(
        tempfile.mkdtemp(prefix="gitlab-ai-mr-responder-env-")
    )

    try:
        if repos_dir.is_dir():
            for env_file in sorted(repos_dir.glob("*/.env")):
                if not env_file.is_file():
                    continue

                project_name = env_file.parent.name
                backup_path = temp_dir / f"{project_name}.env"
                shutil.copy2(env_file, backup_path)

                preserved_env_files[
                    env_file.parent
                ] = backup_path

                print(
                    f"  Preserving: {env_file}"
                )

        paths_to_remove = [
            repos_dir,
            logs_dir,
            state_file,
        ]

        for path in paths_to_remove:
            if not path.exists():
                print(f"  Not present: {path}")
                continue

            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

                print(f"  Removed: {path}")
            except Exception as exc:
                print(f"  FAILED: {path}: {exc}")
                raise

        repos_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        for project_directory, backup_path in (
            preserved_env_files.items()
        ):
            try:
                project_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                shutil.copy2(
                    backup_path,
                    project_directory / ".env",
                )

                print(
                    "  Restored: "
                    f"{project_directory / '.env'}"
                )
            except Exception as exc:
                print(
                    f"  FAILED to restore "
                    f"{project_directory / '.env'}: {exc}"
                )
                raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("Clear completed.")
    print()


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value.strip()


def get_optional_env(
    name: str,
) -> str | None:
    value = os.getenv(name)

    if value is None or not value.strip():
        return None

    return value.strip()


def get_int_env(
    name: str,
    default: int,
) -> int:
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable {name} must be an integer"
        ) from exc

    if parsed < 0:
        raise RuntimeError(
            f"Environment variable {name} must be >= 0"
        )

    return parsed


def get_bool_env(
    name: str,
    default: bool,
) -> bool:
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise RuntimeError(
        f"Environment variable {name} must be true/false"
    )


def get_projects() -> list[str]:
    value = get_required_env("GITLAB_PROJECTS")

    projects = [
        project.strip()
        for project in value.split(";")
        if project.strip()
    ]

    if not projects:
        raise RuntimeError(
            "GITLAB_PROJECTS must contain at least one project"
        )

    return projects


def load_config() -> Config:
    env_file = PROJECT_ROOT / ".env"

    load_dotenv(env_file)

    state_file_value = get_required_env("STATE_FILE")

    state_file = Path(state_file_value)

    if not state_file.is_absolute():
        state_file = PROJECT_ROOT / state_file

    return Config(
        gitlab=GitLabConfig(
            url=get_required_env("GITLAB_URL"),
            token=get_required_env("GITLAB_TOKEN"),
            projects=get_projects(),
            ssh_port=get_int_env(
                "GITLAB_SSH_PORT",
                2222,
            ),
        ),
        ollama=OllamaConfig(
            url=get_required_env("OLLAMA_URL"),
            model=get_required_env("OLLAMA_MODEL"),
        ),
        opencode=OpenCodeConfig(
            command=get_required_env("OPENCODE_COMMAND"),
            model=get_required_env("OPENCODE_MODEL"),
            auto_approve=get_bool_env(
                "OPENCODE_AUTO_APPROVE",
                False,
            ),
        ),
        limits=LimitsConfig(
            max_total_iterations_per_discussion=get_int_env(
                "AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION",
                10,
            ),
            max_commits_per_merge_request=get_int_env(
                "AI_MAX_COMMITS_PER_MERGE_REQUEST",
                5,
            ),
        ),
        state=StateConfig(
            file=state_file,
        ),
        runtime=RuntimeConfig(
            dry_run=get_bool_env(
                "DRY_RUN",
                True,
            ),
        ),
        test=TestConfig(
            command=get_required_env("TEST_COMMAND"),
            after_run_command=get_optional_env(
                "TEST_COMMAND_AFTER_RUN"
            ),
        ),
    )


def print_config(config: Config) -> None:
    print("Configuration:")
    print(f"  GitLab URL: {config.gitlab.url}")
    print(
        f"  GitLab projects: "
        f"{len(config.gitlab.projects)}"
    )
    print(
        f"  GitLab SSH port: "
        f"{config.gitlab.ssh_port}"
    )
    print(f"  Ollama URL: {config.ollama.url}")
    print(f"  Ollama model: {config.ollama.model}")
    print(
        f"  OpenCode command: "
        f"{config.opencode.command}"
    )
    print(
        f"  OpenCode model: "
        f"{config.opencode.model}"
    )
    print(
        f"  OpenCode auto approve: "
        f"{config.opencode.auto_approve}"
    )
    print(
        f"  Test command: "
        f"{config.test.command}"
    )
    print(
        "  Max total iterations/discussion: "
        f"{config.limits.max_total_iterations_per_discussion}"
    )
    print(
        "  Max AI commits/MR: "
        f"{config.limits.max_commits_per_merge_request}"
    )
    print(f"  State file: {config.state.file}")
    print(f"  Dry run: {config.runtime.dry_run}")
    print()


def prepare_repository(
    repository_manager: RepositoryManager,
    project_name: str,
    project_url: str,
    merge_request_iid: int,
    merge_request_sha: str,
):
    repository = repository_manager.prepare_merge_request(
        project_name=project_name,
        project_url=project_url,
        merge_request_iid=merge_request_iid,
        sha=merge_request_sha,
    )

    print(
        "      Repository: "
        f"{repository.path}"
    )

    print(
        "      Checked out SHA: "
        f"{repository.sha}"
    )

    return repository


@dataclass(frozen=True)
class AgentOutcome:
    commit_sha: str | None = None
    comment: str | None = None
    merge_request_iid: int | None = None
    merge_request_url: str | None = None


def post_explanation_comment(
    client: GitLabClient,
    config: Config,
    state_store: StateStore,
    project_id: int,
    merge_request_iid: int,
    discussion_state: DiscussionState,
    discussion_id: str,
    comment: str,
) -> None:
    if config.runtime.dry_run:
        print()
        print("      → Dry run: skipping GitLab reply")
        print(comment)
        return

    try:
        reply_note_id = client.post_discussion_reply(
            project_id=project_id,
            merge_request_iid=merge_request_iid,
            discussion_id=discussion_id,
            body=comment,
        )
    except GitLabApiError as exc:
        print(
            "      → Failed to post reply: "
            f"{exc}"
        )
        return

    if reply_note_id is not None:
        state_store.mark_note_processed(
            discussion_state,
            reply_note_id,
        )

    state_store.record_replied(discussion_state)

    print(
        "      → Posted explanation reply "
        f"to discussion {discussion_id}"
    )


def _parse_message_events(stdout: str) -> list[dict]:
    stdout = stdout.strip()

    if not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        events: list[dict] = []

        for line in stdout.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(parsed, dict):
                events.append(parsed)
            elif isinstance(parsed, list):
                events.extend(
                    item
                    for item in parsed
                    if isinstance(item, dict)
                )

        return events
    else:
        if isinstance(data, list):
            return [
                item
                for item in data
                if isinstance(item, dict)
            ]

        if isinstance(data, dict):
            return [data]

        return []


def extract_agent_report(stdout: str) -> str:
    events = _parse_message_events(stdout)

    texts: list[str] = []

    for event in events:
        raw_parts = event.get("parts")

        if not isinstance(raw_parts, list):
            part = event.get("part")

            raw_parts = (
                [part]
                if isinstance(part, dict)
                else []
            )

        for part in raw_parts:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                texts.append(part["text"])

    if texts:
        return texts[-1].strip()

    return stdout.strip()


def summarize_test_failure(
    test_result: TestResult,
    max_lines: int = 40,
) -> str:
    combined = (
        (test_result.stdout or "")
        + "\n"
        + (test_result.stderr or "")
    )

    keywords = (
        "FAILED",
        "ERROR",
        "Traceback",
        "AssertionError",
        "assert",
        "Error",
        "error",
        "failed",
        "Failed",
        "not ok",
        "✗",
        "×",
        "tests failed",
        "tests passed",
        "failed,",
        "passed,",
    )

    selected: list[str] = []

    for line in combined.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        if any(keyword in line for keyword in keywords):
            selected.append(stripped)

        if len(selected) >= max_lines:
            break

    if not selected:
        lines = [
            stripped
            for stripped in (
                line.strip()
                for line in combined.splitlines()
            )
            if stripped
        ]

        selected = lines[-max_lines:]
        selected.append(
            "(no failure markers detected, "
            "showing last output lines)"
        )

    return "\n".join(selected)


def format_failure_comment(
    agent_report: str,
    test_summary: str,
) -> str:
    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "The AI responder attempted a fix, "
        "but the tests did not pass, "
        "so no changes were pushed.\n\n"
        "Failed tests:\n"
        "```\n"
        f"{test_summary}\n"
        "```\n\n"
        f"Agent report:\n{agent_report}"
    )


def format_no_change_comment(
    agent_report: str,
) -> str:
    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "The AI responder analyzed this discussion "
        "and did not change the codebase. "
        "Explanation:\n\n"
        f"{agent_report}"
    )


def format_fix_comment(
    agent_report: str,
    changed_files: list[str],
    commit_sha: str,
    review_comment: str,
    merge_request_web_url: str,
    merge_request_iid: int,
    new_branch: str,
) -> str:
    changed_files_text = "\n".join(
        f"- {changed_file}"
        for changed_file in changed_files
    )

    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "**What was improved**\n\n"
        "This discussion was addressed with a code "
        "change. The change was pushed to a new branch "
        "and a new merge request was opened against the "
        "original source branch (it was never committed "
        "directly to the source branch).\n\n"
        f"New merge request: !{merge_request_iid}\n"
        f"{merge_request_web_url}\n\n"
        f"New branch: `{new_branch}`\n\n"
        "Addressed review comment:\n"
        f"> {review_comment.strip()}\n\n"
        "**How**\n\n"
        f"Agent summary of the change:\n\n"
        f"{agent_report}\n\n"
        "**Changed files**\n\n"
        f"{changed_files_text}\n\n"
        f"Commit: `{commit_sha}`"
    )


def sanitize_branch_slug(raw: str) -> str | None:
    slug = raw.strip().lower()

    slug = slug.replace("/", "-")

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        slug,
    )

    slug = slug.strip("-")

    if not slug:
        return None

    return slug[:60]


def extract_branch_slug(agent_report: str) -> str | None:
    slug: str | None = None

    for line in reversed(
        agent_report.splitlines()
    ):
        stripped = line.strip()

        upper = stripped.upper()

        if upper.startswith("BRANCH:"):
            slug = stripped[len("BRANCH:"):].strip()
            break

    if slug is None:
        return None

    return sanitize_branch_slug(slug)


def build_merge_request_description(
    original_iid: int,
    original_title: str,
    review_comment: str,
    agent_report: str,
    changed_files: list[str],
) -> str:
    changed_files_text = "\n".join(
        f"- {changed_file}"
        for changed_file in changed_files
    )

    return (
        "## Problem\n\n"
        f"Addressing a review comment from merge "
        f"request !{original_iid} "
        f"({original_title}).\n\n"
        f"> {review_comment.strip()}\n\n"
        "## Solution\n\n"
        "The AI responder made the following change:\n\n"
        f"{agent_report}\n\n"
        "## Changed files\n\n"
        f"{changed_files_text}"
    )


def format_blocked_comment(
    reason: str,
) -> str:
    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "The AI responder stopped working on this "
        "discussion because an iteration limit was "
        f"reached: {reason}.\n\n"
        "No further automatic analysis or fix will be "
        "attempted. All information gathered so far was "
        "already reported in the previous replies, "
        "including all possible causes of the reported "
        "issue. A human must now take over or explicitly "
        "raise the limits."
    )


def format_missing_keyword_comment(
) -> str:
    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "The AI responder did not act on this comment "
        "because it does not contain the keyword "
        "AI_APPROVED.\n\n"
        "No analysis or fix was performed yet. To "
        "authorize an automatic fix, post a new comment "
        "containing AI_APPROVED. To request a read-only "
        "verification that enumerates every possible "
        "cause of the reported issue, post a comment "
        "containing AI_REVIEW."
    )


def format_review_comment(
    agent_report: str,
) -> str:
    return (
        f"{AI_COMMENT_MARKER}\n\n"
        "The AI responder performed a read-only "
        "verification of this comment against the "
        "codebase. No files were changed.\n\n"
        "**Verification report**\n\n"
        f"{agent_report}"
    )


def run_review(
    config: Config,
    repository_manager: RepositoryManager,
    opencode_runner: OpenCodeRunner,
    project_name: str,
    project_url: str,
    merge_request_iid: int,
    merge_request_title: str,
    merge_request_sha: str,
    discussion_id: str,
    iteration: int,
    prompt: str,
) -> AgentOutcome:
    repository = prepare_repository(
        repository_manager=repository_manager,
        project_name=project_name,
        project_url=project_url,
        merge_request_iid=merge_request_iid,
        merge_request_sha=merge_request_sha,
    )

    git_manager = GitManager(
        repository_path=repository.path,
    )

    git_manager.verify_clean_before_agent()

    result = opencode_runner.run(
        prompt=prompt,
        working_directory=repository.path,
        model=config.opencode.model,
        auto_approve=config.opencode.auto_approve,
        dry_run=config.runtime.dry_run,
    )

    if config.runtime.dry_run:
        return AgentOutcome()

    print()
    print(
        "      OpenCode return code: "
        f"{result.return_code}"
    )

    if result.stdout.strip():
        print()
        print("      OpenCode output:")
        print(result.stdout.strip())

    if result.stderr.strip():
        print()
        print("      OpenCode errors:")
        print(result.stderr.strip())

    if not result.success:
        raise RuntimeError(
            "OpenCode review execution failed with "
            f"return code {result.return_code}"
        )

    agent_report = extract_agent_report(result.stdout)

    changes = git_manager.get_changes()

    if changes.has_changes:
        print()
        print(
            "      → Review mode detected working "
            "tree changes; discarding them (review is "
            "read-only)"
        )

        git_manager.discard_changes()

    return AgentOutcome(
        comment=format_review_comment(agent_report)
    )


def build_unique_branch_name(
    git_manager: GitManager,
    preferred_branch: str,
    note_id: int | None,
) -> str:
    if git_manager.is_branch_free(preferred_branch):
        return preferred_branch

    if note_id is not None:
        candidate = f"{preferred_branch}-c{note_id}"

        if git_manager.is_branch_free(candidate):
            print()
            print(
                "      Branch already exists. Using "
                f"suffixed branch: {candidate}"
            )

            return candidate

    suffix = int(time.time())

    candidate = (
        f"{preferred_branch}-"
        f"{note_id if note_id is not None else 'ai'}-{suffix}"
    )

    candidate = candidate[:80]

    if not git_manager.is_branch_free(candidate):
        raise RuntimeError(
            "Unable to build a free branch name for: "
            f"{preferred_branch}"
        )

    print()
    print(
        "      Branch already exists. Using dated "
        f"branch: {candidate}"
    )

    return candidate


def run_agent(
    config: Config,
    client: GitLabClient,
    project_id: int,
    repository_manager: RepositoryManager,
    opencode_runner: OpenCodeRunner,
    test_runner: TestRunner,
    state_store: StateStore,
    discussion_state: DiscussionState,
    project_name: str,
    project_url: str,
    merge_request_iid: int,
    merge_request_title: str,
    merge_request_sha: str,
    source_branch: str,
    discussion_id: str,
    iteration: int,
    prompt: str,
    review_comment: str,
    note_id: int | None,
) -> AgentOutcome:
    repository = prepare_repository(
        repository_manager=repository_manager,
        project_name=project_name,
        project_url=project_url,
        merge_request_iid=merge_request_iid,
        merge_request_sha=merge_request_sha,
    )

    git_manager = GitManager(
        repository_path=repository.path,
    )

    git_manager.verify_clean_before_agent()

    base_sha = git_manager.get_current_sha()

    print(
        "      Base SHA: "
        f"{base_sha}"
    )

    result = opencode_runner.run(
        prompt=prompt,
        working_directory=repository.path,
        model=config.opencode.model,
        auto_approve=config.opencode.auto_approve,
        dry_run=config.runtime.dry_run,
    )

    if config.runtime.dry_run:
        return AgentOutcome()

    print()
    print(
        "      OpenCode return code: "
        f"{result.return_code}"
    )

    if result.stdout.strip():
        print()
        print("      OpenCode output:")
        print(result.stdout.strip())

    if result.stderr.strip():
        print()
        print("      OpenCode errors:")
        print(result.stderr.strip())

    if not result.success:
        raise RuntimeError(
            "OpenCode execution failed with "
            f"return code {result.return_code}"
        )

    git_manager.verify_agent_commit_unchanged(
        base_sha=base_sha,
    )

    agent_report = extract_agent_report(result.stdout)

    changes = git_manager.get_changes()

    if not changes.has_changes:
        print()
        print(
            "      → OpenCode did not modify "
            "the working tree"
        )

        return AgentOutcome(
            comment=format_no_change_comment(
                agent_report
            )
        )

    print()
    print(
        "      Changed files: "
        f"{len(changes.changed_files)}"
    )

    for changed_file in changes.changed_files:
        print(
            f"        - {changed_file}"
        )

    if changes.diff_stat:
        print()
        print("      Diff stat:")
        print(changes.diff_stat)

    print()
    print("      Running tests...")

    test_result = test_runner.run(
        working_directory=repository.path,
        project_directory=repository.path.parent,
        merge_request_directory=repository.path,
        merge_request_iid=merge_request_iid,
        dry_run=False,
    )

    if not test_result.success:
        print()
        print(
            "      → Tests FAILED"
        )

        test_summary = summarize_test_failure(
            test_result
        )

        state_store.record_tests_failed(discussion_state)
        state_store.save()

        return AgentOutcome(
            comment=format_failure_comment(
                agent_report=agent_report,
                test_summary=test_summary,
            )
        )

    state_store.record_tests_passed(discussion_state)
    state_store.save()

    print()
    print(
        "      → Tests PASSED"
    )

    agent_slug = extract_branch_slug(agent_report)

    if agent_slug is None:
        preferred_branch = f"{source_branch}-AI_CHANGE"
    else:
        preferred_branch = agent_slug

    new_branch = build_unique_branch_name(
        git_manager=git_manager,
        preferred_branch=preferred_branch,
        note_id=note_id,
    )

    commit_message = (
        f"fix: {new_branch} "
        f"(MR !{merge_request_iid})"
    )

    print()
    print(
        "      Creating commit: "
        f"{commit_message}"
    )

    state_store.record_commit_started(discussion_state)
    state_store.save()

    commit_result = git_manager.create_commit(
        message=commit_message,
    )

    print()
    print(
        "      Commit created: "
        f"{commit_result.commit_sha}"
    )

    state_store.record_commit_created(
        discussion_state,
        commit_result.commit_sha,
    )
    state_store.save()

    print()
    print(
        "      Creating new branch from source: "
        f"{new_branch}"
    )

    state_store.record_push_started(discussion_state)
    state_store.save()

    git_manager.create_branch(
        branch=new_branch,
    )

    test_runner.run_after_run_command(
        working_directory=repository.path,
        project_directory=repository.path.parent,
        merge_request_directory=repository.path,
        merge_request_iid=merge_request_iid,
        dry_run=config.runtime.dry_run,
    )

    print()
    print(
        "      Pushing to new branch: "
        f"origin/{new_branch}"
    )

    try:
        push_result = git_manager.push_to_branch(
            branch=new_branch,
        )
    except RuntimeError as exc:
        print()
        print(
            "      → Push FAILED: "
            f"origin/{new_branch}: {exc}"
        )

        state_store.record_push_failed(discussion_state)
        state_store.save()

        raise

    print()
    print(
        "      Push completed: "
        f"{push_result.commit_sha}"
    )

    state_store.record_push_completed(discussion_state)
    state_store.save()

    print()
    print(
        "      Creating merge request into "
        f"{source_branch}"
    )

    created_merge_request = (
        client.create_merge_request(
            project_id=project_id,
            source_branch=new_branch,
            target_branch=source_branch,
            title=f"fix: {new_branch}",
            description=(
                build_merge_request_description(
                    original_iid=merge_request_iid,
                    original_title=merge_request_title,
                    review_comment=review_comment,
                    agent_report=agent_report,
                    changed_files=changes.changed_files,
                )
            ),
        )
    )

    print()
    print(
        "      Merge request created: "
        f"!{created_merge_request.iid} "
        f"{created_merge_request.web_url}"
    )

    return AgentOutcome(
        commit_sha=push_result.commit_sha,
        comment=format_fix_comment(
            agent_report=agent_report,
            changed_files=changes.changed_files,
            commit_sha=push_result.commit_sha,
            review_comment=review_comment,
            merge_request_web_url=(
                created_merge_request.web_url
            ),
            merge_request_iid=(
                created_merge_request.iid
            ),
            new_branch=new_branch,
        ),
        merge_request_iid=(
            created_merge_request.iid
        ),
        merge_request_url=(
            created_merge_request.web_url
        ),
    )


def scan_gitlab(
    config: Config,
    state_store: StateStore,
) -> None:
    client = GitLabClient(
        base_url=config.gitlab.url,
        token=config.gitlab.token,
    )

    repository_manager = RepositoryManager(
        repositories_root=PROJECT_ROOT / "repos",
        gitlab_ssh_port=config.gitlab.ssh_port,
    )

    opencode_runner = OpenCodeRunner(
        command=config.opencode.command,
    )

    test_runner = TestRunner(
        command=config.test.command,
        base_path=PROJECT_ROOT,
        after_run_command=(
            config.test.after_run_command
        ),
    )

    iteration_guard = IterationGuard(
        max_total_iterations=(
            config.limits
            .max_total_iterations_per_discussion
        ),
        max_ai_commits_per_merge_request=(
            config.limits
            .max_commits_per_merge_request
        ),
    )

    decision_engine = DiscussionDecisionEngine(
        state_store=state_store,
        iteration_guard=iteration_guard,
    )

    for project_url in config.gitlab.projects:
        print("=" * 80)
        print(f"Project: {project_url}")

        try:
            project = client.get_project(project_url)

            print(
                f"GitLab project: "
                f"{project.path_with_namespace}"
            )
            print(f"Project ID: {project.id}")

            merge_requests = (
                client.get_open_merge_requests(
                    project.id
                )
            )

            if not merge_requests:
                print("No open merge requests.")
                continue

            print(
                f"Open merge requests: "
                f"{len(merge_requests)}"
            )

            for merge_request in merge_requests:
                print()
                print(
                    f"MR !{merge_request.iid}: "
                    f"{merge_request.title}"
                )

                print(
                    f"  SHA: "
                    f"{merge_request.sha}"
                )

                print(
                    f"  Source branch: "
                    f"{merge_request.source_branch}"
                )

                print(
                    f"  Target branch: "
                    f"{merge_request.target_branch}"
                )

                merge_request_state = (
                    state_store.get_merge_request(
                        project.id,
                        merge_request.iid,
                    )
                )

                print(
                    "  AI commits: "
                    f"{len(merge_request_state.ai_commits)}"
                    f"/"
                    f"{config.limits.max_commits_per_merge_request}"
                )

                discussions = (
                    client.get_merge_request_discussions(
                        project.id,
                        merge_request.iid,
                    )
                )

                if not discussions:
                    print("  No discussions.")
                    continue

                print(
                    f"  Discussions: "
                    f"{len(discussions)}"
                )

                for discussion in discussions:
                    discussion_state = (
                        state_store.get_discussion(
                            project.id,
                            merge_request.iid,
                            discussion.id,
                        )
                    )

                    print()
                    print(
                        f"    Discussion "
                        f"{discussion.id}"
                    )

                    print(
                        f"      Notes: "
                        f"{len(discussion.notes)}"
                    )

                    print(
                        "      Iterations: "
                        f"{discussion_state.iterations}"
                    )

                    print(
                        "      Status: "
                        f"{discussion_state.status}"
                    )

                    decision = decision_engine.decide(
                        discussion=discussion,
                        discussion_state=(
                            discussion_state
                        ),
                        merge_request_state=(
                            merge_request_state
                        ),
                    )

                    print(
                        "      Decision: "
                        f"{decision.action.value.upper()}"
                    )

                    print(
                        "      Reason: "
                        f"{decision.reason}"
                    )

                    if decision.note is not None:
                        print(
                            "      Note: "
                            f"#{decision.note.id}"
                        )

                        body = (
                            decision.note.body
                            .replace("\n", " ")
                            .strip()
                        )

                        if len(body) > 200:
                            body = body[:200] + "..."

                        print(
                            "      Body: "
                            f"{body}"
                        )

                    if decision.continue_note is not None:
                        print(
                            "      Continue note: "
                            f"{decision.continue_note.id}"
                        )

                        print(
                            "      Continue author: "
                            f"{decision.continue_note.author_username}"
                        )

                    if (
                        decision.action
                        == DiscussionAction.FIX
                    ):
                        if decision.note is None:
                            print(
                                "      → FIX skipped: "
                                "no review comment"
                            )
                            continue

                        iteration = (
                            discussion_state.iterations
                            + 1
                        )

                        print(
                            "      → Starting automatic "
                            f"iteration {iteration}"
                        )

                        if not config.runtime.dry_run:
                            state_store.record_automatic_iteration(
                                discussion_state,
                                merge_request.sha,
                            )

                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            state_store.save()

                        task = build_fix_prompt(
                            project_name=project.path,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            comment=(
                                decision.note.body
                            ),
                        )

                        outcome = run_agent(
                            config=config,
                            client=client,
                            project_id=project.id,
                            repository_manager=(
                                repository_manager
                            ),
                            opencode_runner=(
                                opencode_runner
                            ),
                            test_runner=test_runner,
                            state_store=state_store,
                            discussion_state=(
                                discussion_state
                            ),
                            project_name=project.path,
                            project_url=project_url,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            source_branch=(
                                merge_request.source_branch
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            prompt=task.prompt,
                            review_comment=(
                                decision.note.body
                            ),
                            note_id=decision.note.id,
                        )

                        if not config.runtime.dry_run:
                            state_store.record_completed_iteration(
                                discussion_state,
                                commit_sha=(
                                    outcome.commit_sha
                                ),
                            )

                            state_store.save()

                            if outcome.commit_sha:
                                state_store.record_ai_commit(
                                    merge_request_state,
                                    outcome.commit_sha,
                                )

                            if outcome.comment is not None:
                                post_explanation_comment(
                                    client=client,
                                    config=config,
                                    state_store=state_store,
                                    project_id=project.id,
                                    merge_request_iid=(
                                        merge_request.iid
                                    ),
                                    discussion_state=(
                                        discussion_state
                                    ),
                                    discussion_id=(
                                        discussion.id
                                    ),
                                    comment=outcome.comment,
                                )

                    elif (
                        decision.action
                        == DiscussionAction.CONTINUE
                    ):
                        if (
                            decision.continue_note is None
                        ):
                            print(
                                "      → CONTINUE skipped: "
                                "no continuation note"
                            )
                            continue

                        original_comment = ""

                        if decision.note is not None:
                            original_comment = (
                                decision.note.body
                            )

                        iteration = (
                            discussion_state.iterations
                            + 1
                        )

                        print(
                            "      → Starting manual "
                            f"iteration {iteration}"
                        )

                        if not config.runtime.dry_run:
                            state_store.record_manual_iteration(
                                discussion_state,
                                merge_request.sha,
                            )

                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            state_store.save()

                        task = build_continue_prompt(
                            project_name=project.path,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            original_comment=(
                                original_comment
                            ),
                            continue_comment=(
                                decision.continue_note.body
                            ),
                        )

                        outcome = run_agent(
                            config=config,
                            client=client,
                            project_id=project.id,
                            repository_manager=(
                                repository_manager
                            ),
                            opencode_runner=(
                                opencode_runner
                            ),
                            test_runner=test_runner,
                            state_store=state_store,
                            discussion_state=(
                                discussion_state
                            ),
                            project_name=project.path,
                            project_url=project_url,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            source_branch=(
                                merge_request.source_branch
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            prompt=task.prompt,
                            review_comment=(
                                original_comment
                            ),
                            note_id=(
                                decision.continue_note.id
                            ),
                        )

                        if not config.runtime.dry_run:
                            state_store.record_completed_iteration(
                                discussion_state,
                                commit_sha=(
                                    outcome.commit_sha
                                ),
                            )

                            state_store.save()

                            if outcome.commit_sha:
                                state_store.record_ai_commit(
                                    merge_request_state,
                                    outcome.commit_sha,
                                )

                            if outcome.comment is not None:
                                post_explanation_comment(
                                    client=client,
                                    config=config,
                                    state_store=state_store,
                                    project_id=project.id,
                                    merge_request_iid=(
                                        merge_request.iid
                                    ),
                                    discussion_state=(
                                        discussion_state
                                    ),
                                    discussion_id=(
                                        discussion.id
                                    ),
                                    comment=outcome.comment,
                                )

                    elif (
                        decision.action
                        == DiscussionAction.REVIEW
                    ):
                        if decision.note is None:
                            print(
                                "      → REVIEW skipped: "
                                "no comment"
                            )
                            continue

                        iteration = (
                            discussion_state.iterations
                            + 1
                        )

                        print(
                            "      → Starting read-only "
                            f"review pass {iteration}"
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            state_store.save()

                        task = build_review_prompt(
                            project_name=project.path,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            comment=(
                                decision.note.body
                            ),
                        )

                        outcome = run_review(
                            config=config,
                            repository_manager=(
                                repository_manager
                            ),
                            opencode_runner=(
                                opencode_runner
                            ),
                            project_name=project.path,
                            project_url=project_url,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            merge_request_title=(
                                merge_request.title
                            ),
                            merge_request_sha=(
                                merge_request.sha
                            ),
                            discussion_id=discussion.id,
                            iteration=iteration,
                            prompt=task.prompt,
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            if outcome.comment is not None:
                                post_explanation_comment(
                                    client=client,
                                    config=config,
                                    state_store=state_store,
                                    project_id=project.id,
                                    merge_request_iid=(
                                        merge_request.iid
                                    ),
                                    discussion_state=(
                                        discussion_state
                                    ),
                                    discussion_id=(
                                        discussion.id
                                    ),
                                    comment=outcome.comment,
                                )

                    elif (
                        decision.action
                        == DiscussionAction.WAIT
                    ):
                        print(
                            "      → Waiting for new "
                            "human input"
                        )

                        if (
                            decision.reason
                            == "missing_ai_approved_keyword"
                        ):
                            print(
                                "      → Posting summary "
                                "reply: keyword missing"
                            )

                            if not config.runtime.dry_run:
                                decision_engine.mark_decision_processed(
                                    discussion=discussion,
                                    discussion_state=(
                                        discussion_state
                                    ),
                                    decision=decision,
                                )

                                state_store.save()

                                post_explanation_comment(
                                    client=client,
                                    config=config,
                                    state_store=state_store,
                                    project_id=project.id,
                                    merge_request_iid=(
                                        merge_request.iid
                                    ),
                                    discussion_state=(
                                        discussion_state
                                    ),
                                    discussion_id=(
                                        discussion.id
                                    ),
                                    comment=(
                                        format_missing_keyword_comment()
                                    ),
                                )

                    elif (
                        decision.action
                        == DiscussionAction.BLOCKED
                    ):
                        print(
                            "      → Discussion is blocked "
                            "by iteration limits"
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            state_store.save()

                            post_explanation_comment(
                                client=client,
                                config=config,
                                state_store=state_store,
                                project_id=project.id,
                                merge_request_iid=(
                                    merge_request.iid
                                ),
                                discussion_state=(
                                    discussion_state
                                ),
                                discussion_id=(
                                    discussion.id
                                ),
                                comment=(
                                    format_blocked_comment(
                                        decision.reason
                                    )
                                ),
                            )

                    elif (
                        decision.action
                        == DiscussionAction.IGNORE
                    ):
                        print(
                            "      → Ignoring discussion"
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            state_store.save()

        except GitLabApiError as exc:
            print(
                f"GitLab API error for "
                f"{project_url}: {exc}"
            )

        except Exception as exc:
            print(
                f"Unexpected error for "
                f"{project_url}: {exc}"
            )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        clear_runtime_data()
        return

    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from exc

    print_config(config)

    state_store = StateStore(
        config.state.file
    )

    scan_gitlab(
        config,
        state_store,
    )

    state_store.save()

    print()
    print("Scan completed.")


if __name__ == "__main__":
    main()
