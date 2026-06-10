#!/usr/bin/env node

export const ISSUER_COMMANDS = [
  {
    name: "/help",
    usage: "/help",
    example: "/help",
    description: "查看机器人能力、命令和注意事项。"
  },
  {
    name: "/confirm",
    usage: "/confirm [repo|draft:<id>]",
    example: "/confirm robot",
    description: "提交当前待执行草案；多草案时必须显式指定仓库或 draft。"
  },
  {
    name: "/cancel",
    usage: "/cancel [repo|draft:<id>]",
    example: "/cancel draft:abcd1234",
    description: "取消当前待执行草案。"
  }
];

function buildList(title, items) {
  return [title, ...items].join("\n");
}

export function buildRepoAliasesSection(policy) {
  const aliases = Array.isArray(policy?.repoAliases) ? policy.repoAliases.filter(Boolean) : [];
  if (aliases.length === 0) {
    return "";
  }
  return buildList(
    "仓库别名：",
    aliases.slice(0, 20).map((item) => `- ${item.alias} -> ${item.owner}/${item.repo}`)
  );
}

export function buildCommandsSection() {
  return buildList(
    "命令：",
    ISSUER_COMMANDS.map((command) => `- ${command.usage}\n  说明：${command.description}\n  示例：${command.example}`)
  );
}
