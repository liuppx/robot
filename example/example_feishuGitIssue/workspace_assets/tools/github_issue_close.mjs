#!/usr/bin/env node

import { parseArgs, printJson, required } from "./lib/common.mjs";
import { resolveGitHubToken } from "./lib/github_app.mjs";

const ALLOWED_REASONS = new Set(["completed", "not_planned"]);

function parseIssueNumber(args) {
  const raw = required("issueNumber", args.issueNumber || args.number);
  const issueNumber = Number(raw);
  if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
    throw new Error("issueNumber must be a positive integer.");
  }
  return issueNumber;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const execute = args.execute === "true";
  const owner = args.owner || process.env.GITHUB_DEFAULT_OWNER || process.env.GITHUB_OWNER;
  const repo = args.repo || process.env.GITHUB_DEFAULT_REPO || process.env.GITHUB_REPO;
  const issueNumber = parseIssueNumber(args);
  const reason = args.reason || args.stateReason || "completed";

  if (!ALLOWED_REASONS.has(reason)) {
    throw new Error(`Unsupported close reason: ${reason}. Use one of: ${Array.from(ALLOWED_REASONS).join(", ")}.`);
  }

  const payload = {
    issueNumber,
    state: "closed",
    stateReason: reason
  };

  if (!owner || !repo) {
    printJson({
      ok: false,
      mode: execute ? "execute" : "preview",
      error: "Missing GitHub repository. Provide --owner/--repo or set GITHUB_DEFAULT_OWNER/GITHUB_DEFAULT_REPO.",
      payload
    });
    process.exit(execute ? 2 : 0);
  }

  if (!execute) {
    printJson({
      ok: true,
      mode: "preview",
      owner,
      repo,
      payload
    });
    return;
  }

  const auth = await resolveGitHubToken({ owner, repo });
  const response = await fetch(`https://api.github.com/repos/${owner}/${repo}/issues/${issueNumber}`, {
    method: "PATCH",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${auth.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      state: "closed",
      state_reason: reason
    })
  });

  const raw = await response.text();
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = { raw };
  }

  if (!response.ok) {
    printJson({
      ok: false,
      mode: "execute",
      owner,
      repo,
      issueNumber,
      authMode: auth.mode,
      ...(auth.installationId ? { installationId: auth.installationId } : {}),
      status: response.status,
      response: parsed
    });
    process.exit(1);
  }

  printJson({
    ok: true,
    mode: "execute",
    owner,
    repo,
    issueNumber,
    authMode: auth.mode,
    ...(auth.installationId ? { installationId: auth.installationId } : {}),
    ...(auth.expiresAt ? { tokenExpiresAt: auth.expiresAt } : {}),
    result: {
      number: parsed.number,
      title: parsed.title,
      state: parsed.state,
      stateReason: parsed.state_reason || reason,
      htmlUrl: parsed.html_url,
      closedAt: parsed.closed_at
    }
  });
}

main().catch((error) => {
  printJson({
    ok: false,
    mode: "execute",
    error: error instanceof Error ? error.message : String(error),
    ...(error?.status ? { status: error.status } : {}),
    ...(error?.response ? { response: error.response } : {})
  });
  process.exit(1);
});
