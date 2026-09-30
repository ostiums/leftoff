# leftoff

**Your ChatGPT Work and Codex chats, inside Claude Code.**

leftoff copies the chats that ChatGPT's **Work mode**, the **Codex App** and the **Codex CLI** keep on your computer into Claude Code's own session store. Install it once, and then:

- **`/resume` in Claude Code lists them** next to your Claude chats, as `⬡ ChatGPT: <title>` and `⬡ Codex: <title>`. Open one and continue where you left off, in the folder where it ran, with the whole conversation in place.
- **Claude can search them.** Ask "how did we fix the flaky auth tests?" and Claude looks through past chats of the project, Codex ones included, because they are ordinary Claude sessions now.
- **New chats show up on their own.** An async hook syncs them every time Claude Code starts. With nothing new it takes about 0.04 s and never delays startup.

[![License: MIT](https://img.shields.io/github/license/ostiums/leftoff)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ostiums/leftoff)](https://github.com/ostiums/leftoff/releases)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![macOS | Linux | Windows](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

![leftoff demo: in Claude Code, /resume lists ChatGPT and Codex chats next to a regular Claude chat, a ChatGPT Work chat opens and Claude says where the work stopped, and after /clear, asked whether it can read Codex chats now, Claude says it can continue any of them and lists the four chats of the project](assets/demo.gif)

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ostiums/leftoff/main/install.sh | zsh
```

On Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/ostiums/leftoff/main/install.ps1 | iex
```

That's all. The installer imports your existing chats and turns on the sync hook, so the next `/resume` already shows them. What the hook runs and why is explained [below](#the-hook-in-claude-code-settings).

Claude Code's built-in `/import codex` brings over Codex configuration (MCP servers, AGENTS.md). leftoff brings over the chats.

There is also a `leftoff` command for the terminal: a picker over the chats that ran in the current folder, which opens the chosen one in Claude. It's handy when you're not in Claude yet, and optional.

## The hook in Claude Code settings

The installer adds one entry to `~/.claude/settings.json`:

```json
"hooks": {
  "SessionStart": [
    { "hooks": [{ "type": "command", "command": "/Users/you/.local/bin/leftoff sync --quiet", "async": true }] }
  ]
}
```

Claude Code runs this command every time a session starts, in any folder, including chats you open in parallel.

On Windows the entry is `{ "type": "command", "command": "& 'C:\\Users\\you\\.local\\bin\\leftoff.cmd' sync --quiet", "shell": "powershell", "async": true }`. The `shell` field makes Claude Code run it in PowerShell, so it works with or without Git Bash.

**Why it's needed.** `/resume` and Claude's search through past chats only see the session files in `~/.claude/projects`. Claude Code has no way to ask another tool for more chats, so a Codex chat has to be copied there before you go looking for it. Session start is the only moment Claude Code runs something on its own. Without the hook, a chat you had in Codex an hour ago stays out of `/resume` until you run `leftoff sync` or `/leftoff`.

**What it does and doesn't do.**
- Reads `~/.codex/sessions` and never changes anything there.
- Writes one Claude session file per Codex chat to `~/.claude/projects/<folder>/`, plus its own state in `~/.local/state/leftoff/`. No other files. A chat you've already continued in Claude is never overwritten.
- Makes no network requests. The hook runs the copy of leftoff already on your disk and never downloads or updates it. The code changes only when you run `leftoff update`.
- Doesn't slow Claude down. `async: true` means Claude Code starts without waiting for it, and with nothing new it finishes in about 0.04 s.
- Runs once when several sessions start together. The first sync takes a lock, the others see it and exit right away, so two syncs never write the same files.

**Checking it and turning it off.** The hook runs `leftoff.py` from `~/.local/share/leftoff`, a single Python file that uses only the standard library, so you can read all of it. `leftoff autosync off` removes the entry and keeps your other settings and hooks. `leftoff autosync on` puts it back. To install without it, end the install command with `| zsh -s -- --no-autosync`, or on Windows run `iex "& {$(irm https://raw.githubusercontent.com/ostiums/leftoff/main/install.ps1)} -NoAutosync"`. leftoff refuses to write `settings.json` if it can't parse the file.

## Where chats come from

All three write the same session files to `~/.codex/sessions`, so leftoff reads them the same way and labels each chat with its source:

| Source | How it's detected |
|---|---|
| ChatGPT Work | ChatGPT app for Mac, Work mode (`originator: codex_work_desktop`) |
| Codex App | Codex desktop app |
| Codex CLI | `codex` in the terminal, including `codex exec` |
| Codex IDE | Codex extension in VS Code or JetBrains |

Requirements: macOS or Linux with zsh, or Windows 10/11 with PowerShell. Also `git`, Python 3.9 or newer, Codex and Claude Code. No other dependencies. `fzf` is installed through Homebrew when brew is present, or through winget on Windows.

## What gets carried over

- Your messages and the Codex replies, in order, including everything before a Codex context compaction.
- Tool calls as short text blocks (`[Codex tool: exec]`, the command, then the output cut to 2,000 characters), so Claude knows what was run without a multi-megabyte context.
- Screenshots you attached in Codex, as real images Claude can see.
- The chat title, shown in `/resume` as `⬡ Codex: <title>` or `⬡ ChatGPT: <title>`.

Left out: Codex system prompts, `<environment_context>` and AGENTS.md inserts, developer messages, encrypted reasoning, and the internal approval-reviewer sessions Codex Desktop creates. Codex data is only read, never modified.

## Compared with other tools

Measured on 2026-09-23 with two real Codex Desktop 0.155 chats, a short one and a long one. Each converted chat was opened in Claude Code, and Claude was asked what the chat was about. The table shows the long chat: 113 tool calls, one context compaction, 43 assistant replies.

| Tool | What Claude gets | History carried | Codex system text in the history | Title in `/resume` |
|---|---|---|---|---|
| **leftoff** | native session | all 43 replies, tool calls as compact text: 189k characters | filtered out | `⬡ Codex: <title>` |
| [transession](https://github.com/inmzhang/transession) 0.2.0 | native session | all replies with full tool output and images: 2.6M characters, about 180k tokens on the first prompt, and one reply on Haiku cost $0.53 | kept, replayed as user messages | first message, which is Codex system text |
| [codex2claude](https://github.com/MisterBrookT/codex2claude) | native session (runs transession, then cleans up) | full history, 5.8 MB session file | partly filtered | first message |
| [cli-continues](https://github.com/yigitkonur/cli-continues) 4.1.1 | a summary prompt in a new session | last 10 messages (50 with `--preset full`), and replies from before the compaction are lost in the default preset | partly filtered | none |
| [authsec-bridge](https://github.com/authsec-ai/authsec-bridge) | native session | replies kept, 0 of 113 tool calls | kept | none |
| [resume-cli](https://github.com/danishaft/resume-cli-) | a summary prompt in a new session | nothing: it can't read the Codex Desktop format, and Claude answered that it had no context | not applicable | not applicable |

Where the others do more: cli-continues moves sessions between 16 coding agents, and transession converts in both directions and keeps complete tool output.

Two leftoff features come from this comparison. Images you attached in Codex are passed to Claude as real images, the way transession does it. The folder a chat opens in is taken from its latest `turn_context`, as in [PavelCz's fork of cli-continues](https://github.com/PavelCz/cli-continues), so a chat that moved to another folder opens where it ended.

## Usage

```sh
leftoff                     # chats from the CURRENT folder: pick one in fzf and open it in Claude
leftoff global              # same, across all folders (-g / --global is a synonym)
leftoff resume <id>         # open a specific chat (looked up across all folders)
leftoff list [-g] [--json]  # list chats: folder, title, current folder only by default
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

In the current-folder list you see the date and the chat title. In `global`
mode the folder name is shown between them (`~` for your home folder). When the
chats come from more than one app, a colored source column (ChatGPT Work, Codex
App, Codex CLI) appears before the title. The
preview is hidden, and **Space** shows and hides it. Because of that you can't type
a space in the fzf search field, so search by a single word. The full path is
shown in the preview.

Inside Claude Code: `/leftoff` syncs all chats. Then pick one in the
built-in `/resume` picker: type `Codex` or `ChatGPT` to filter (imported chats
are titled `⬡ Codex: <title>` or `⬡ ChatGPT: <title>`), `Ctrl+A` shows chats from all folders, `Space` previews,
`Enter` opens.

## Conversion limits

- Tool call input is cut to 1,000 characters and output to 2,000.
- A single message is cut to 8,000 characters. If the whole history is longer than 400,000 characters, the oldest turns are dropped and a note at the start of the chat says how many.
- Attached images (PNG, JPEG, GIF, WebP up to 5 MB) cost roughly 1 to 1.5k tokens each per request. Screenshots taken by Codex tools are not embedded, the tool output shows `[screenshot]` instead.
- The first message starts with a note that the chat was moved from Codex, so Claude treats the `[Codex tool: …]` blocks as the Codex agent's actions.
- `↩Claude` in the list marks chats that Codex itself once imported from Claude.

## Installer details

The installer:
- clones the repo into `~/.local/share/leftoff` and links `~/.local/bin/leftoff`
- copies the `/leftoff` slash command to `~/.claude/commands/`
- turns on autosync: an async `SessionStart` hook in `~/.claude/settings.json` runs `leftoff sync --quiet` at every Claude start (see [The hook in Claude Code settings](#the-hook-in-claude-code-settings))
- runs the first sync, so chats are in `/resume` right away
- installs `fzf` through Homebrew if brew is available (without fzf, chats are picked from a numbered list)
- adds `~/.local/bin` to PATH in `~/.zshrc` if it isn't there yet.

Install without autosync: `curl -fsSL …/install.sh | zsh -s -- --no-autosync`. Switch it later with `leftoff autosync on|off`. Updates never turn it back on.

Update: `leftoff update`, or run the curl line again.

On Windows, `install.ps1` does the same with these differences:
- the clone goes to `~\.local\share\leftoff` as well, and `~\.local\bin` gets two small launchers instead of a link: `leftoff.cmd` for PowerShell and cmd, and `leftoff` for Git Bash
- it looks for Python as `py -3`, `python` or `python3` and writes its full path into the launchers and the hook, so after moving to another Python, run the install line again
- the hook runs in PowerShell (see [above](#the-hook-in-claude-code-settings))
- `fzf` comes from winget, and `~\.local\bin` is added to your user PATH instead of `~/.zshrc`.

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

On Windows, in PowerShell:

```powershell
leftoff autosync off
Remove-Item ~\.local\bin\leftoff, ~\.local\bin\leftoff.cmd, ~\.claude\commands\leftoff.md
Remove-Item -Recurse -Force ~\.local\state\leftoff, ~\.local\share\leftoff
```

## Tests

```sh
python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
