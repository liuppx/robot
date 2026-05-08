#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      continue;
    }
    const key = token.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      args[key] = "true";
      continue;
    }
    args[key] = next;
    index += 1;
  }
  return args;
}

export function required(name, value) {
  if (value === undefined || value === null || value === "") {
    throw new Error(`Missing required argument: ${name}`);
  }
  return value;
}

export function parseCsv(value) {
  if (!value) {
    return [];
  }
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function workspaceRootFromTool(importMetaUrl) {
  return path.resolve(path.dirname(fileURLToPath(importMetaUrl)), "..");
}

export function workspaceRootFromHook(importMetaUrl) {
  return path.resolve(path.dirname(fileURLToPath(importMetaUrl)), "../..");
}

export function readJsonIfExists(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) {
    return fallback;
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

export function loadPolicy(workspaceRoot) {
  const primary = path.join(workspaceRoot, "config", "policy.json");
  const fallback = path.join(workspaceRoot, "config", "policy.example.json");
  if (fs.existsSync(primary)) {
    return readJsonIfExists(primary, {});
  }
  return readJsonIfExists(fallback, {}) || {};
}

export function base64UrlEncode(value) {
  const buffer = Buffer.isBuffer(value) ? value : Buffer.from(String(value));
  return buffer
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

export function normalizeText(value) {
  return String(value || "")
    .replace(/<at\b[^>]*>.*?<\/at>/gis, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function currentSessionsIndexPath() {
  if (process.env.OPENCLAW_STATE_DIR) {
    return path.join(process.env.OPENCLAW_STATE_DIR, "agents", "main", "sessions", "sessions.json");
  }
  return path.join(os.homedir(), ".openclaw", "agents", "main", "sessions", "sessions.json");
}

export function pickLatestSessionEntry(indexPayload) {
  const entries = Object.entries(indexPayload || {}).sort(
    (left, right) => (right[1]?.updatedAt ?? 0) - (left[1]?.updatedAt ?? 0)
  );
  return entries.length > 0 ? { sessionKey: entries[0][0], entry: entries[0][1] } : null;
}

function extractJsonBlock(text, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`${escaped}[\\s\\S]*?\\\`\\\`\\\`json\\s*([\\s\\S]*?)\\\`\\\`\\\``, "i");
  const match = String(text || "").match(pattern);
  if (!match) {
    return null;
  }
  try {
    return JSON.parse(match[1]);
  } catch {
    return null;
  }
}

export function inferConversationContextFromLatestSession() {
  const sessionsIndexPath = currentSessionsIndexPath();
  if (!fs.existsSync(sessionsIndexPath)) {
    return null;
  }

  const indexPayload = JSON.parse(fs.readFileSync(sessionsIndexPath, "utf8"));
  const latest = pickLatestSessionEntry(indexPayload);
  if (!latest?.entry?.sessionFile || !fs.existsSync(latest.entry.sessionFile)) {
    return null;
  }

  const lines = fs
    .readFileSync(latest.entry.sessionFile, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  for (let index = lines.length - 1; index >= 0; index -= 1) {
    let parsed;
    try {
      parsed = JSON.parse(lines[index]);
    } catch {
      continue;
    }

    if (parsed?.type !== "message" || parsed?.message?.role !== "user") {
      continue;
    }

    const contentItems = Array.isArray(parsed.message.content) ? parsed.message.content : [];
    const combinedText = contentItems
      .filter((item) => item?.type === "text")
      .map((item) => item.text || "")
      .join("\n");

    const conversation = extractJsonBlock(combinedText, "Conversation info (untrusted metadata):");
    const sender = extractJsonBlock(combinedText, "Sender (untrusted metadata):");

    if (!conversation && !sender) {
      continue;
    }

    return {
      sessionKey: latest.sessionKey,
      sessionFile: latest.entry.sessionFile,
      conversation,
      sender
    };
  }

  return null;
}

export function printJson(payload) {
  console.log(JSON.stringify(payload, null, 2));
}
