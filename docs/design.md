# leftoff: continue ChatGPT Work and Codex chats in Claude Code

Date: 2026-09-23

## Goal

Pick any Codex-format chat (Codex App, Codex CLI, or ChatGPT app Work mode, which
all write `~/.codex/sessions` rollouts) and continue it in Claude Code as a native session
(`claude --resume`), in the same working directory, with the prior dialogue
visible to the model. One command from the terminal, or a slash command from
inside Claude Code.

Success criteria:

- `leftoff` → fuzzy picker → chosen chat opens in `claude --resume` in its
  original `cwd`, and Claude answers with awareness of the earlier conversation.
- Imported sessions show up in Claude's own `/resume` list with a recognizable
  title (`⬡ Codex: <title>`).
- Codex data is never modified.
- Re-importing a chat that got new messages in Codex refreshes the Claude
  session, but never destroys a continuation the user already made in Claude.

Non-goals: two-way sync; exporting Claude → Codex (Codex already does this);
reproducing Codex tool calls as real `tool_use` blocks; decrypting Codex
reasoning.

## Sources (Codex, read-only)

- Sessions: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` and
  `~/.codex/archived_sessions/rollout-*.jsonl`.
- Titles: `~/.codex/session_index.jsonl` (`{id, thread_name, updated_at}`; the
  last line per id wins).
- Claude-origin registry: `~/.codex/external_agent_session_imports.json`
  (`records[].imported_thread_id`, `title`, `source_path`) — chats Codex itself
  imported from Claude. Used for the title fallback, an `↩Claude` marker in
  the list, and to tell copies from chats (see "Copies of Claude sessions").

Record shapes used (line = `{timestamp, type, payload}`):

| type / payload.type | Use |
|---|---|
| `session_meta` | `id`, `cwd`, `timestamp`, `source`, `thread_source`, `parent_thread_id` |
| `response_item/message` role `user`/`assistant` | dialogue; content items `input_text`/`output_text`/`input_image` |
| `response_item/message` role `developer` | dropped |
| `response_item/function_call` + `function_call_output` | tool call (`name`, `arguments` JSON string / `output` string) paired by `call_id` |
| `response_item/custom_tool_call` + `custom_tool_call_output` | tool call (`name`, `input` string / `output` list of `{text}` or string) paired by `call_id` |
| `compacted` | `replacement_history` = the dialogue that replaced everything before it |
| everything else (`reasoning`, `compaction`, `event_msg`, `turn_context`, `world_state`, `token_usage_record`) | dropped |

### Which sessions are chats

Excluded from listing: sessions whose `session_meta.source` is an object with
`subagent`, or whose `thread_source == "guardian_review"` (approval reviewers),
and sessions with zero real user messages after filtering.

### Copies of Claude sessions

Codex imports Claude sessions as threads of its own, including the sessions leftoff wrote.
Importing those back would loop: every round adds another `⬡ Codex: ⬡ Codex: …` session on
each side (measured: 112 of 176 registry records pointed at leftoff's own sessions, 41 of
them copies of copies).

A thread is a copy if its id is an `imported_thread_id` in the registry, or if its first user
message starts with our context header (`MOVED_PREFIXES`, the English header and the earlier
Russian one). The header is the fallback for threads the registry doesn't list; it only
catches copies of leftoff's sessions, and only while Codex keeps the first message.

A copy is untouched until a turn is run on it in Codex, and only that writes a `turn_context`
record (the import itself writes `session_meta`, `response_item` and `event_msg` only). An
untouched copy holds nothing Claude doesn't have, so it is not a chat: `sync` skips it, the
list and the picker hide it (`list --all` shows it), an explicit id still resolves and
imports it into a session of its own.

A copy that was continued is imported. If it opens with our header, that header is taken off
the first message and put back on top after the size cut, in place of a new one, so there is
always exactly one. The header and any `⬡ …:` prefixes are stripped from a copy's title; if
that leaves a title source empty, the next one is used. Titles of ordinary chats are left as
they are.

- Into the Claude session it was copied from, when leftoff wrote that session, it sits in
  the project folder of the copy's cwd, its mtime still equals the registry's
  `source_modified_at` (nanoseconds, exact) and it has no more lines than leftoff wrote. The
  copy holds all of that session plus the new turns, so `/resume` keeps one line. The state
  then maps the copy's id to that session, and the entry of the chat it first came from gets
  `lines_written: 0`, so a later change to that chat goes to a fresh session. On later
  imports of the same copy only the line count is checked: the mtime is leftoff's own by then.
  A second copy of the same session gets a session of its own.
- Otherwise into a session of its own, as any other chat. A Claude chat leftoff didn't write
  is never overwritten.

### Title

First non-empty of: latest `thread_name` from `session_index.jsonl`; `title`
from the Claude-origin registry; first real user message (single line, 60
chars). Leading `⬡ Codex: ` / `⬡ ChatGPT: ` prefixes and our context header are
stripped, so the title of a copy doesn't stack markers.

## Conversion

### Compaction

`compacted` records are ignored and the full raw rollout is converted. (Real
data: `replacement_history` holds only user messages plus an encrypted
`compaction` summary, while the append-only rollout keeps every original
record.)

### Size limits

Each Codex user/assistant message item is truncated to 8000 chars (before items are merged into turns). If the assembled
history exceeds 400 000 chars, the oldest turns are dropped and the first user
turn gets `[N earlier turns were not carried over because of size — they remain in Codex.]`.

### User messages — noise filter

A user text item is dropped if, after `lstrip()`, it starts with any of:

`<environment_context>`, `<app-context>`, `<recommended_plugins>`,
`<guardian_tool_descriptions>`, `<user_instructions>`, `<INSTRUCTIONS>`,
`<skill>`, `<turn_aborted>`, `# AGENTS.md instructions`.

