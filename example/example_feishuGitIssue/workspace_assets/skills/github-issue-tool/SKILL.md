---
name: github-issue-tool
description: |
  Use for real GitHub Issue previews, creation, closing, and explicit issue comments in Feishu/OpenClaw. Always preview first for create/close, and use direct execution for explicit comment commands like /run when the target issue is already specified.
---

# GitHub Issue Tool

Activate this skill when the user wants to:

- 创建 GitHub Issue
- 关闭 GitHub Issue
- 给已有 Issue 追加明确评论，例如 `/run`
- 查询 Issue 状态
- 处理和 GitHub Issue 有关的请求

## Read first

- `config/policy.json`
- `tools/github_issue_create.mjs`
- `tools/github_issue_close.mjs`
- `tools/pending_action.mjs`

## Required create flow

1. Resolve `owner/repo` from `repoAliases` when possible.
2. Draft title, body, labels, and assignees.
3. Preview with `node tools/github_issue_create.mjs ...`.
4. Save a pending action with `--kind github_issue_create`.
5. Reply with repo, title, labels, body, `/confirm`, `/cancel`.

## Required close flow

1. Resolve `owner/repo` and `issueNumber`.
2. Default close reason to `completed` unless `not_planned` is clearly intended.
3. Preview with `node tools/github_issue_close.mjs ...`.
4. Save a pending action with `--kind github_issue_close`.
5. Reply with repo, issue number, close reason, `/confirm`, `/cancel`.

## Rules

- Only preview in the normal assistant path.
- Do not call `--execute` directly before explicit confirmation.
- New preview should replace old pending data in the same Feishu conversation scope.


## Explicit comment flow

If the user explicitly asks to post a specific comment on a specific issue, execute it directly with the local tool instead of `gh` CLI.

Example:

```bash
node tools/github_issue_comment.mjs   --issueUrl https://github.com/owner/repo/issues/18   --body '/run'   --execute
```

Rules:

- Prefer `--issueUrl` when the message already includes the full GitHub issue link.
- Do not ask for `gh auth login`.
- Do not route issue comments through the generic `github` skill in this workspace.
