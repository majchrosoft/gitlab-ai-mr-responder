from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class GitLabConfig:
    url: str
    token: str
    projects: list[str]


@dataclass(frozen=True)
class OllamaConfig:
    url: str
    model: str


@dataclass(frozen=True)
class OpenCodeConfig:
    command: str


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
class Config:
    gitlab: GitLabConfig
    ollama: OllamaConfig
    opencode: OpenCodeConfig
    limits: LimitsConfig
    state: StateConfig
    runtime: RuntimeConfig


def _get_required_env(name: str) -> str:
    value = os.environ.get(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable {name!r} is not set."
        )

    return value.strip()


def _get_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)

    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable {name!r} must be an integer."
        ) from exc


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)

    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"Environment variable {name!r} must be a boolean "
        f"(true/false, yes/no, 1/0)."
    )


def _validate_url(name: str, value: str) -> str:
    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            f"Environment variable {name!r} must contain a valid "
            f"http:// or https:// URL."
        )

    return value.rstrip("/")


def _get_projects() -> list[str]:
    raw = _get_required_env("GITLAB_PROJECTS")

    projects = [
        project.strip().rstrip("/")
        for project in raw.split(";")
        if project.strip()
    ]

    if not projects:
        raise RuntimeError(
            "GITLAB_PROJECTS must contain at least one project."
        )

    validated_projects: list[str] = []

    for project in projects:
        validated_projects.append(
            _validate_url("GITLAB_PROJECTS", project)
        )

    return validated_projects


def load_config() -> Config:
    project_root = Path(__file__).resolve().parent.parent

    env_file = project_root / ".env"

    if env_file.exists():
        load_dotenv(env_file)

    gitlab_url = _validate_url(
        "GITLAB_URL",
        _get_required_env("GITLAB_URL"),
    )

    gitlab_token = _get_required_env("GITLAB_TOKEN")

    projects = _get_projects()

    ollama_url = _validate_url(
        "OLLAMA_URL",
        _get_required_env("OLLAMA_URL"),
    )

    ollama_model = _get_required_env("OLLAMA_MODEL")

    opencode_command = os.environ.get(
        "OPENCODE_COMMAND",
        "opencode",
    ).strip()

    if not opencode_command:
        opencode_command = "opencode"

    max_auto_iterations = _get_int_env(
        "AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION",
        2,
    )

    max_total_iterations = _get_int_env(
        "AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION",
        10,
    )

    max_commits = _get_int_env(
        "AI_MAX_COMMITS_PER_MERGE_REQUEST",
        5,
    )

    if max_auto_iterations < 0:
        raise RuntimeError(
            "AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION "
            "cannot be negative."
        )

    if max_total_iterations < 1:
        raise RuntimeError(
            "AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION "
            "must be at least 1."
        )

    if max_auto_iterations > max_total_iterations:
        raise RuntimeError(
            "AI_MAX_AUTO_ITERATIONS_PER_DISCUSSION cannot be "
            "greater than AI_MAX_TOTAL_ITERATIONS_PER_DISCUSSION."
        )

    if max_commits < 0:
        raise RuntimeError(
            "AI_MAX_COMMITS_PER_MERGE_REQUEST cannot be negative."
        )

    state_file_raw = os.environ.get(
        "STATE_FILE",
        "data/state.json",
    ).strip()

    if not state_file_raw:
        state_file_raw = "data/state.json"

    state_file = Path(state_file_raw)

    if not state_file.is_absolute():
        state_file = project_root / state_file

    return Config(
        gitlab=GitLabConfig(
            url=gitlab_url,
            token=gitlab_token,
            projects=projects,
        ),
        ollama=OllamaConfig(
            url=ollama_url,
            model=ollama_model,
        ),
        opencode=OpenCodeConfig(
            command=opencode_command,
        ),
        limits=LimitsConfig(
            max_auto_iterations_per_discussion=max_auto_iterations,
            max_total_iterations_per_discussion=max_total_iterations,
            max_commits_per_merge_request=max_commits,
        ),
        state=StateConfig(
            file=state_file,
        ),
        runtime=RuntimeConfig(
            dry_run=_get_bool_env("DRY_RUN"),
        ),
    )
