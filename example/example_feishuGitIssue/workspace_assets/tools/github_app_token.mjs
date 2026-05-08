#!/usr/bin/env node

import { parseArgs, printJson } from "./lib/common.mjs";
import { createInstallationAccessToken, loadGitHubAppConfigEnv } from "./lib/github_app.mjs";

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const configEnv = loadGitHubAppConfigEnv();
  const appId = args.appId || process.env.GITHUB_APP_ID || configEnv.GITHUB_APP_ID;
  const privateKey = args.privateKey || process.env.GITHUB_APP_PRIVATE_KEY;
  const privateKeyPath = args.privateKeyPath || process.env.GITHUB_APP_PRIVATE_KEY_PATH || configEnv.GITHUB_APP_PRIVATE_KEY_PATH;
  const installationId =
    args.installationId ||
    process.env.GITHUB_APP_INSTALLATION_ID ||
    process.env.GITHUB_INSTALLATION_ID ||
    configEnv.GITHUB_APP_INSTALLATION_ID ||
    configEnv.GITHUB_INSTALLATION_ID;
  const owner =
    args.owner ||
    process.env.GITHUB_DEFAULT_OWNER ||
    process.env.GITHUB_OWNER ||
    configEnv.GITHUB_DEFAULT_OWNER ||
    configEnv.GITHUB_OWNER;
  const repo =
    args.repo ||
    process.env.GITHUB_DEFAULT_REPO ||
    process.env.GITHUB_REPO ||
    configEnv.GITHUB_DEFAULT_REPO ||
    configEnv.GITHUB_REPO;

  if (!appId) {
    throw new Error("Missing GitHub App ID. Set --appId or GITHUB_APP_ID.");
  }

  const token = await createInstallationAccessToken({
    appId,
    privateKey,
    privateKeyPath,
    installationId,
    owner,
    repo
  });

  if (args.format === "env") {
    console.log(`GH_TOKEN=${token.token}`);
    return;
  }

  printJson({
    ok: true,
    authMode: "github_app_installation",
    owner: owner || null,
    repo: repo || null,
    installationId: token.installationId,
    resolvedFromRepo: token.resolvedFromRepo,
    expiresAt: token.expiresAt,
    token: token.token
  });
}

main().catch((error) => {
  printJson({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
    ...(error?.status ? { status: error.status } : {}),
    ...(error?.response ? { response: error.response } : {})
  });
  process.exit(1);
});
