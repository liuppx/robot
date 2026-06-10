---
name: github-issue-tool
description: |
  Use for real GitHub Issue previews, creation, updating, closing, and explicit issue comments in Feishu/OpenClaw. Always preview first for create/update/close, and use direct execution for explicit comment commands when the target issue is already specified.
---

# GitHub Issue Tool

Activate this skill when the user wants to:

- 创建 GitHub Issue
- 修改 GitHub Issue
- 关闭 GitHub Issue
- 给已有 Issue 追加明确评论，例如 `/run`
- 查询 Issue 状态
- 处理和 GitHub Issue 有关的请求

## Read first

- `config/policy.json`
- `tools/github_issue_create.mjs`
- `tools/github_issue_update.mjs`
- `tools/github_issue_close.mjs`
- `tools/pending_action.mjs`

## Required create flow

1. Resolve `owner/repo` from `repoAliases` when possible.
   - Also accept bare repo names under the configured default owner, for example `router` -> `yeying-community/router`.
   - If the user already named a repo and it still cannot be normalized, ask for clarification instead of falling back to `robot`.
   - Treat colloquial task requests that already mention a repo, for example `将chat登录页面的广告链接去掉`, as create requests when they describe a concrete change or bug.
2. Draft title, body, labels, and assignees.
   - Always include a `跟进人：` line in the issue body.
   - If the user already指定了谁来跟进，直接写成 `跟进人：姓名`；否则先留空。
   - If `config/policy.json` contains `githubUserAliases`, use that mapping to turn the follow owner name into GitHub `assignees`.
3. Preview with `node tools/github_issue_create.mjs ...`.
4. Save a pending action with:

```bash
node tools/pending_action.mjs \
  --action create \
  --kind github_issue_create \
  --headline "创建 issue: 标题" \
  --paramsJson '{"owner":"yeying-community","repo":"robot","title":"标题","body":"正文"}'
```

5. Reply with repo, title, labels, body, `/confirm`, `/cancel`.
6. If the same requester already has multiple pending drafts in the current group, tell them to confirm with `/confirm <repo>`.

## Required update flow

1. Resolve `owner/repo` and `issueNumber`.
   - Bare repo names should use the configured default owner.
   - Never replace an unrecognized repo with `robot`.
2. Only modify the fields the user clearly asked for: `title`, `body`, `labels`, `assignees`.
   - If the user is only specifying who来处理这个 issue, update the `跟进人：` line in the body.
   - Prefer passing `--followOwner 姓名`. If the user明确要清空跟进人，use `--clearFollowOwner true`.
   - If that name exists in `config/policy.json.githubUserAliases`, also sync it into GitHub `assignees`.
3. Preview with `node tools/github_issue_update.mjs ...`.
4. Save a pending action with `node tools/pending_action.mjs --action create ...`.
5. Reply with repo, issue number, changed fields, `/confirm`, `/cancel`.
6. If the same requester already has multiple pending drafts in the current group, tell them to confirm with `/confirm <repo>`.

## Required close flow

1. Resolve `owner/repo` and `issueNumber`.
   - Bare repo names should use the configured default owner.
   - Never replace an unrecognized repo with `robot`.
2. Default close reason to `completed` unless `not_planned` is clearly intended.
3. Preview with `node tools/github_issue_close.mjs ...`.
4. Save a pending action with `node tools/pending_action.mjs --action create ...`.
5. Reply with repo, issue number, close reason, `/confirm`, `/cancel`.

## Rules

- If the inbound message is `/confirm`, `/submit`, `/cancel`, or `/help` with or without a repo / `draft:<id>` argument, stop here and return `NO_REPLY`.
- Those command turns are owned by `hooks/confirmation-bridge`; do not run preview, do not save pending data again, and do not call any `--execute` mutation tool from the assistant path.
- Never use `tools/pending_action.mjs save ...` or positional `tools/pending_action.mjs create ...`; always pass `--action create`.
- Only preview in the normal assistant path.
- Do not call `--execute` directly before explicit confirmation.
- Pending isolation is `same Feishu conversation + same requester + same repository`.
- New preview should replace the old pending draft only when those three dimensions are the same.
- Same requester may keep multiple pending drafts in one group if the repositories differ.
- If the requester has multiple repo drafts, require `/confirm <repo>` or `/cancel <repo>`.
- If repo is clear, issue number is absent, and the message is a concrete task/bug/change request, prefer create preview over silence even when the user did not literally say `创建 issue`.
- Attachment behavior: if the user message includes Feishu attachments, preview should keep them as attachment notes in the draft/body. After execute or `/confirm`, they should only be described as uploaded if the tool path truly uploaded them and returned usable links.


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
