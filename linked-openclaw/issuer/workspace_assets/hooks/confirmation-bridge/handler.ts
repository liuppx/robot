import fs from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

import { appendAuditLog } from "../../tools/lib/audit_log.mjs";
import {
  loadPolicy,
  normalizeText,
  resolveGitHubRepoFromPolicy,
  workspaceRootFromHook
} from "../../tools/lib/common.mjs";
import { sendFeishuTextForEvent } from "../../tools/lib/feishu_reply.mjs";
import { buildCommandsSection, buildRepoAliasesSection } from "../../tools/lib/issuer_capabilities.mjs";

function toolsDir() {
  return path.join(workspaceRootFromHook(import.meta.url), "tools");
}

function runTool(toolName, args, scope) {
  return spawnSync(process.execPath, [path.join(toolsDir(), toolName), ...args], {
    env: {
      ...process.env,
      PENDING_SCOPE_CHANNEL_ID: scope.channelId || "feishu",
      PENDING_SCOPE_ACCOUNT_ID: scope.accountId || "default",
      PENDING_SCOPE_CONVERSATION_ID: scope.conversationId || "",
      PENDING_SCOPE_CHAT_TYPE: scope.chatType || "group"
    },
    encoding: "utf8"
  });
}

function parseJsonOutput(result) {
  const raw = (result.stdout || result.stderr || "").trim();
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return { raw };
  }
}

function isFeishuMessageEvent(event) {
  return event?.type === "message" && event?.action === "received" && event?.context?.channelId === "feishu";
}

function pushReply(event, message) {
  if (!event || !message) {
    return;
  }
  if (!Array.isArray(event.messages)) {
    event.messages = [];
  }
  event.messages.push(String(message));
}

async function reply(workspaceRoot, event, message) {
  pushReply(event, message);
  if (process.env.ISSUER_DISABLE_DIRECT_FEISHU_REPLY === "1") {
    return;
  }
  try {
    await sendFeishuTextForEvent(workspaceRoot, event, message);
  } catch (error) {
    appendAuditLog(workspaceRoot, {
      source: "confirmation_bridge",
      event: "hook.reply.failed",
      error: error instanceof Error ? error.message : String(error),
      status: error?.status || null,
      payload: error?.payload || null
    });
  }
}

function resolveSender(event) {
  return {
    id: event?.context?.metadata?.senderId || event?.context?.from || null,
    label:
      event?.context?.metadata?.senderName ||
      event?.context?.metadata?.senderUsername ||
      event?.context?.metadata?.senderId ||
      event?.context?.from ||
      null
  };
}

function confirmCommands(policy) {
  return Array.isArray(policy?.confirmCommands) && policy.confirmCommands.length > 0
    ? policy.confirmCommands
    : ["/confirm", "/submit", "确认", "确认一下", "提交", "提交 issue", "提交 github issue"];
}

function cancelCommands(policy) {
  return Array.isArray(policy?.cancelCommands) && policy.cancelCommands.length > 0
    ? policy.cancelCommands
    : ["/cancel", "取消", "算了", "不用了", "先别创建"];
}