`input_image` items become `[image]`. Everything else is kept verbatim
(including `<task-notification>` and text from Claude-origin transcripts — it
is real history). A user message whose items all get dropped is dropped.

### Assistant side

- `output_text` → text.
- Tool call → text block:

  ```
  [Codex tool: <name>]
  <input, ≤1000 chars>
  → <output, ≤2000 chars>
  ```

  Truncated parts end with `…[truncated, N chars]`. For `function_call`, the
  input is `arguments`; if that JSON has `cmd`/`command`/`code`, that value is
  shown instead of the raw JSON. An output with no matching call is dropped; a
  call with no output shows `→ (no output)`.

### Turn assembly

Items are folded into strictly alternating turns: consecutive user items merge
into one user turn (joined by blank line); consecutive assistant texts/tool
blocks merge into one assistant turn. A leading assistant turn gets a synthetic
user turn `[Continuing a chat from Codex]`. If the transcript ends with a user
turn, it is kept as is (Claude will answer it on the next prompt).

A final synthetic pair is **not** added — the user's next prompt continues the
chat.

The first user turn is prefixed with a context header:

```
[This chat was moved from Codex (<YYYY-MM-DD>, cwd <cwd>). The assistant replies
below were written by the Codex agent; [Codex tool: …] blocks are commands it ran
and their output. Continue the work with this history in mind.]
```

(Verified in a spike: without it the model disowns the `[Codex tool: …]`
blocks as not its own actions.)

## Output (Claude Code, write)

Path: `~/.claude/projects/<slug>/<session_id>.jsonl`, where `slug` is the cwd
with every non-alphanumeric char replaced by `-` (matches existing dirs, e.g.
`/Users/alice/work/my-app` → `-Users-alice-work-my-app`).

If the recorded cwd no longer exists, `~` is used and a warning is printed.

Records, in order:

1. One `user` / `assistant` record per turn, chained by `parentUuid` (first is
   `null`). Common fields: `parentUuid`, `isSidechain: false`, `type`,
   `message`, `uuid`, `timestamp` (from the source item), `userType:
   "external"`, `entrypoint: "cli"`, `cwd`, `sessionId`, `version` (current
   `claude --version`, fallback `"2.1.0"`), `gitBranch: ""`.
   - user: `message = {role: "user", content: "<text>"}`.
   - assistant: `message = {id: "msg_codex_<n>", type: "message", role:
     "assistant", model: "<synthetic>" (any unknown id makes Claude Code warn "Session model … could not be restored" on resume), content: [{type: "text", text}],
     stop_reason: "end_turn", stop_sequence: null, usage: {input_tokens: 0,
     output_tokens: 0}}`.
2. `{"type": "custom-title", "customTitle": "⬡ Codex: <title>", "sessionId"}`.

This field set was validated in a spike: `claude -p --resume <sid>` on a
hand-built file with exactly these records resumes correctly and the model
recalls the imported content.

### Session id and re-import

