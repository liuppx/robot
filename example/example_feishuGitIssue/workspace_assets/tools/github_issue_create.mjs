#!/usr/bin/env node

import { parseArgs, parseCsv, printJson, required } from "./lib/common.mjs";
import { resolveGitHubToken } from "./lib/github_app.mjs";

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const execute = args.execute === "true";
  const owner = args.owner || process.env.GITHUB_DEFAULT_OWNER || process.env.GITHUB_OWNER;
  const repo = args.repo || process.env.GITHUB_DEFAULT_REPO || process.env.GITHUB_REPO;
  const title = required("title", args.title);
  const body = required("body", args.body);
  const labels = parseCsv(args.labels);
  const assignees = parseCsv(args.assignees);

  const payload = {
    title,
    body,
    ...(labels.length > 0 ? { labels } : {}),
    ...(assignees.length > 0 ? { assignees } : {})
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
  const response = await fetch(`https://api.github.com/repos/${owner}/${repo}/issues`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${auth.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
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
    authMode: auth.mode,
    ...(auth.installationId ? { installationId: auth.installationId } : {}),
    ...(auth.expiresAt ? { tokenExpiresAt: auth.expiresAt } : {}),
    result: {
      number: parsed.number,
      title: parsed.title,
      state: parsed.state,
      htmlUrl: parsed.html_url
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
