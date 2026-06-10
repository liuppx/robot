# TOOLS.md

## GitHub Issue bot local notes

### GitHub App auth

- Config file: `config/github-app.config.env`
- Private key file is expected under this app directory, normally `secrets/github-app.pem`
- Normal issue create / update / close flows should use the local issue tools, not `gh auth login`

### Preferred tools

- Create preview / execute: `tools/github_issue_create.mjs`
- Update preview / execute: `tools/github_issue_update.mjs`
- Close preview / execute: `tools/github_issue_close.mjs`
- Comment execute: `tools/github_issue_comment.mjs`
- Pending confirmation state: `tools/pending_action.mjs`
- Confirmation/help hook: `hooks/confirmation-bridge`

### Repo aliases

Read `config/policy.json` first.
If the repo is not in `repoAliases`, a bare repo name may still be valid when `config/github-app.config.env` sets `GITHUB_DEFAULT_OWNER`.
Do not treat `GITHUB_DEFAULT_REPO` as a fallback when the user already named a different repo.

### Important operational rules

- If a user already provided the repo in the message, do not waste turns checking local git remotes.
- For create / update draft flows, save pending state through `tools/pending_action.mjs --action create --kind ... --headline ... --paramsJson ...`.
- Do not use `tools/pending_action.mjs save ...` or positional `tools/pending_action.mjs create ...`.
- Pending isolation is by `conversation + requester + repo`.
- If a user has multiple pending drafts in one group, tell them to use `/confirm <repo>` or `/cancel <repo>`.
- Feishu attachments are first recorded as attachment notes in preview. On execute or `/confirm`, they should be uploaded to the configured external attachment storage and then rendered back into the GitHub issue/comment body.

### Important comment rule

For a direct request like “给 issue #18 评论 /run”, do not use `gh issue comment`; use `tools/github_issue_comment.mjs` instead.