- `session_id = uuid5(NAMESPACE, codex_id)` with a fixed namespace UUID.
- State file `~/.local/state/leftoff/imports.json`:
  `{codex_id: {session_id, path, lines_written}}`.
- On import, if the target file exists and has more lines than
  `lines_written` (user continued in Claude), write to a fresh id
  `uuid5(NAMESPACE, f"{codex_id}:{k}")` with the smallest free `k ≥ 1`, and
  print a note that the earlier continuation was kept. Otherwise overwrite.
- Writes go to a temp file in the same dir, then `os.replace`.

## Interface

Single file `leftoff.py` (stdlib only, Python ≥ 3.9). The installer symlinks it as
`~/.local/bin/leftoff` (launchers on Windows, see below). In the plugin, `bin/leftoff` runs it.

Scope: by default `list` and the picker show only chats whose recorded cwd
equals the current directory (both `realpath`-resolved). `leftoff global`
or `-g/--global` shows chats from all folders. An explicit `<id>` always
resolves across all folders. The Claude session is always created and opened
in the chat's own cwd. An empty local list prints a hint pointing to
`leftoff global` (exit 0 for `list`, exit 1 for the picker).

```
leftoff                 # = resume (interactive picker, current folder)
leftoff global [<id>]   # picker over all folders
leftoff list [--json]   # chats, newest first
leftoff import <id>     # convert; print session id, path, resume command
leftoff resume [<id>]   # import, then chdir(cwd) and exec `claude --resume <sid>`
leftoff preview <id>    # first ~15 turns as plain text (fzf preview)
leftoff update          # git pull own checkout + re-run the installer (the plugin: use /plugin)
```

`<id>` accepts the full Codex id or any unique substring of it (≥ 6 chars).

Row layout: `<date YYYY-MM-DD HH:MM>  [<folder name, padded, ≤24>  ]<title>[ ↩Claude]` + tab +
full id. The folder column appears only in global mode (`~` for home). No
message-count column. `--json` prints objects `{id, title, cwd, updated,
user_turns, from_claude}`.

Picker: `fzf --delimiter '\t' --with-nth 1`, preview `leftoff preview {2}`
hidden by default and toggled with Space (`--bind space:toggle-preview`; the
query therefore cannot contain spaces). If `fzf` is missing, fall back to a
numbered menu reading a number from stdin. Cancel → exit 130, nothing written.

Errors: unknown/ambiguous id → message listing candidates, exit 2; malformed
JSON lines (e.g. a half-written last line) are skipped silently. An explicit
id is resolved among visible chats first, then among hidden service sessions.

### Sync and autosync

`leftoff sync` imports every visible chat whose rollout is new or changed.
The state file keeps `__sources__`: rollout path → `[mtime_ns, size]` recorded at
the last import/sync; matching files are skipped without being opened (measured:
0.04 s for 204 rollouts / 98 MB; first full sync 0.8 s). A chat is re-imported
only when its Codex file changed, so a Claude-side continuation never causes a
duplicate by itself.

`leftoff autosync on|off|status` adds/removes one `SessionStart` hook
`{"type": "command", "command": "<abs path>/leftoff sync --quiet", "async": true}`
in `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`, leaving every other setting
untouched and refusing to write if the file can't be parsed. `on` also runs the
first sync immediately.

### Slash command

`commands/leftoff.md` (installed as `/leftoff`): injects the output of
`leftoff sync` and `autosync status` with `` !`…` `` and tells the user to
pick the chat in the built-in `/resume` picker (type `Codex` to filter, `Ctrl+A`
for all folders). Custom commands can't render their own full-screen picker, and
AskUserQuestion is limited to 4 options, so the native picker is used instead.

## Install

`install.sh` (zsh, re-runnable): symlink `~/.local/bin/leftoff`, copy the
slash command into `${CLAUDE_CONFIG_DIR:-~/.claude}/commands/`, `brew install
fzf` if fzf and brew are available, and add `~/.local/bin` to PATH via
`~/.zshrc` if missing. `leftoff update` runs `git pull --ff-only` on the
tool's own checkout and re-runs `install.sh` (`install.ps1` on Windows). With the plugin
on, the installers add only the terminal command (see below).

## Claude Code plugin

