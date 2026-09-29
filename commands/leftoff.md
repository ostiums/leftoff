---
description: Sync ChatGPT Work and Codex chats into Claude Code's /resume list
allowed-tools: Bash(leftoff *)
---

Sync result:

!`leftoff sync 2>&1`

!`leftoff autosync status 2>&1`

Do not run any tools. Reply in Russian in at most 4 short lines:
- the sync result above (how many chats were imported);
- how to pick one: run `/resume`, type `Codex` or `ChatGPT` to filter (imported chats are titled `⬡ Codex: <title>` or `⬡ ChatGPT: <title>` for ChatGPT Work mode), `Ctrl+A` shows chats from all folders, `Space` previews, `Enter` opens;
- only if autosync is off: `leftoff autosync on` keeps the list up to date automatically at every Claude start.
