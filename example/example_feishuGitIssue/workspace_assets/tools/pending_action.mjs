#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  base64UrlEncode,
  inferConversationContextFromLatestSession,
  parseArgs,
  printJson,
  required,
  workspaceRootFromTool
} from "./lib/common.mjs";

function workspaceRoot() {
  return workspaceRootFromTool(import.meta.url);
}

function stateDir() {
  const dir = path.join(workspaceRoot(), "state", "pending-actions");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function inferScopeAndRequester() {
  const inferred = inferConversationContextFromLatestSession();
  if (!inferred) {
    return null;
  }

  const conversation = inferred.conversation || {};
  const sender = inferred.sender || {};

  return {
    scope: {
      channelId: "feishu",
      accountId: "default",
      conversationId: conversation.chat_id || conversation.conversation_label || null,
      chatType: conversation.is_group_chat ? "group" : "direct",
      sessionKey: inferred.sessionKey || null,
      sessionFile: inferred.sessionFile || null
    },
    requester: {
      id: sender.id || sender.label || conversation.sender_id || conversation.sender || null,
      label: sender.name || sender.label || conversation.sender_id || conversation.sender || null
    }
  };
}

function currentScopeFromArgsOrEnv(args) {
  const explicit = {
    channelId: args.channelId || process.env.PENDING_SCOPE_CHANNEL_ID || "feishu",
    accountId: args.accountId || process.env.PENDING_SCOPE_ACCOUNT_ID || "default",
    conversationId: args.conversationId || process.env.PENDING_SCOPE_CONVERSATION_ID || "",
    chatType: args.chatType || process.env.PENDING_SCOPE_CHAT_TYPE || "group"
  };

  if (explicit.conversationId) {
    return explicit;
  }

  const inferred = inferScopeAndRequester();
  if (inferred?.scope?.conversationId) {
    return inferred.scope;
  }

  throw new Error(
    "Unable to resolve current conversation scope. Provide --conversationId or run inside an OpenClaw Feishu session."
  );
}

function currentRequester(args) {
  if (args.requesterId || args.requesterLabel) {
    return {
      id: args.requesterId || null,
      label: args.requesterLabel || null
    };
  }

  const inferred = inferScopeAndRequester();
  if (inferred?.requester?.id || inferred?.requester?.label) {
    return inferred.requester;
  }

  return null;
}

function scopeFilePath(scope) {
  const key = `${scope.channelId}:${scope.accountId}:${scope.conversationId}`;
  return path.join(stateDir(), `${base64UrlEncode(key)}.json`);
}

function readPending(scope) {
  const filePath = scopeFilePath(scope);
  if (!fs.existsSync(filePath)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writePending(scope, payload) {
  const filePath = scopeFilePath(scope);
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2));
  return filePath;
}

function clearPending(scope) {
  const filePath = scopeFilePath(scope);
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
  }
  return filePath;
}

function buildExecCommand(kind, params) {
  const toolsDir = path.join(workspaceRoot(), "tools");
  const toolByKind = {
    github_issue_create: "github_issue_create.mjs",
    github_issue_close: "github_issue_close.mjs"
  };

  const toolName = toolByKind[kind];
  if (!toolName) {
    throw new Error(`Unsupported pending action kind: ${kind}`);
  }

  const command = [path.join(toolsDir, toolName)];
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    const normalizedValue = Array.isArray(value) ? value.join(",") : String(value);
    command.push(`--${key}`, normalizedValue);
  }
  command.push("--execute");
  return command;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const action = args.action || "get";
  const scope = currentScopeFromArgsOrEnv(args);

  if (action === "create") {
    const kind = required("kind", args.kind);
    const headline = required("headline", args.headline);
    const paramsJson = required("paramsJson", args.paramsJson);
    const previewNote = args.previewNote || "";
    const requester = currentRequester(args);
    const payload = {
      version: 1,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      scope,
      kind,
      headline,
      previewNote,
      params: JSON.parse(paramsJson),
      ...(requester ? { requester } : {})
    };
    const filePath = writePending(scope, payload);
    printJson({
      ok: true,
      action,
      filePath,
      pending: payload
    });
    return;
  }

  if (action === "get") {
    const pending = readPending(scope);
    printJson({
      ok: Boolean(pending),
      action,
      scope,
      pending
    });
    process.exit(pending ? 0 : 1);
  }

  if (action === "clear") {
    const filePath = clearPending(scope);
    printJson({
      ok: true,
      action,
      scope,
      filePath
    });
    return;
  }

  if (action === "execute") {
    const pending = readPending(scope);
    if (!pending) {
      printJson({
        ok: false,
        action,
        scope,
        error: "No pending action for current conversation."
      });
      process.exit(1);
    }

    const command = buildExecCommand(pending.kind, pending.params);
    const result = spawnSync(process.execPath, command, {
      env: process.env,
      encoding: "utf8"
    });
    const raw = (result.stdout || result.stderr || "").trim();
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = { raw };
    }

    if (result.status === 0 && parsed?.ok) {
      clearPending(scope);
      printJson({
        ok: true,
        action,
        scope,
        executed: parsed
      });
      return;
    }

    printJson({
      ok: false,
      action,
      scope,
      executed: parsed
    });
    process.exit(1);
  }

  if (action === "list") {
    const entries = fs
      .readdirSync(stateDir())
      .filter((name) => name.endsWith(".json"))
      .map((name) => JSON.parse(fs.readFileSync(path.join(stateDir(), name), "utf8")));
    printJson({
      ok: true,
      action,
      entries
    });
    return;
  }

  throw new Error(`Unsupported action: ${action}`);
}

main();
