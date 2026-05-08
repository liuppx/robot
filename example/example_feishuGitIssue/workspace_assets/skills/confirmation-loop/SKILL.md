---
name: confirmation-loop
description: |
  Use for preview -> confirm -> execute flows in Feishu/OpenClaw, especially for GitHub Issue creation and closing that must wait for /confirm, /submit, or /cancel.
---

# Confirmation Loop

Use this skill when the task involves:

- 保存待确认动作
- 展示确认草案
- 等待 `/confirm`
- 等待 `/submit`
- 等待 `/cancel`

## Tool

```bash
node tools/pending_action.mjs --action ...
```

## Rules

- Preview first, execute later.
- Saving a pending action is part of the preview flow.
- Real external write must not happen before explicit confirmation.
- New preview should replace old pending data in the same Feishu conversation scope.
- Confirmation is handled by the hook `hooks/confirmation-bridge`.