function helpCommands(policy) {
  return Array.isArray(policy?.helpCommands) && policy.helpCommands.length > 0
    ? policy.helpCommands
    : ["/help", "help", "帮助", "使用帮助"];
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function matchCommand(text, candidates) {
  const normalized = normalizeText(text);
  if (!normalized) {
    return null;
  }

  for (const candidate of [...candidates].sort((left, right) => String(right).length - String(left).length)) {
    const pattern = new RegExp(`^${escapeRegex(String(candidate))}(?:\\s+([\\s\\S]*))?$`, "i");
    const match = normalized.match(pattern);
    if (!match) {
      continue;
    }
    return {
      keyword: String(candidate),
      argument: String(match[1] || "").trim(),
      explicitSlash: String(candidate).startsWith("/")
    };
  }

  return null;
}

function isAllowedConfirmer(policy, pending, sender) {
  if (!sender?.id) {
    return {
      allowed: false,
      reason: "无法识别确认人，请让发起人本人或管理员发送命令。"
    };
  }

  if (pending?.requester?.id && sender.id === pending.requester.id) {
    return { allowed: true };
  }

  const admins = Array.isArray(policy?.admins) ? policy.admins : [];
  if (admins.includes(sender.id)) {
    return { allowed: true };
  }

  return {
    allowed: false,
    reason: "只有发起人本人或管理员可以确认或取消当前操作。"
  };
}

function successMessage(kind, issue) {
  if (!issue?.htmlUrl) {
    if (kind === "github_issue_close") {
      return "已关闭 GitHub Issue。";
    }
    if (kind === "github_issue_comment") {
      return "已发布 GitHub Issue 评论。";
    }
    if (kind === "github_issue_update") {
      return "已更新 GitHub Issue。";
    }
    return "已创建 GitHub Issue。";
  }

  if (kind === "github_issue_close" || issue?.state === "closed") {
    return `已关闭 GitHub Issue #${issue.number} ${issue.title}
${issue.htmlUrl}`;
  }

  if (kind === "github_issue_update") {
    return `已更新 GitHub Issue #${issue.number} ${issue.title}
${issue.htmlUrl}`;
  }

  if (kind === "github_issue_comment") {
    return `已发布 GitHub Issue 评论
${issue.htmlUrl || issue.issueUrl || ""}`.trim();
  }

  return `已创建 GitHub Issue #${issue.number} ${issue.title}
${issue.htmlUrl}`;
}

function resolveRepoArgument(workspaceRoot, policy, argument) {
  const raw = String(argument || "").trim();
  if (!raw) {
    return "";
  }

  const resolved = resolveGitHubRepoFromPolicy(policy, raw, { workspaceRoot });
  if (resolved?.owner && resolved?.repo) {
    return `${resolved.owner}/${resolved.repo}`;
  }

  return raw;
}

function repoDisplay(entry) {
  return entry?.target?.repoKey || "unknown-repo";
}

function shortDraftId(draftId) {
  const raw = String(draftId || "").trim();
  if (!raw) {
    return "unknown";
  }
  return raw.length > 8 ? raw.slice(0, 8) : raw;
}

function draftRef(entry) {
  return `draft:${shortDraftId(entry?.draftId)}`;
}

function pendingLabel(entry) {
  const repo = repoDisplay(entry);
  const number = entry?.target?.issueNumber ? ` #${entry.target.issueNumber}` : "";
  const requester = entry?.requester?.label ? ` · 发起人:${entry.requester.label}` : "";
  return `- ${repo}${number} · ${entry?.headline || entry?.kind || "pending action"} · ${draftRef(entry)}${requester}`;
}

function buildAmbiguousMessage(actionLabel, slashCommand, matches, draftQuery = "") {
  const lines = [
    draftQuery
      ? `当前 draft 查询仍匹配到多个待${actionLabel}草案，请提供更精确的 draftId：`
      : `你在当前群里有多个待${actionLabel}草案，请显式指定仓库：`,
    ...matches.map(pendingLabel),
    "",
    draftQuery ? `例如：${slashCommand} ${draftQuery}` : `例如：${slashCommand} robot`
  ];
  return lines.join("\n");
}

function buildNotFoundMessage(actionLabel, targetArgument, available) {
  if (available.length === 0) {
    return `你在当前群里没有待${actionLabel}草案。`;
  }

  const lines = [];
  if (targetArgument) {
    lines.push(`未找到你在当前群里的 ${targetArgument} 待${actionLabel}草案。`);
  } else {
    lines.push(`你在当前群里没有待${actionLabel}草案。`);
  }
  lines.push("当前可操作草案：");
  lines.push(...available.map(pendingLabel));
  return lines.join("\n");
}

function statusConflictMessage(action, payload) {
  const status = String(payload?.current?.status || "").trim().toLowerCase();
  if (status === "executing") {
    return action === "cancel" ? "草案正在执行，不能取消。请等待执行结果。" : "草案正在执行，请不要重复确认。";
  }
  if (status === "done") {
    return action === "cancel" ? "草案已执行完成，不能再取消。" : "草案已经执行完成，不需要重复确认。";
  }
  if (status === "cancelled") {
    return action === "cancel" ? "草案已经取消，无需重复操作。" : "草案已经取消，不能再确认。";
  }
  return action === "cancel" ? "取消失败，请刷新后重试。" : "确认失败，请刷新后重试。";
}

function helpTemplatePath(workspaceRoot) {
  return path.join(workspaceRoot, "hooks", "confirmation-bridge", "help.template.md");
}

function fallbackHelpTemplate() {
  return [
    "Issuer 使用说明",
    "{{COMMANDS_SECTION}}",
    "",
    "{{CAPABILITIES_SECTION}}",
    "",
    "{{LIMITATIONS_SECTION}}",
    "",
    "{{REPO_ALIASES_SECTION}}"
  ].join("\n");
}

function buildHelpMessage(workspaceRoot, policy) {
  const templatePath = helpTemplatePath(workspaceRoot);
  const template = fs.existsSync(templatePath) ? fs.readFileSync(templatePath, "utf8") : fallbackHelpTemplate();
  return template
    .replace("{{COMMANDS_SECTION}}", buildCommandsSection())
    .replace("{{REPO_ALIASES_SECTION}}", buildRepoAliasesSection(policy))
    .trim();
}

function resolveDraftArgument(argument) {
  const raw = String(argument || "").trim();
  const match = raw.match(/^draft\s*:\s*(.+)$/i);
  if (!match) {
    return "";
  }
  return String(match[1] || "").trim();
}

function resolveCommandTarget(policy, argument) {
  const draftQuery = resolveDraftArgument(argument);
  if (draftQuery) {
    return {
      repoQuery: "",
      draftQuery
    };
  }

  return {
    repoQuery: resolveRepoArgument(workspaceRootFromHook(import.meta.url), policy, argument),
    draftQuery: ""
  };
}

function pendingArgs(sender, repoQuery, draftQuery, action) {
  const args = ["--action", action];
  if (sender?.id) {
    args.push("--requesterId", String(sender.id));
  }
  if (sender?.label) {
    args.push("--requesterLabel", String(sender.label));
  }
  if (repoQuery) {
    args.push("--repoQuery", repoQuery);
  }
  if (draftQuery) {
    args.push("--draftQuery", draftQuery);
  }
  return args;
}

function pendingArgsForScope(repoQuery, draftQuery, action) {
  const args = ["--action", action];
  if (repoQuery) {
    args.push("--repoQuery", repoQuery);
  }
  if (draftQuery) {
    args.push("--draftQuery", draftQuery);
  }
  return args;
}

function isAdminSender(policy, sender) {
  const admins = Array.isArray(policy?.admins) ? policy.admins : [];
  return !!sender?.id && admins.includes(sender.id);
}

function resolvePendingForSender(policy, sender, repoQuery, draftQuery, scope) {
  const ownPending = parseJsonOutput(runTool("pending_action.mjs", pendingArgs(sender, repoQuery, draftQuery, "get"), scope));
  if (ownPending?.ok || ownPending?.error === "ambiguous") {
    return ownPending;
  }

  if (!isAdminSender(policy, sender)) {
    return ownPending;
  }

  return parseJsonOutput(runTool("pending_action.mjs", pendingArgsForScope(repoQuery, draftQuery, "get"), scope));
}

function auditHook(workspaceRoot, event, details) {
  appendAuditLog(workspaceRoot, {
    source: "confirmation_bridge",
    event,
    ...details
  });
}

const handler = async (event) => {
  if (!isFeishuMessageEvent(event)) {
    return;
  }

  const workspaceRoot = workspaceRootFromHook(import.meta.url);
  const policy = loadPolicy(workspaceRoot);
  const text = normalizeText(event?.context?.content || "");
  if (!text) {
    return;
  }

  const sender = resolveSender(event);
  const scope = {
    channelId: "feishu",
    accountId: event?.context?.accountId || "default",
    conversationId: event?.context?.conversationId || event?.context?.metadata?.to || event?.context?.to || "",
    chatType: event?.context?.conversationId ? "group" : "direct"
  };

  const help = matchCommand(text, helpCommands(policy));
  if (help) {
    auditHook(workspaceRoot, "hook.help", {
      sender,
      text,
      conversationId:
        event?.context?.conversationId || event?.context?.metadata?.to || event?.context?.to || null
    });
    await reply(workspaceRoot, event, buildHelpMessage(workspaceRoot, policy));
    return;
  }

  const confirm = matchCommand(text, confirmCommands(policy));
  const cancel = matchCommand(text, cancelCommands(policy));
  if (!confirm && !cancel) {
    return;
  }

  if (!scope.conversationId) {
    auditHook(workspaceRoot, "hook.command.scope_missing", {
      sender,
      text,
      action: confirm ? "confirm" : "cancel"
    });
    await reply(workspaceRoot, event, "无法定位当前会话，不能处理确认命令。");
    return;
  }
  const command = confirm || cancel;
  const { repoQuery, draftQuery } = resolveCommandTarget(policy, command?.argument || "");
  const actionLabel = confirm ? "确认" : "取消";
  const slashCommand = confirm ? "/confirm" : "/cancel";
  auditHook(workspaceRoot, confirm ? "hook.confirm.received" : "hook.cancel.received", {
    scope,
    sender,
    repoQuery,
    draftQuery,
    argument: command?.argument || "",
    explicitSlash: !!command?.explicitSlash
  });
  const pending = resolvePendingForSender(policy, sender, repoQuery, draftQuery, scope);

  if (!pending?.ok || !pending?.pending) {
    if (pending?.error === "ambiguous") {
      auditHook(workspaceRoot, confirm ? "hook.confirm.ambiguous" : "hook.cancel.ambiguous", {
        scope,
        sender,
        repoQuery,
        draftQuery,
        matches: Array.isArray(pending.matches) ? pending.matches : []
      });
      await reply(
        workspaceRoot,
        event,
        buildAmbiguousMessage(
          actionLabel,
          slashCommand,
          Array.isArray(pending.matches) ? pending.matches : [],
          draftQuery ? `draft:${draftQuery}` : ""
        )
      );
      return;
    }

    if ((pending?.error === "not_found" || pending?.error === "ambiguous") && command?.explicitSlash) {
      auditHook(workspaceRoot, confirm ? "hook.confirm.not_found" : "hook.cancel.not_found", {
        scope,
        sender,
        repoQuery,
        draftQuery,
        available: Array.isArray(pending?.available) ? pending.available : []
      });
      await reply(
        workspaceRoot,
        event,
        buildNotFoundMessage(actionLabel, command?.argument || "", Array.isArray(pending?.available) ? pending.available : [])
      );
    }
    return;
  }

  const allowed = isAllowedConfirmer(policy, pending.pending, sender);
  if (!allowed.allowed) {
    auditHook(workspaceRoot, confirm ? "hook.confirm.permission_denied" : "hook.cancel.permission_denied", {
      scope,
      sender,
      repoQuery,
      draftQuery,
      draft: pending.pending,
      reason: allowed.reason || null
    });
    await reply(workspaceRoot, event, allowed.reason || "只有发起人本人或管理员可以确认或取消当前操作。");
    return;
  }

  if (cancel) {
    const cleared = parseJsonOutput(
      runTool(
        "pending_action.mjs",
        pendingArgs(pending.pending.requester || sender, repoQuery, draftQuery, "clear"),
        scope
      )
    );
    if (!cleared?.ok) {
      if (cleared?.error === "not_pending" && cleared?.current) {
        await reply(workspaceRoot, event, statusConflictMessage("cancel", cleared));
        return;
      }
      auditHook(workspaceRoot, "hook.cancel.clear_failed", {
        scope,
        sender,
        repoQuery,
        draftQuery,
        draft: pending.pending,
        response: cleared || null
      });
      await reply(workspaceRoot, event, "取消失败，请重试或检查待执行草案状态。");
      return;
    }
    auditHook(workspaceRoot, "hook.cancel.cleared", {
      scope,
      sender,
      repoQuery,
      draftQuery,
      draft: pending.pending
    });
    await reply(workspaceRoot, event, `已取消 ${repoDisplay(pending.pending)} 的待执行操作。(${draftRef(pending.pending)})`);
    return;
  }

  const executed = parseJsonOutput(
    runTool(
      "pending_action.mjs",
      pendingArgs(pending.pending.requester || sender, repoQuery, draftQuery, "execute"),
      scope
    )
  );
  if (!executed?.ok) {
    if (executed?.error === "not_pending" && executed?.current) {
      await reply(workspaceRoot, event, statusConflictMessage("confirm", executed));
      return;
    }
    auditHook(workspaceRoot, "hook.confirm.execute_failed", {
      scope,
      sender,
      repoQuery,
      draftQuery,
      draft: pending.pending,
      response: executed || null
    });
    const failure =
      executed?.executed?.response?.message ||
      executed?.executed?.error ||
      executed?.error ||
      "执行失败，请检查 GitHub 配置或最近日志，必要时重新发起或先发送 /cancel。";
    await reply(workspaceRoot, event, `执行失败：${failure}`);
    return;
  }

  const issue = executed?.executed?.result;
  const kind = pending.pending.kind;
  auditHook(workspaceRoot, "hook.confirm.executed", {
    scope,
    sender,
    repoQuery,
    draftQuery,
    draft: pending.pending,
    result: executed?.executed || null
  });
  await reply(workspaceRoot, event, `${successMessage(kind, issue)}\n草案: ${draftRef(pending.pending)}`);
};

export default handler;
