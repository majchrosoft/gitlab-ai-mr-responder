from __future__ import annotations

from dataclasses import dataclass


AI_NAMESPACE = "gitlab-ai-mr-responder"


@dataclass(frozen=True)
class OpenCodeTask:
    prompt: str
    iteration_type: str


def build_fix_prompt(
    project_name: str,
    merge_request_iid: int,
    merge_request_title: str,
    merge_request_sha: str,
    discussion_id: str,
    iteration: int,
    comment: str,
) -> OpenCodeTask:
    prompt = f"""
You are the coding agent of the GitLab AI MR Responder.

Your namespace is:
{AI_NAMESPACE}

You are working inside the repository checked out for this
Merge Request.

Repository:
{project_name}

Merge Request:
!{merge_request_iid} {merge_request_title}

Current MR SHA:
{merge_request_sha}

GitLab discussion:
{discussion_id}

AI iteration:
{iteration}

Review comment:
----------------
{comment}
----------------

Your task:

1. Inspect the repository and understand the review comment.
2. Locate the relevant code.
3. Determine whether the review comment is valid and actionable.
4. If it is valid, implement the smallest appropriate fix.
5. Preserve the existing architecture and coding conventions.
6. Do not make unrelated changes.
7. Run the most relevant tests or static checks available in the repository.
8. If the comment is already fixed by the current code, do not make
   an unnecessary change.
9. If the comment cannot be safely fixed without human clarification,
   do not invent requirements.

Important rules:

- You are operating on a Merge Request working tree.
- Do not change Git remotes.
- Do not push anything to GitLab.
- Do not create or modify Git commits.
- Do not reset or discard changes that were made by this task.
- Do not modify files unrelated to the review comment.
- Do not add explanatory files just to document your work.
- Do not modify the AI orchestrator project itself.
- Work only on the repository currently provided as your working directory.

At the end, report:

1. Whether the review comment was actionable.
2. What files you changed.
3. What you changed.
4. Which tests/checks you ran.
5. Whether those tests/checks passed.
6. Any remaining concern that should be reviewed by a human.

Do not write a GitLab comment yourself.
The orchestrator will handle GitLab communication.
""".strip()

    return OpenCodeTask(
        prompt=prompt,
        iteration_type="automatic",
    )


def build_continue_prompt(
    project_name: str,
    merge_request_iid: int,
    merge_request_title: str,
    merge_request_sha: str,
    discussion_id: str,
    iteration: int,
    original_comment: str,
    continue_comment: str,
) -> OpenCodeTask:
    prompt = f"""
You are the coding agent of the GitLab AI MR Responder.

Your namespace is:
{AI_NAMESPACE}

This is a manually authorized continuation of a previous AI attempt.

Repository:
{project_name}

Merge Request:
!{merge_request_iid} {merge_request_title}

Current MR SHA:
{merge_request_sha}

GitLab discussion:
{discussion_id}

AI iteration:
{iteration}

Original review comment:
----------------
{original_comment}
----------------

Human continuation instruction:
----------------
{continue_comment}
----------------

The human explicitly authorized one additional attempt with
AI-CONTINUE.

Your task:

1. Re-evaluate the original review comment.
2. Read the current repository state carefully.
3. Take the human continuation instruction into account.
4. Inspect what the previous attempt actually changed.
5. Correct or improve the implementation if appropriate.
6. Do not blindly change code just because AI-CONTINUE was issued.
7. If the issue is already correctly resolved, do not make an
   unnecessary change.
8. Run the most relevant tests or static checks available.
9. Keep the change limited to the review issue.

Important rules:

- Do not change Git remotes.
- Do not push anything to GitLab.
- Do not create or modify Git commits.
- Do not reset or discard changes that belong to this task.
- Do not make unrelated refactors.
- Do not modify the AI orchestrator project itself.
- Work only on the repository currently provided as your working directory.

At the end, report:

1. Whether another code change was necessary.
2. What files you changed.
3. What you changed.
4. Which tests/checks you ran.
5. Whether those tests/checks passed.
6. Any remaining concern that should be reviewed by a human.

Do not write a GitLab comment yourself.
The orchestrator will handle GitLab communication.
""".strip()

    return OpenCodeTask(
        prompt=prompt,
        iteration_type="manual",
    )
