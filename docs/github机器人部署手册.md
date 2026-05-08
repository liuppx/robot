# GitHub 机器人部署手册

这是一份给第一次接手的人用的手册，目标只有一个:

- 在一台已经装好 OpenClaw 的机器上，把 `example/example_feishuGitIssue/` 这套 Issue 机器人手动跑起来。

这套示例当前只负责三类动作:

- 在飞书里生成 Issue 草案并创建 GitHub Issue
- 在飞书里生成关闭草案并关闭 GitHub Issue
- 在明确指定的 Issue 下追加评论，例如 `/run`

这套 MVP 默认不依赖 GitHub webhook。飞书消息进入 OpenClaw，GitHub 鉴权走 GitHub App Installation Token。

## 1. 你真正要改的文件

部署时通常只需要改下面 4 个地方:

1. `/root/.config/openclaw/github-app/config.env`
2. `/root/.config/openclaw/github-app/feishu-issue-bot.private-key.pem`
3. `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/openclaw.json`
4. `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot/config/policy.json`

除此之外，其他脚本和 `workspace_assets/` 基本都不用动。

## 2. 前置条件

开始前先确认:

- 机器上已经能执行 `openclaw --version`
- 机器上已经能执行 `node -v`
- 仓库已经在目标机上，推荐路径就是 `/root/code/bot`
- 你已经有飞书机器人的 `appId` 和 `appSecret`
- 你已经知道目标飞书群的 `chat_id`，格式通常是 `oc_xxx`
- 你已经创建好 GitHub App，并且把它安装到了目标仓库
- 你已经拿到了 GitHub App 的 `.pem` 私钥文件
- 你已经有模型路由可用的 API Key；当前示例默认使用 `router/gpt-5.4`

## 3. GitHub App 最小要求

这套示例不要求 webhook。

创建或修改 GitHub App 时，先按下面这组最小配置来:

- Repository permissions:
  - `Issues`: `Read and write`
  - `Metadata`: `Read-only`
- Webhook:
  - 可以先不启用
- Installation target:
  - 只要能安装到目标仓库即可

装好以后，确认这个 App 已经被安装到你要操作的仓库上。

## 4. 初始化示例目录

先进入示例目录:

```bash
cd /root/code/bot/example/example_feishuGitIssue
```

第一次部署先跑初始化脚本:

```bash
bash scripts/init_gitissue_gpt54.sh
```

这个脚本会做 3 件事:

1. 创建运行时目录 `.openclaw-feishu-gitissue-gpt54/`
2. 把 `workspace_assets/` 同步到 `workspace-larkbot/`
3. 如果还没有运行配置，就从 `openclaw.example.json` 复制一份 `openclaw.json`

跑完以后，你会看到这些关键路径:

- 运行时目录: `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54`
- OpenClaw 配置: `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/openclaw.json`
- 工作区目录: `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot`
- 策略文件: `/root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot/config/policy.json`

## 5. 配 GitHub App 凭据

先创建配置目录:

```bash
mkdir -p /root/.config/openclaw/github-app
```

把模板复制过去:

```bash
cp /root/code/bot/example/example_feishuGitIssue/config/github-app.config.env.example \
  /root/.config/openclaw/github-app/config.env
```

再把你的 GitHub App 私钥放到:

```bash
/root/.config/openclaw/github-app/feishu-issue-bot.private-key.pem
```

并收紧权限:

```bash
chmod 600 /root/.config/openclaw/github-app/feishu-issue-bot.private-key.pem
chmod 600 /root/.config/openclaw/github-app/config.env
```

然后编辑:

```bash
vim /root/.config/openclaw/github-app/config.env
```

最少要填这些字段:

```dotenv
GITHUB_APP_ID=1234567
GITHUB_APP_PRIVATE_KEY_PATH=/root/.config/openclaw/github-app/feishu-issue-bot.private-key.pem
GITHUB_APP_INSTALLATION_ID=
GITHUB_DEFAULT_OWNER=yeying-community
GITHUB_DEFAULT_REPO=robot
```

说明:

- `GITHUB_APP_INSTALLATION_ID` 可以留空；工具会按 `owner/repo` 自动查询
- `GITHUB_DEFAULT_OWNER` 和 `GITHUB_DEFAULT_REPO` 是默认仓库
- 如果你不想写默认仓库，也可以每次调用工具时手动传 `--owner` 和 `--repo`

## 6. 配 OpenClaw 与飞书

编辑运行时 OpenClaw 配置:

```bash
vim /root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/openclaw.json
```

最少检查这些字段:

- `models.providers.router.apiKey`
- `agents.defaults.workspace`
- `channels.feishu.appId`
- `channels.feishu.appSecret`
- `channels.feishu.groupAllowFrom`
- `gateway.port`
- `gateway.auth.token`

推荐检查规则:

- 如果你的仓库不在 `/root/code/bot`，把 `agents.defaults.workspace` 改成实际路径
- `groupAllowFrom` 只放你要让机器人生效的飞书群 `chat_id`
- `gateway.auth.token` 换成你自己的随机长字符串
- `gateway.port` 默认是 `18890`，如果冲突就换一个

## 7. 配策略文件

编辑策略文件:

```bash
vim /root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot/config/policy.json
```

至少要改 2 处:

1. `admins`
2. `repoAliases`

示例:

```json
{
  "admins": [
    "ou_xxx_replace_with_admin_user_id"
  ],
  "repoAliases": [
    {
      "alias": "robot",
      "owner": "yeying-community",
      "repo": "robot"
    }
  ]
}
```

