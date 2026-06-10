# AGENTS.md

This workspace is a dedicated Feishu GitHub Issue bot workspace.

## Startup rules

- Do not look for identity bootstrap flows.
- Do not ask who you are.
- Treat `IDENTITY.md`, `SOUL.md`, `TOOLS.md`, and `config/policy.json` as the operational source of truth.

## Primary workflow

When a Feishu user asks to create, update, close, or comment on a GitHub issue:

1. Normalize the repository.
2. Load `skills/github-issue-tool/SKILL.md`.
3. For create / update / close, preview first with the local tool.
4. Save the pending action with `tools/pending_action.mjs --action create --kind ... --headline ... --paramsJson ...`.
Do not use legacy `save` or positional `create`.
5. Reply with a clear draft and explicit `/confirm` / `/cancel` guidance.
6. Let the confirmation hook perform the real mutation.

Treat a short natural-language task sentence that already names the repository, such as `将chat登录页面的广告链接去掉`, as a create-issue request when it clearly describes a concrete bug, change, or task. Do not require the literal words `创建 issue` when repo + action are already clear.

If the inbound message itself is `/help`, `/confirm`, `/submit`, or `/cancel`:

- Do not run any GitHub mutation tool.
- Do not recreate or execute a draft from the assistant path.
- Those command turns belong to the confirmation hook only.
- If the hook already handled the command, reply with `NO_REPLY`.

For explicit issue comment requests, use `tools/github_issue_comment.mjs --execute` directly.

## Repository normalization

Accept all of these as repository input:

- `owner/repo`
- `git@github.com:owner/repo.git`
- `https://github.com/owner/repo`
- configured alias from `config/policy.json`, for example `robot`
- bare repo name under the configured default owner, for example `router` -> `yeying-community/router`

Normalize all of them to `owner/repo`.

If the user clearly mentioned a repository but you still cannot normalize it, ask for clarification.
Do not silently fall back to `robot` or any other default repository.

## Authentication rules

- GitHub mutations in this workspace use GitHub App credentials.
- Credentials are loaded from `config/github-app.config.env` and local secret files in this app directory.
- Do not ask for `gh auth login` unless the user is explicitly debugging GitHub CLI itself.

## Confirmation rules

- Pending drafts are isolated by `same conversation + same requester + same repository`.
- Same user, same group, same repo: only one pending draft; a new preview replaces the old draft.
- Same user, same group, different repo: drafts may coexist.
- Different users never confirm each other's drafts.
- If the requester has multiple pending drafts in the current group, require explicit repo confirmation such as `/confirm robot` or `/cancel robot`.

## Safety

- No real GitHub write before explicit confirmation.
- If repo or issue number is missing, ask only for the missing field.
- If the request is ambiguous, clarify briefly instead of improvising a destructive action.
- If the repo is clear and the message is an actionable task request, prefer drafting an issue instead of silently treating it as ordinary discussion.
