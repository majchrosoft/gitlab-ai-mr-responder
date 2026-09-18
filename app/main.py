from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


from dotenv import load_dotenv

from discussion_decision import (
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
)
from repository_manager import RepositoryManager
from state import StateStore
from test_runner import TestRunner


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
    max_auto_iterations_per_discussion: int
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
            max_auto_iterations_per_discussion=get_int_env(
                "AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION",
                1,
            ),
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
        "  Max automatic iterations/discussion: "
        f"{config.limits.max_auto_iterations_per_discussion}"
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


def run_tests_and_push(
    test_runner: TestRunner,
    git_manager: GitManager,
    project_directory: Path,
    merge_request_directory: Path,
    merge_request_iid: int,
    source_branch: str,
) -> str:
    test_result = test_runner.run(
        working_directory=merge_request_directory,
        project_directory=project_directory,
        merge_request_directory=merge_request_directory,
        merge_request_iid=merge_request_iid,
        dry_run=False,
    )

    if not test_result.success:
        raise RuntimeError(
            "Tests failed."
        )

    print()
    print(
        "      → Tests PASSED"
    )

    commit_message = (
        f"fix: address MR !{merge_request_iid} review"
    )

    print()
    print(
        "      Creating commit: "
        f"{commit_message}"
    )

    commit_result = git_manager.create_commit(
        message=commit_message,
    )

    print()
    print(
        "      Commit created: "
        f"{commit_result.commit_sha}"
    )

    print()
    print(
        "      Pushing to branch: "
        f"{source_branch}"
    )

    push_result = git_manager.push_to_branch(
        branch=source_branch,
    )

    print()
    print(
        "      Push completed: "
        f"{push_result.commit_sha}"
    )

    return commit_result.commit_sha


def retry_failed_tests(
    test_runner: TestRunner,
    state_store: StateStore,
    discussion_state,
    merge_request_state,
    project_path: Path,
    merge_request_iid: int,
    source_branch: str,
) -> bool:
    repository_path = (
        project_path
        / str(merge_request_iid)
    )

    if not repository_path.exists():
        print(
            "      → Retry skipped: "
            "repository does not exist"
        )
        return False

    git_manager = GitManager(
        repository_path=repository_path,
    )

    print()
    print(
        "      → Retrying tests on existing "
        "working tree"
    )

    try:
        commit_sha = run_tests_and_push(
            test_runner=test_runner,
            git_manager=git_manager,
            project_directory=project_path,
            merge_request_directory=repository_path,
            merge_request_iid=merge_request_iid,
            source_branch=source_branch,
        )
    except RuntimeError as exc:
        print()
        print(
            f"      → Retry failed: {exc}"
        )

        state_store.record_tests_failed(
            discussion_state,
        )

        return False

    state_store.record_ai_commit(
        merge_request_state,
        commit_sha,
    )

    state_store.record_completed_iteration(
        discussion_state,
        commit_sha=commit_sha,
    )

    state_store.clear_retry_pending(
        discussion_state,
    )

    return True


def run_agent(
    config: Config,
    repository_manager: RepositoryManager,
    opencode_runner: OpenCodeRunner,
    test_runner: TestRunner,
    project_name: str,
    project_url: str,
    merge_request_iid: int,
    merge_request_title: str,
    merge_request_sha: str,
    source_branch: str,
    discussion_id: str,
    iteration: int,
    prompt: str,
) -> tuple[bool, str | None]:
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
        return True, None

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

    changes = git_manager.get_changes()

    if not changes.has_changes:
        print()
        print(
            "      → OpenCode did not modify "
            "the working tree"
        )

        return False, None

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

        return False, None

    print()
    print(
        "      → Tests PASSED"
    )

    commit_message = (
        f"fix: address MR !{merge_request_iid} review"
    )

    print()
    print(
        "      Creating commit: "
        f"{commit_message}"
    )

    commit_result = git_manager.create_commit(
        message=commit_message,
    )

    print()
    print(
        "      Commit created: "
        f"{commit_result.commit_sha}"
    )

    print()
    print(
        "      Pushing to branch: "
        f"{source_branch}"
    )

    push_result = git_manager.push_to_branch(
        branch=source_branch,
    )

    print()
    print(
        "      Push completed: "
        f"{push_result.commit_sha}"
    )

    return True, commit_result.commit_sha


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
    )

    iteration_guard = IterationGuard(
        max_auto_iterations=(
            config.limits
            .max_auto_iterations_per_discussion
        ),
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
                        f"{discussion_state.iterations} "
                        f"(auto="
                        f"{discussion_state.automatic_iterations}"
                        f", manual="
                        f"{discussion_state.manual_iterations}"
                        f")"
                    )

                    print(
                        "      Status: "
                        f"{discussion_state.status}"
                    )

                    if (
                        discussion_state.status
                        == "tests_failed"
                        or discussion_state.retry_pending
                    ):
                        print(
                            "      → Pending retry: "
                            "tests"
                        )

                        project_path = (
                            PROJECT_ROOT
                            / "repos"
                            / project.path
                        )

                        retry_success = retry_failed_tests(
                            test_runner=test_runner,
                            state_store=state_store,
                            discussion_state=discussion_state,
                            merge_request_state=(
                                merge_request_state
                            ),
                            project_path=project_path,
                            merge_request_iid=(
                                merge_request.iid
                            ),
                            source_branch=(
                                merge_request.source_branch
                            ),
                        )

                        if retry_success:
                            print(
                                "      → Retry completed successfully"
                            )
                        else:
                            print(
                                "      → Retry will be attempted "
                                "again on next run"
                            )

                        continue

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

                        success, commit_sha = run_agent(
                            config=config,
                            repository_manager=(
                                repository_manager
                            ),
                            opencode_runner=(
                                opencode_runner
                            ),
                            test_runner=test_runner,
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
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            if success:
                                if commit_sha:
                                    state_store.record_ai_commit(
                                        merge_request_state,
                                        commit_sha,
                                    )

                                state_store.record_completed_iteration(
                                    discussion_state,
                                    commit_sha=commit_sha,
                                )
                            else:
                                state_store.record_tests_failed(
                                    discussion_state,
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

                        success, commit_sha = run_agent(
                            config=config,
                            repository_manager=(
                                repository_manager
                            ),
                            opencode_runner=(
                                opencode_runner
                            ),
                            test_runner=test_runner,
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
                        )

                        if not config.runtime.dry_run:
                            decision_engine.mark_decision_processed(
                                discussion=discussion,
                                discussion_state=(
                                    discussion_state
                                ),
                                decision=decision,
                            )

                            if success:
                                if commit_sha:
                                    state_store.record_ai_commit(
                                        merge_request_state,
                                        commit_sha,
                                    )

                                state_store.record_completed_iteration(
                                    discussion_state,
                                    commit_sha=commit_sha,
                                )
                            else:
                                state_store.record_tests_failed(
                                    discussion_state,
                                )

                    elif (
                        decision.action
                        == DiscussionAction.WAIT
                    ):
                        print(
                            "      → Waiting for new "
                            "human input"
                        )

                    elif (
                        decision.action
                        == DiscussionAction.BLOCKED
                    ):
                        print(
                            "      → Discussion is blocked "
                            "by iteration limits"
                        )

                    elif (
                        decision.action
                        == DiscussionAction.IGNORE
                    ):
                        print(
                            "      → Ignoring discussion"
                        )

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
