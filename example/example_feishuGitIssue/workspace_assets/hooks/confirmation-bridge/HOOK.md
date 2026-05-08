---
name: confirmation-bridge
description: "Handle /confirm, /submit, and /cancel in Feishu for saved pending GitHub Issue create / close actions."
metadata:
  { "openclaw": { "emoji": "✅", "events": ["message:received"], "requires": { "bins": ["node"] } } }
---

# Confirmation Bridge

This hook listens for Feishu confirmation messages and turns saved pending actions into real execution.

It should:

- read pending action for the current Feishu conversation
- detect `/confirm`, `/submit`, `/cancel`
- allow only requester or admins
- execute GitHub Issue create or close
- reply back into the same Feishu conversation
