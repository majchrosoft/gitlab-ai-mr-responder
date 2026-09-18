from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Repository:
    path: Path
    sha: str


class RepositoryManager:
    WWW_DATA_USER = "www-data"

    def __init__(
        self,
        repositories_root: Path,
        gitlab_ssh_port: int,
    ) -> None:
        self.repositories_root = repositories_root
        self.gitlab_ssh_port = gitlab_ssh_port

    def prepare_merge_request(
        self,
        project_name: str,
        project_url: str,
        merge_request_iid: int,
        sha: str,
    ) -> Repository:
        repository_path = (
            self.repositories_root
            / project_name
            / str(merge_request_iid)
        )

        repository_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not (repository_path / ".git").exists():
            self._clone(
                project_url=project_url,
                destination=repository_path,
            )

        self._fetch(repository_path)
        self._clean(repository_path)

        self._checkout(
            repository_path=repository_path,
            sha=sha,
        )

        self._clean(repository_path)

        self._ensure_www_data_access(
            repository_path
        )

        current_sha = self._current_sha(
            repository_path
        )

        if current_sha != sha:
            raise RuntimeError(
                "Repository checkout verification failed. "
                f"Expected {sha}, got {current_sha}"
            )

        return Repository(
            path=repository_path,
            sha=current_sha,
        )

    def _clone(
        self,
        project_url: str,
        destination: Path,
    ) -> None:
        ssh_url = self._to_ssh_url(project_url)

        print(
            f"      Cloning repository into "
            f"{destination}"
        )

        print(
            f"      Git URL: {ssh_url}"
        )

        self._run(
            [
                "git",
                "clone",
                ssh_url,
                str(destination),
            ],
            cwd=destination.parent,
        )

    def _fetch(
        self,
        repository_path: Path,
    ) -> None:
        print(
            "      Fetching repository..."
        )

        self._run(
            [
                "git",
                "fetch",
                "--all",
                "--prune",
            ],
            cwd=repository_path,
        )

    def _clean(
        self,
        repository_path: Path,
    ) -> None:
        self._run(
            [
                "git",
                "reset",
                "--hard",
            ],
            cwd=repository_path,
        )

        self._run(
            [
                "git",
                "clean",
                "-fd",
            ],
            cwd=repository_path,
        )

    def _checkout(
        self,
        repository_path: Path,
        sha: str,
    ) -> None:
        print(
            f"      Checking out SHA {sha}"
        )

        self._run(
            [
                "git",
                "checkout",
                "--detach",
                sha,
            ],
            cwd=repository_path,
        )

    def _ensure_www_data_access(
        self,
        repository_path: Path,
    ) -> None:
        print(
            "      Granting www-data access to repository: "
            f"{repository_path}"
        )

        if not self._command_exists("setfacl"):
            raise RuntimeError(
                "Unable to grant www-data access to repository: "
                f"{repository_path}\n"
                "The 'setfacl' command is required but was not found. "
                "Install it with: sudo apt install acl"
            )

        self._set_acl(
            repository_path
        )

    def _set_acl(
        self,
        repository_path: Path,
    ) -> None:
        try:
            self._run(
                [
                    "setfacl",
                    "-R",
                    "-m",
                    f"u:{self.WWW_DATA_USER}:rwx",
                    str(repository_path),
                ],
                cwd=repository_path.parent,
            )

            self._run(
                [
                    "setfacl",
                    "-R",
                    "-d",
                    "-m",
                    f"u:{self.WWW_DATA_USER}:rwx",
                    str(repository_path),
                ],
                cwd=repository_path.parent,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                "Unable to grant www-data access "
                f"to repository: {repository_path}\n"
                f"{exc}"
            ) from exc

    def _walk_repository(
        self,
        repository_path: Path,
    ) -> list[Path]:
        paths = [
            repository_path
        ]

        for path in repository_path.rglob("*"):
            paths.append(path)

        paths.sort(
            key=lambda path: len(path.parts)
        )

        return paths

    @staticmethod
    def _command_exists(
        command: str,
    ) -> bool:
        result = subprocess.run(
            [
                "sh",
                "-c",
                f"command -v {command}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        return result.returncode == 0

    def _current_sha(
        self,
        repository_path: Path,
    ) -> str:
        result = self._run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=repository_path,
        )

        return result.stdout.strip()

    def _to_ssh_url(
        self,
        project_url: str,
    ) -> str:
        parsed = urlparse(
            project_url.rstrip("/")
        )

        if not parsed.scheme or not parsed.netloc:
            raise RuntimeError(
                f"Invalid GitLab project URL: {project_url}"
            )

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise RuntimeError(
                "GitLab project URL must use HTTP/HTTPS: "
                f"{project_url}"
            )

        project_path = parsed.path.strip("/")

        if not project_path:
            raise RuntimeError(
                "GitLab project URL has no project path: "
                f"{project_url}"
            )

        return (
            f"ssh://git@{parsed.netloc}:"
            f"{self.gitlab_ssh_port}/"
            f"{project_path}.git"
        )

    @staticmethod
    def _run(
        command: list[str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(
                "Unable to execute command: "
                f"{' '.join(command)}\n"
                f"{exc}"
            ) from exc

        if result.returncode != 0:
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            details = "\n".join(
                part
                for part in [
                    stdout,
                    stderr,
                ]
                if part
            )

            raise RuntimeError(
                "Command failed:\n"
                f"  {' '.join(command)}\n"
                f"  cwd: {cwd}\n"
                f"{details}"
            )

        return result