The repo is also a plugin and a one-plugin marketplace (`.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json` with `source: "./"`). The marketplace is named `ostiums`,
after the owner and not the plugin, so the plugin is installed as `leftoff@ostiums`:
`/plugin marketplace add ostiums/leftoff`, `/plugin install leftoff@ostiums`. The plugin
root is the repo root, so it carries `leftoff.py` itself.

- `commands/leftoff.md` is picked up as the plugin's `/leftoff` (also `/leftoff:leftoff`).
  It calls a bare `leftoff`, which resolves to the plugin's `bin/leftoff`: Claude Code puts a
  plugin's `bin/` on the Bash tool's PATH.
- `hooks/hooks.json` runs `sh "${CLAUDE_PLUGIN_ROOT}/bin/leftoff" sync --quiet`, async, at
  every `SessionStart`. Shell-form hooks run in `sh -c` on macOS/Linux and in Git Bash on
  Windows, so the plugin needs Git for Windows there (the same assumption as Anthropic's
  Python-based plugins). Exec form was ruled out: on Windows it needs a real `.exe`, and
  there is no Python command name that exists on every OS.
- `bin/leftoff` (POSIX sh, LF-only via `.gitattributes`) probes `python3`, `python`,
  `py -3` for 3.9+ and execs the first that works. The probe skips the Microsoft Store
  `python3` stub. Under Git Bash the script path goes through `cygpath -w`.
- `version` in `plugin.json` is explicit, so users get an update only when it is bumped.
- The plugin is detected through `enabledPlugins["leftoff@ostiums"]` in the user
  `settings.json` (also `leftoff@leftoff`, the id in 0.2.0, when the marketplace was named
  `leftoff`). Then `autosync` reports the plugin's hook and never writes its own
  (`autosync on` removes one left by the installer), the installers skip `/leftoff` in
  `~/.claude/commands`, and `leftoff update` in the plugin's cache points to `/plugin`.
  While the installer's hook is still there next to the plugin, `autosync status` says so
  (and `/leftoff` passes that on). Only the user `settings.json` is checked: a plugin
  installed at project or local scope isn't detected, and the installer's hook then runs a
  second sync in those projects (harmless, the lock turns it into a no-op).

## Windows

Same layout under `%USERPROFILE%` (`~\.codex`, `~\.claude`, `~\.local\…`). What differs:

- `install.ps1` (run with `irm … | iex`) writes two launchers into `~\.local\bin` instead of
  a symlink: `leftoff.cmd` (PowerShell, cmd) and an extensionless `sh` script (Git Bash, which
  Claude Code uses for `!` commands when Git for Windows is installed). Both call the Python
  found at install time by its full path. `~\.local\bin` goes into the user PATH (registry,
  raw value, so `%VAR%` entries stay unexpanded).
- The autosync hook is `& '<home>\.local\bin\leftoff.cmd' sync --quiet` with
  `"shell": "powershell"`, so it runs without Git Bash. The hook is recognized by the regex
  `AUTOSYNC_COMMAND`, which also matches quoted program paths.
- `/leftoff` allows both `Bash(leftoff *)` and `PowerShell(leftoff *)`.
- The sync lock is a one-byte `msvcrt.locking` lock instead of `flock`.
- Opening a chat waits for `claude` (`subprocess.run`, SIGINT ignored) instead of `execvp`,
  which on Windows starts a child and exits, leaving the shell and claude on one console.
- stdout and stderr are UTF-8 (fzf preview and `/leftoff` read them through pipes). Files
  are written with LF. `os.replace` is retried briefly when the target is open elsewhere.
- Path comparisons use `os.path.normcase`. A `\\?\` prefix on a Codex cwd is dropped.
  Slugs need no change: `C:\Users\alice\app` → `C--Users-alice-app`, as Claude Code names them.
- The fzf preview command is `"C:/…/python.exe" "C:/…/leftoff.py" preview {2}`, which both
  cmd.exe (fzf's default) and bash (when `$SHELL` is set) accept. The resume hint is PowerShell:
  `cd '<cwd>'; claude --resume <id>`.

## Testing

`unittest` (stdlib) with small fixture rollouts covering: noise filter,
developer drop, tool call rendering + truncation + pairing, compaction,
turn alternation, subagent exclusion, title priority, slug, re-import
protection, id suffix resolution, copies of Claude sessions (skipped, updated in
place, kept apart). Plus a manual end-to-end check: import one
real chat, `claude --resume` it, ask "what were we talking about?", confirm the answer
reflects the Codex chat.
