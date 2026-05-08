import { spawnSync } from "node:child_process";
import path from "node:path";

import { loadPolicy, normalizeText, workspaceRootFromHook } from "../../tools/lib/common.mjs";

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
    : ["/confirm", "/submit", "确认", "确认一下", "创建 issue", "创建 github issue", "提交", "提交 issue", "提交 github issue"];
}

function cancelCommands(policy) {
  return Array.isArray(policy?.cancelCommands) && policy.cancelCommands.length > 0
    ? policy.cancelCommands
    : ["/cancel", "取消", "算了", "不用了", "先别创建"];
}

function matchesCommand(text, candidates) {
  const normalized = normalizeText(text).toLowerCase();
  return candidates.some((candidate) => normalized.includes(String(candidate).toLowerCase()));
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
    return kind === "github_issue_close" ? "已关闭 GitHub Issue。" : "已创建 GitHub Issue。";
  }

  if (kind === "github_issue_close" || issue?.state === "closed") {
    return `已关闭 GitHub Issue #${issue.number} ${issue.title}
${issue.htmlUrl}`;
  }

  return `已创建 GitHub Issue #${issue.number} ${issue.title}
${issue.htmlUrl}`;
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

  const isConfirm = matchesCommand(text, confirmCommands(policy));
  const isCancel = matchesCommand(text, cancelCommands(policy));
  if (!isConfirm && !isCancel) {
    return;
  }

  const scope = {
    channelId: "feishu",
    accountId: event?.context?.accountId || "default",
    conversationId: event?.context?.conversationId || event?.context?.metadata?.to || event?.context?.to || "",
    chatType: event?.context?.conversationId ? "group" : "direct"
  };

  if (!scope.conversationId) {
    if (Array.isArray(event.messages)) {
      event.messages.push("无法定位当前会话，不能处理确认命令。");
    }
    return;
  }

  const pending = parseJsonOutput(runTool("pending_action.mjs", ["--action", "get"], scope));
  if (!pending?.ok || !pending?.pending) {
    return;
  }

  const sender = resolveSender(event);
  const allowed = isAllowedConfirmer(policy, pending.pending, sender);
  if (!allowed.allowed) {
    if (Array.isArray(event.messages)) {
      event.messages.push(allowed.reason || "只有发起人本人或管理员可以确认或取消当前操作。");
    }
    return;
  }

  if (isCancel) {
    runTool("pending_action.mjs", ["--action", "clear"], scope);
    if (Array.isArray(event.messages)) {
      event.messages.push("已取消待执行操作。");
    }
    return;
  }

  const executed = parseJsonOutput(runTool("pending_action.mjs", ["--action", "execute"], scope));
  if (!executed?.ok) {
    if (Array.isArray(event.messages)) {
      const failure =
        executed?.executed?.response?.message ||
        executed?.executed?.error ||
        executed?.error ||
        "执行失败，请检查 GitHub 配置或最近日志，必要时重新发起或先发送 /cancel。";
      event.messages.push(`执行失败：${failure}`);
    }
    return;
  }

  const issue = executed?.executed?.result;
  const kind = pending.pending.kind;
  if (Array.isArray(event.messages)) {
    event.messages.push(successMessage(kind, issue));
  }
};

export default handler;