说明:

- `admins` 里填能兜底确认的飞书用户 ID
- `repoAliases` 决定用户在群里说“robot 仓库”时，最终映射到哪个 GitHub 仓库

## 8. 启动、查看状态、停止

启动:

```bash
cd /root/code/bot/example/example_feishuGitIssue
bash start_gitissue_gpt54.sh
```

查看状态:

```bash
bash status_gitissue_gpt54.sh
```

停止:

```bash
bash stop_gitissue_gpt54.sh
```

如果你改了 `workspace_assets/` 里的代码，先重新同步工作区再重启:

```bash
bash scripts/sync_to_feishu_workspace.sh
bash stop_gitissue_gpt54.sh
bash start_gitissue_gpt54.sh
```

## 9. 先做 CLI 级别自检

在飞书联调前，先验证 GitHub App 鉴权没问题。

进入运行时工作区:

```bash
cd /root/code/bot/example/example_feishuGitIssue/.openclaw-feishu-gitissue-gpt54/workspace-larkbot
```

先做创建预览:

```bash
node tools/github_issue_create.mjs \
  --owner yeying-community \
  --repo robot \
  --title "Issue Bot smoke test" \
  --body "created by local smoke test"
```

如果输出里出现下面这些字段，说明本地工具链通了:

- `"ok": true`
- `"mode": "preview"`
- `owner`
- `repo`

再做一次真实创建:

```bash
node tools/github_issue_create.mjs \
  --owner yeying-community \
  --repo robot \
  --title "Issue Bot smoke test" \
  --body "created by local smoke test" \
  --execute
```

创建成功后，记下返回的 `number`，然后试关闭:

```bash
node tools/github_issue_close.mjs \
  --owner yeying-community \
  --repo robot \
  --issueNumber 123 \
  --reason completed \
  --execute
```

如果这里都正常，说明 GitHub App 的配置大概率已经没问题。

## 10. 飞书侧冒烟测试

确认机器人已经启动后，在允许的飞书群里做下面这组最小测试:

1. `@机器人 请帮我在 robot 仓库创建一个测试 issue，标题是 xxx，正文是 yyy`
2. 机器人应该先返回一份 Issue 草案，而不是直接创建
3. 你回复 `/confirm`
4. 机器人返回 GitHub Issue 链接

如果要测试关闭:

1. `@机器人 关闭 robot 仓库的 issue 123，原因 completed`
2. 机器人先返回关闭草案
3. 你回复 `/confirm` 或 `/submit`
4. 机器人返回关闭成功消息

## 11. 这套示例的代码都在哪

和 GitHub Issue 相关的源码都集中在:

- `/root/code/bot/example/example_feishuGitIssue/workspace_assets/tools/`
- `/root/code/bot/example/example_feishuGitIssue/workspace_assets/hooks/confirmation-bridge/`
- `/root/code/bot/example/example_feishuGitIssue/workspace_assets/skills/`

你平时最常用的几个入口:

- `scripts/init_gitissue_gpt54.sh`: 初始化运行目录
- `scripts/sync_to_feishu_workspace.sh`: 把源码同步进 OpenClaw 工作区
- `start_gitissue_gpt54.sh`: 启动
- `status_gitissue_gpt54.sh`: 看状态和最近日志
- `stop_gitissue_gpt54.sh`: 停止

## 12. 常见问题

### 1. `openclaw: command not found`

先执行:

```bash
OPENCLAW_BIN="$(npm config get prefix)/bin/openclaw"
if [[ -x "$OPENCLAW_BIN" ]]; then
  sudo ln -sf "$OPENCLAW_BIN" /usr/local/bin/openclaw
  openclaw --version
fi
```

### 2. GitHub 返回 401 或 403

优先检查:

- GitHub App 是否真的安装到了目标仓库
- `Issues` 权限是不是 `Read and write`
- `GITHUB_APP_PRIVATE_KEY_PATH` 指向的 `.pem` 是否正确
- `GITHUB_DEFAULT_OWNER` / `GITHUB_DEFAULT_REPO` 是否填对
- 如果手填了 `GITHUB_APP_INSTALLATION_ID`，它是不是对应同一个仓库安装

### 3. 飞书里 @ 机器人没有反应

优先检查:

- `openclaw.json` 里的 `channels.feishu.appId` / `appSecret`
- `groupAllowFrom` 是否包含当前群的 `chat_id`
- 当前群消息是不是确实 `@机器人`
- `status_gitissue_gpt54.sh` 的最近日志里有没有报错

### 4. `/confirm` 没有生效

优先检查:

- 当前发 `/confirm` 的人是不是发起人本人
- 或者这个人是否在 `policy.json` 的 `admins` 里
- 当前会话是否真的已有 pending action

### 5. 端口冲突

如果 `18890` 被占用，改下面两处:

1. `start_gitissue_gpt54.sh` / `status_gitissue_gpt54.sh` / `stop_gitissue_gpt54.sh` 里的 `PORT`
2. `.openclaw-feishu-gitissue-gpt54/openclaw.json` 里的 `gateway.port`

## 13. 提交代码前别把这些东西带上去

下面这些文件属于运行态或敏感信息，不应该提交:

- `.openclaw-feishu-gitissue-gpt54/`
- `GitIssue.md`
- `/root/.config/openclaw/github-app/config.env`
- `/root/.config/openclaw/github-app/*.pem`

示例目录里已经配了 `.gitignore` 来尽量规避误提交，但推代码前最好还是自己再看一眼 `git status`。
