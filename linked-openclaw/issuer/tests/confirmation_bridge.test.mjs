import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import handler from "../workspace_assets/hooks/confirmation-bridge/handler.ts";
import { appRoot, installFakeCreateTool, makeTempDir, readJsonLines, runNodeJson, withEnv, writeJson } from "./helpers.mjs";

const sourceToolsDir = path.join(appRoot, "workspace_assets", "tools");
const sourceHooksDir = path.join(appRoot, "workspace_assets", "hooks");

function workspaceEnv(root) {
  return {
    ISSUER_WORKSPACE_ROOT: root,
    ISSUER_POLICY_PATH: path.join(root, "config", "policy.json"),
    PENDING_DB_PATH: path.join(root, "state", "pending-actions.sqlite3"),
    ISSUER_AUDIT_LOG_PATH: path.join(root, "logs", "issuer-audit.jsonl"),
    ISSUER_DISABLE_DIRECT_FEISHU_REPLY: "1"
  };
}

function makeEvent(senderId, content) {
  return {
    type: "message",
    action: "received",
    context: {
      channelId: "feishu",
      accountId: "default",
      conversationId: "chat-hook",
      content,
      from: senderId,
      metadata: {
        senderId,
        senderName: senderId,
        to: "chat-hook"
      }
    },
    messages: []
  };
}

function createDraft(workspaceRoot, env, requesterId, repo, headline) {
  return runNodeJson(
    path.join(workspaceRoot, "tools", "pending_action.mjs"),
    [
      "--action",
      "create",
      "--conversationId",
      "chat-hook",
      "--requesterId",
      requesterId,
      "--requesterLabel",
      requesterId,
      "--kind",
      "github_issue_create",
      "--headline",
      headline,
      "--paramsJson",
      JSON.stringify({
        owner: "yeying-community",
        repo,
        title: headline,
        body: `${headline} body`
      })
    ],
    { env }
  );
}

test("confirmation bridge requires explicit repo when multiple drafts exist and confirms only the selected repo", async (t) => {
  const workspaceRoot = makeTempDir("issuer-hook-");
  fs.cpSync(sourceToolsDir, path.join(workspaceRoot, "tools"), { recursive: true });
  fs.cpSync(sourceHooksDir, path.join(workspaceRoot, "hooks"), { recursive: true });
  fs.mkdirSync(path.join(workspaceRoot, "config"), { recursive: true });
  writeJson(path.join(workspaceRoot, "config", "policy.json"), {
    repoAliases: [
      { alias: "robot", owner: "yeying-community", repo: "robot" },
      { alias: "openclaw", owner: "yeying-community", repo: "openclaw" }
    ],
    admins: ["admin-user"]
  });
  fs.writeFileSync(
    path.join(workspaceRoot, "hooks", "confirmation-bridge", "help.template.md"),
    ["自定义帮助", "示例：/confirm draft:<id>", "", "{{REPO_ALIASES_SECTION}}"].join("\n")
  );
  installFakeCreateTool(path.join(workspaceRoot, "tools", "github_issue_create.mjs"));

  const env = workspaceEnv(workspaceRoot);
  const restoreEnv = withEnv(env);
  t.after(restoreEnv);

  const robotDraft = createDraft(workspaceRoot, env, "user-a", "robot", "robot draft");
  assert.equal(robotDraft.result.status, 0);
  const openclawDraft = createDraft(workspaceRoot, env, "user-a", "openclaw", "openclaw draft");
  assert.equal(openclawDraft.result.status, 0);

  const helpEvent = makeEvent("user-a", "/help");
  await handler(helpEvent);
  assert.equal(helpEvent.messages.length, 1);
  assert.match(helpEvent.messages[0], /自定义帮助/);
  assert.match(helpEvent.messages[0], /robot -> yeying-community\/robot/);

  const ambiguousEvent = makeEvent("user-a", "/confirm");
  await handler(ambiguousEvent);
  assert.equal(ambiguousEvent.messages.length, 1);
  assert.match(ambiguousEvent.messages[0], /多个待确认草案/);
  assert.match(ambiguousEvent.messages[0], /\/confirm robot/);
  assert.match(ambiguousEvent.messages[0], /draft:/);

  const confirmOpenclawByDraft = makeEvent("user-a", `/confirm draft:${openclawDraft.json.pending.draftId.slice(0, 8)}`);
  await handler(confirmOpenclawByDraft);
  assert.equal(confirmOpenclawByDraft.messages.length, 1);
  assert.match(confirmOpenclawByDraft.messages[0], /https:\/\/github\.com\/yeying-community\/openclaw\/issues\/321/);
  assert.match(confirmOpenclawByDraft.messages[0], /草案: draft:/);

  const openclawGone = runNodeJson(
    path.join(workspaceRoot, "tools", "pending_action.mjs"),
    ["--action", "get", "--conversationId", "chat-hook", "--requesterId", "user-a", "--repoQuery", "openclaw"],
    { env }
  );
  assert.equal(openclawGone.result.status, 1);
  assert.equal(openclawGone.json.error, "not_found");

  const robotStillThere = runNodeJson(
    path.join(workspaceRoot, "tools", "pending_action.mjs"),
    ["--action", "get", "--conversationId", "chat-hook", "--requesterId", "user-a", "--repoQuery", "robot"],
    { env }
  );
  assert.equal(robotStillThere.result.status, 0);
  assert.equal(robotStillThere.json.pending.target.repo, "robot");

  const adminCancel = makeEvent("admin-user", "/cancel robot");
  await handler(adminCancel);
  assert.equal(adminCancel.messages.length, 1);
  assert.match(adminCancel.messages[0], /已取消 yeying-community\/robot 的待执行操作/);
  assert.match(adminCancel.messages[0], /draft:/);

  const auditEvents = readJsonLines(env.ISSUER_AUDIT_LOG_PATH).map((entry) => entry.event);
  assert.ok(auditEvents.includes("hook.help"));
  assert.ok(auditEvents.includes("hook.confirm.ambiguous"));
  assert.ok(auditEvents.includes("hook.confirm.executed"));
  assert.ok(auditEvents.includes("hook.cancel.cleared"));
});

