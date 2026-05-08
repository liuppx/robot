# Feishu GitHub Issue Bot

这个示例目录只负责 GitHub Issue 相关动作，不负责写代码。

当前 MVP 支持:

- 在飞书里生成 Issue 草案并创建 GitHub Issue
- 在飞书里生成关闭草案并关闭 GitHub Issue
- 在明确指定的 Issue 下追加评论，例如 `/run`

默认链路是:

`飞书 @机器人 -> OpenClaw 生成草案 -> /confirm 或 /submit -> 调 GitHub API`

这套示例默认不依赖 GitHub webhook。

## 快速入口

如果你只是想把它跑起来，优先看:

- `../../docs/github机器人部署手册.md`

最常用的脚本只有 5 个:

- `scripts/init_gitissue_gpt54.sh`
- `scripts/sync_to_feishu_workspace.sh`
- `start_gitissue_gpt54.sh`
- `status_gitissue_gpt54.sh`
- `stop_gitissue_gpt54.sh`

## 目录说明

- `workspace_assets/tools/`
  - GitHub App Installation Token 获取
  - Issue create / close / comment 本地工具
  - pending action 保存与执行
- `workspace_assets/hooks/confirmation-bridge/`
  - 监听飞书里的 `/confirm`、`/submit`、`/cancel`
- `workspace_assets/skills/`
  - 给 OpenClaw 的技能说明
- `config/policy.example.json`
  - 飞书管理员和仓库别名示例
- `config/github-app.config.env.example`
  - GitHub App 环境变量示例
- `openclaw.example.json`
  - OpenClaw 运行配置模板

## 第一次初始化

```bash
cd /root/code/bot/example/example_feishuGitIssue
bash scripts/init_gitissue_gpt54.sh
```

这个命令会:

1. 创建 `.openclaw-feishu-gitissue-gpt54/`
2. 同步 `workspace_assets/` 到运行时工作区
3. 生成 `openclaw.json` 模板

## 你通常要改的配置

### 1. GitHub App 凭据

真实文件路径:

- `/root/.config/openclaw/github-app/config.env`
- `/root/.config/openclaw/github-app/feishu-issue-bot.private-key.pem`

模板文件:

- `config/github-app.config.env.example`

### 2. OpenClaw 运行配置

真实文件路径:

- `.openclaw-feishu-gitissue-gpt54/openclaw.json`

模板文件:

- `openclaw.example.json`

### 3. 飞书策略

真实文件路径:

- `.openclaw-feishu-gitissue-gpt54/workspace-larkbot/config/policy.json`

至少要改:

- `admins`
- `repoAliases`

## 启动和查看状态

```bash
bash start_gitissue_gpt54.sh
bash status_gitissue_gpt54.sh
```

停止:

```bash
bash stop_gitissue_gpt54.sh
```

如果你改了 `workspace_assets/` 里的源码，记得先重新同步:

```bash
bash scripts/sync_to_feishu_workspace.sh
```

## 本地工具验证

进入运行时工作区:

```bash
cd /root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot
```

Issue 创建预览:

```bash
node tools/github_issue_create.mjs \
  --owner yeying-community \
  --repo robot \
  --title "Issue Bot smoke test" \
  --body "created by local smoke test"
```

Issue 真实创建:

```bash
node tools/github_issue_create.mjs \
  --owner yeying-community \
  --repo robot \
  --title "Issue Bot smoke test" \
  --body "created by local smoke test" \
  --execute
```

Issue 关闭:

```bash
node tools/github_issue_close.mjs \
  --owner yeying-community \
  --repo robot \
  --issueNumber 123 \
  --reason completed \
  --execute
```

Issue 评论:

```bash
node tools/github_issue_comment.mjs \
  --issueUrl https://github.com/yeying-community/robot/issues/123 \
  --body '/run' \
  --execute
```

## 提交前注意

下面这些内容是本地运行态或敏感信息，不要提交:

- `.openclaw-feishu-gitissue-gpt54/`
- `GitIssue.md`
- `config/policy.json`

这个目录下已经放了 `.gitignore` 来规避误提交。
