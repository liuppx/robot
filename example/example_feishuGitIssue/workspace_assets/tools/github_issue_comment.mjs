#!/usr/bin/env node

import { parseArgs, printJson, required } from "./lib/common.mjs";
import { resolveGitHubToken } from "./lib/github_app.mjs";

function parseIssueUrl(issueUrl) {
  const match = String(issueUrl || "").trim().match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/issues\/(\d+)(?:[/?#].*)?$/i);
  if (!match) {
    return null;
  }
  return {
    owner: match[1],
    repo: match[2],
    issueNumber: Number(match[3])
  };
}

function parseIssueNumber(args, fromUrl) {
  const raw = args.issueNumber || args.number || fromUrl?.issueNumber;
  const issueNumber = Number(required("issueNumber", raw));
  if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
    throw new Error("issueNumber must be a positive integer.");
  }
  return issueNumber;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const execute = args.execute === "true";
  const fromUrl = args.issueUrl ? parseIssueUrl(args.issueUrl) : null;
  if (args.issueUrl && !fromUrl) {
    throw new Error("issueUrl must look like https://github.com/<owner>/<repo>/issues/<number>");
  }

  const owner = args.owner || fromUrl?.owner || process.env.GITHUB_DEFAULT_OWNER || process.env.GITHUB_OWNER;
  const repo = args.repo || fromUrl?.repo || process.env.GITHUB_DEFAULT_REPO || process.env.GITHUB_REPO;
  const issueNumber = parseIssueNumber(args, fromUrl);
  const body = required("body", args.body);

  const payload = {
    issueNumber,
    body
  };

  if (!owner || !repo) {
    printJson({
      ok: false,
      mode: execute ? "execute" : "preview",
      error: "Missing GitHub repository. Provide --owner/--repo, --issueUrl, or set GITHUB_DEFAULT_OWNER/GITHUB_DEFAULT_REPO.",
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
  const response = await fetch(`https://api.github.com/repos/${owner}/${repo}/issues/${issueNumber}/comments`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${auth.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ body })
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
      id: parsed.id,
      htmlUrl: parsed.html_url,
      body: parsed.body,
      issueUrl: `https://github.com/${owner}/${repo}/issues/${issueNumber}`
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