test("help message auto-renders command block", async (t) => {
  const workspaceRoot = makeTempDir("issuer-help-");
  fs.cpSync(sourceToolsDir, path.join(workspaceRoot, "tools"), { recursive: true });
  fs.cpSync(sourceHooksDir, path.join(workspaceRoot, "hooks"), { recursive: true });
  fs.mkdirSync(path.join(workspaceRoot, "config"), { recursive: true });
  writeJson(path.join(workspaceRoot, "config", "policy.json"), {
    repoAliases: [{ alias: "robot", owner: "yeying-community", repo: "robot" }]
  });

  const env = workspaceEnv(workspaceRoot);
  const restoreEnv = withEnv(env);
  t.after(restoreEnv);

  const helpEvent = makeEvent("user-a", "/help");
  await handler(helpEvent);
  assert.equal(helpEvent.messages.length, 1);
  assert.match(helpEvent.messages[0], /这个机器人可以帮你做 4 件事/);
  assert.match(helpEvent.messages[0], /指定跟进人 \/ assignees/);
  assert.match(helpEvent.messages[0], /GitHub 的 `assignee`/);
  assert.match(helpEvent.messages[0], /上传附件的方法/);
  assert.match(helpEvent.messages[0], /默认单个附件不要超过 5MB/);
  assert.match(helpEvent.messages[0], /\/confirm \[repo\|draft:<id>\]/);
  assert.doesNotMatch(helpEvent.messages[0], /仓库别名：/);
});

test("confirmation bridge resolves bare repo names under the default owner", async (t) => {
  const workspaceRoot = makeTempDir("issuer-hook-router-");
  fs.cpSync(sourceToolsDir, path.join(workspaceRoot, "tools"), { recursive: true });
  fs.cpSync(sourceHooksDir, path.join(workspaceRoot, "hooks"), { recursive: true });
  fs.mkdirSync(path.join(workspaceRoot, "config"), { recursive: true });
  writeJson(path.join(workspaceRoot, "config", "policy.json"), {
    repoAliases: [{ alias: "robot", owner: "yeying-community", repo: "robot" }],
    admins: ["admin-user"]
  });
  fs.writeFileSync(
    path.join(workspaceRoot, "config", "github-app.config.env"),
    "GITHUB_DEFAULT_OWNER=yeying-community\nGITHUB_DEFAULT_REPO=robot\n"
  );

  const env = {
    ...workspaceEnv(workspaceRoot),
    GITHUB_ENV_FILE: path.join(workspaceRoot, "config", "github-app.config.env")
  };
  const restoreEnv = withEnv(env);
  t.after(restoreEnv);

  const routerDraft = createDraft(workspaceRoot, env, "user-a", "router", "router draft");
  assert.equal(routerDraft.result.status, 0);

  const cancelRouter = makeEvent("user-a", "/cancel router");
  await handler(cancelRouter);
  assert.equal(cancelRouter.messages.length, 1);
  assert.match(cancelRouter.messages[0], /已取消 yeying-community\/router 的待执行操作/);
});
