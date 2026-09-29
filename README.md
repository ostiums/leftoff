# leftoff

**Your ChatGPT Work and Codex chats, inside Claude Code.**

leftoff copies the chats that ChatGPT's **Work mode**, the **Codex App** and the **Codex CLI** keep on your Mac into Claude Code's own session store. Install it once, and then:

- **`/resume` in Claude Code lists them** next to your Claude chats, as `⬡ ChatGPT: <title>` and `⬡ Codex: <title>`. Open one and continue where you left off, in the folder where it ran, with the whole conversation in place.
- **Claude can search them.** Ask "how did we fix the flaky auth tests?" and Claude looks through past chats of the project, Codex ones included, because they are ordinary Claude sessions now.
- **New chats show up on their own.** An async hook syncs them every time Claude Code starts. With nothing new it takes about 0.04 s and never delays startup.

[![License: MIT](https://img.shields.io/github/license/ostiums/leftoff)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ostiums/leftoff)](https://github.com/ostiums/leftoff/releases)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

![leftoff demo: in Claude Code, /resume lists ChatGPT and Codex chats next to a regular Claude chat; a ChatGPT Work chat opens and Claude says where the work stopped; after /clear, Claude answers a question about an old Codex CLI chat by searching the project's past chats](assets/demo.gif)

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ostiums/leftoff/main/install.sh | zsh
```

That's all. The installer imports your existing chats and turns on the sync hook, so the next `/resume` already shows them.

Claude Code's built-in `/import codex` brings over Codex configuration (MCP servers, AGENTS.md). leftoff brings over the chats.

There is also a `leftoff` command for the terminal: a picker over the chats that ran in the current folder, which opens the chosen one in Claude. It's handy when you're not in Claude yet, and optional.

## Where chats come from

All three write the same session files to `~/.codex/sessions`, so leftoff reads them the same way and labels each chat with its source:

| Source | How it's detected |
|---|---|
| ChatGPT Work | ChatGPT app for Mac, Work mode (`originator: codex_work_desktop`) |
| Codex App | Codex desktop app |
| Codex CLI | `codex` in the terminal, including `codex exec` |
| Codex IDE | Codex extension in VS Code or JetBrains |

Requirements: macOS or Linux with zsh, `git`, `python3` 3.9 or newer, Codex and Claude Code. No other dependencies; `fzf` is installed through Homebrew when brew is present.

## What gets carried over

- Your messages and the Codex replies, in order, including everything before a Codex context compaction.
- Tool calls as short text blocks (`[Codex tool: exec]`, the command, then the output cut to 2,000 characters), so Claude knows what was run without a multi-megabyte context.
- Screenshots you attached in Codex, as real images Claude can see.
- The chat title, shown in `/resume` as `⬡ Codex: <title>` or `⬡ ChatGPT: <title>`.

Left out: Codex system prompts, `<environment_context>` and AGENTS.md inserts, developer messages, encrypted reasoning, and the internal approval-reviewer sessions Codex Desktop creates. Codex data is only read, never modified.

## Compared with other tools

Measured on 2026-09-23 with one real Codex Desktop 0.155 chat: 113 tool calls, one context compaction, 43 assistant replies.

| Tool | What Claude gets | History carried | Codex system text in the history | Title in `/resume` |
|---|---|---|---|---|
| **leftoff** | native session | all 43 replies, tool calls as compact text: 189k characters | filtered out | `⬡ Codex: <title>` |
| [transession](https://github.com/inmzhang/transession) 0.2.0 | native session | all replies with full tool output and images: 2.6M characters, about 180k tokens on the first prompt | kept, replayed as user messages | first message, which is Codex system text |
| [codex2claude](https://github.com/MisterBrookT/codex2claude) | native session (runs transession, then cleans up) | full history, 5.8 MB session file | partly filtered | first message |
| [cli-continues](https://github.com/yigitkonur/cli-continues) 4.1.1 | a summary prompt in a new session | last 10 messages (50 with `--preset full`); replies from before the compaction are lost in the default preset | partly filtered | none |
| [authsec-bridge](https://github.com/authsec-ai/authsec-bridge) | native session | replies kept, 0 of 113 tool calls | kept | none |

Where the others do more: cli-continues moves sessions between 16 coding agents, and transession converts in both directions and keeps complete tool output.

## Usage

```sh
leftoff                     # chats from the CURRENT folder: pick one in fzf and open it in Claude
leftoff global              # same, across all folders (-g / --global is a synonym)
leftoff resume <id>         # open a specific chat (looked up across all folders)
leftoff list [-g] [--json]  # list chats: folder, title; current folder only by default
leftoff import <id>         # convert only, print the command to continue
leftoff preview <id>        # show the beginning of a chat
leftoff sync                # import all new/changed chats so they appear in /resume
leftoff autosync on|off     # run sync in the background at every Claude start
```

`<id>` is a full Codex session id or any unique part of it (6+ characters).

The chat always opens in Claude in the folder where it last ran in Codex (the
latest `turn_context`, falling back to where it started), even if
`leftoff global` was started somewhere else. Folders are compared after
resolving symlinks.

In the current-folder list you see the date and the chat title; in `global`
mode the folder name is shown between them (`~` for your home folder). When the
chats come from more than one app, a colored source column (ChatGPT Work, Codex
App, Codex CLI) appears before the title. The
preview is hidden; **Space** shows and hides it. Because of that you can't type
a space in the fzf search field, so search by a single word. The full path is
shown in the preview.

Inside Claude Code: `/leftoff` syncs all chats. Then pick one in the
built-in `/resume` picker: type `Codex` or `ChatGPT` to filter (imported chats
are titled `⬡ Codex: <title>` or `⬡ ChatGPT: <title>`), `Ctrl+A` shows chats from all folders, `Space` previews,
`Enter` opens.

## Conversion limits

- Tool call input is cut to 1,000 characters and output to 2,000.
- A single message is cut to 8,000 characters. If the whole history is longer than 400,000 characters, the oldest turns are dropped and a note at the start of the chat says how many.
- Attached images (PNG, JPEG, GIF, WebP up to 5 MB) cost roughly 1 to 1.5k tokens each per request. Screenshots taken by Codex tools are not embedded; the tool output shows `[screenshot]` instead.
- The first message starts with a note that the chat was moved from Codex, so Claude treats the `[Codex tool: …]` blocks as the Codex agent's actions.
- `↩Claude` in the list marks chats that Codex itself once imported from Claude.

## Installer details

The installer:
- clones the repo into `~/.local/share/leftoff` and links `~/.local/bin/leftoff`;
- copies the `/leftoff` slash command to `~/.claude/commands/`;
- turns on autosync: an async `SessionStart` hook in `~/.claude/settings.json` runs `leftoff sync --quiet` at every Claude start. It never delays startup; a sync with nothing new takes about 0.04 s with 150+ chats;
- runs the first sync, so chats are in `/resume` right away;
- installs `fzf` through Homebrew if brew is available (without fzf, chats are picked from a numbered list);
- adds `~/.local/bin` to PATH in `~/.zshrc` if it isn't there yet.

Install without autosync: `curl -fsSL …/install.sh | zsh -s -- --no-autosync`. Switch it later with `leftoff autosync on|off`; updates never turn it back on.

Update: `leftoff update`, or run the curl line again.

## Re-importing

The Claude session id is derived from the Codex session id, so re-importing
updates the same file. If the imported session has already been continued in
Claude (the file has grown), it is left untouched: a new session is created and
a warning is printed. `sync` only re-imports a chat when its Codex file has
changed since the last import, so continuing a chat in Claude never produces
duplicates by itself. State is kept in `~/.local/state/leftoff/imports.json`.

## Upgrading from codex-resume

leftoff was called codex-resume before. `codex-resume update` (or the curl line)
moves everything over: the clone goes to `~/.local/share/leftoff`, the command
becomes `leftoff`, `/codex-import` becomes `/leftoff`, the autosync hook and the
import state are carried over. The old GitHub URL redirects to the new one.

## Uninstall

```sh
leftoff autosync off
rm ~/.local/bin/leftoff ~/.claude/commands/leftoff.md
rm -rf ~/.local/state/leftoff ~/.local/share/leftoff
```

## Tests

```sh
python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
