#!/usr/bin/env python3
"""leftoff: pick up your ChatGPT Work and Codex chats where you left off, in Claude Code.

Converts a Codex rollout (~/.codex/sessions/**/rollout-*.jsonl, written by Codex App,
Codex CLI and ChatGPT's Work mode) into a native Claude Code session file so it can be
opened with `claude --resume`.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
import re
from dataclasses import dataclass, field
from pathlib import Path

WINDOWS = os.name == "nt"
if WINDOWS:
    import msvcrt
else:
    import fcntl

NAMESPACE = uuid.UUID("6f1c2b1e-3c1a-4d7e-9a57-2f0c0de5e5a1")
TOOL_INPUT_LIMIT = 1000
TOOL_OUTPUT_LIMIT = 2000
MESSAGE_LIMIT = 8000  # per user/assistant message
TOTAL_LIMIT = 400_000  # whole imported history; oldest turns are dropped beyond this
IMAGE_MAX_CHARS = 5 * 1024 * 1024  # base64 payload cap per image (Claude API limit is 5 MB)
IMAGE_DATA_URL = re.compile(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)\Z")
DIR_COLUMN_MAX = 24
PREVIEW_TURNS = 15
PREVIEW_CHARS = 600
TITLE_PREFIX = "⬡ Codex: "  # hexagon marks imported chats in /resume; plain text, so search and rename keep working
WORK_TITLE_PREFIX = "⬡ ChatGPT: "
TITLE_MARKERS = re.compile(f"^(?:{re.escape(TITLE_PREFIX)}|{re.escape(WORK_TITLE_PREFIX)})+")
WORK = "ChatGPT Work"  # ChatGPT app's Work mode runs the Codex agent and writes the same rollouts
SOURCE_COLORS = {WORK: "32", "Codex App": "36", "Codex CLI": "35", "Codex IDE": "33"}  # ANSI, themed by the terminal
LEAD_USER_TEXT = "[Continuing a chat from Codex]"
# How the context header starts (the second one is from before the header was in English): a Codex
# chat that opens with it is Codex's own copy of a session leftoff wrote.
MOVED_PREFIXES = ("[This chat was moved from ", "[Этот чат перенесён из ")
NOISE_PREFIXES = (
    "<environment_context>", "<app-context>", "<recommended_plugins>",
    "<guardian_tool_descriptions>", "<user_instructions>", "<INSTRUCTIONS>",
    "<skill>", "<turn_aborted>", "# AGENTS.md instructions",
)

# ---------------------------------------------------------------- parse


@dataclass
class Item:
    role: str  # "user" | "assistant" | "tool"
    text: str  # message text, or tool input for role == "tool"
    ts: str | None
    name: str = ""
    output: str | None = None
    images: list[dict] = field(default_factory=list)  # Claude image blocks (user messages only)


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    parts: list[str]
    ts: str | None
    images: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.parts)


def load_jsonl(path) -> tuple[list[dict], int]:
    records, bad = [], 0
    with open(path, "rb") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                bad += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                bad += 1
    return records, bad


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def atomic_write(path: Path, text: str) -> None:
    # A temp name of its own, so two writers never share (and steal) one temp file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:  # LF on Windows too
            f.write(text)
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: the target is open in another process for a moment
                if not WINDOWS or attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"…[truncated, {len(s)} chars]"


def image_block(url) -> dict | None:
    """Codex `input_image` data URL → Claude image block; None if unusable (then only a text marker remains)."""
    m = IMAGE_DATA_URL.match(url) if isinstance(url, str) else None
    if not m or len(m.group(2)) > IMAGE_MAX_CHARS:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}


def _message_content(payload: dict) -> tuple[str, list[dict]]:
    role = payload.get("role")
    parts, images = [], []
    for c in payload.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "input_image":
            parts.append("[image]")
            block = image_block(c.get("image_url")) if role == "user" else None
            if block:
                images.append(block)
            continue
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and text.lstrip().startswith(NOISE_PREFIXES):
            continue
        parts.append(text)
    return "\n\n".join(parts), images


def _tool_input(payload: dict) -> str:
    if payload.get("type") == "custom_tool_call":
        return str(payload.get("input") or "")
    raw = payload.get("arguments") or ""
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return str(raw)
    if isinstance(args, dict):
        for key in ("cmd", "command", "code"):
            value = args.get(key)
            if isinstance(value, list):
                return " ".join(map(str, value))
            if isinstance(value, str):
                return value
    return str(raw)


def _tool_output(payload: dict) -> str:
    out = payload.get("output")
    if isinstance(out, list):
        return "\n".join("[screenshot]" if c.get("type") == "input_image" else c.get("text", "")
                         for c in out if isinstance(c, dict))
    if out is None:
        return ""
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def extract_items(records: list[dict]) -> list[Item]:
    items: list[Item] = []
    calls: dict[str, Item] = {}

    def add(payload: dict, ts: str | None) -> None:
        kind = payload.get("type")
        if kind == "message" and payload.get("role") in ("user", "assistant"):
            text, images = _message_content(payload)
            text = truncate(text, MESSAGE_LIMIT)
            if text:
                items.append(Item(payload["role"], text, ts, images=images))
        elif kind in ("function_call", "custom_tool_call"):
            item = Item("tool", _tool_input(payload), ts, name=str(payload.get("name") or "?"))
            items.append(item)
            if payload.get("call_id"):
                calls[payload["call_id"]] = item
        elif kind in ("function_call_output", "custom_tool_call_output"):
            item = calls.get(payload.get("call_id"))
            if item is not None:
                item.output = _tool_output(payload)

    # `compacted` records are ignored: their replacement_history is only the user
    # messages plus an encrypted summary, while the rollout keeps the full raw history.
    for rec in records:
        if rec.get("type") == "response_item" and isinstance(rec.get("payload"), dict):
            add(rec["payload"], rec.get("timestamp"))
    return items


def render_tool(item: Item) -> str:
    output = "(no output)" if item.output is None else truncate(item.output, TOOL_OUTPUT_LIMIT)
    return f"[Codex tool: {item.name}]\n{truncate(item.text, TOOL_INPUT_LIMIT)}\n→ {output}"


def build_turns(items: list[Item], header: str | None = None, max_chars: int = TOTAL_LIMIT) -> list[Turn]:
    turns: list[Turn] = []
    for item in items:
        role = "user" if item.role == "user" else "assistant"
        text = render_tool(item) if item.role == "tool" else item.text
        if not (turns and turns[-1].role == role):
            turns.append(Turn(role, [], item.ts))
        turns[-1].parts.append(text)
        turns[-1].images.extend(item.images)
    dropped = 0
    total = sum(len(t.text) for t in turns)
    while len(turns) > 1 and total > max_chars:
        total -= len(turns.pop(0).text)
        dropped += 1
    if turns and turns[0].role == "assistant":
        turns.insert(0, Turn("user", [LEAD_USER_TEXT], turns[0].ts))
    if turns and dropped:
        turns[0].parts.insert(0, f"[{dropped} earlier turns were not carried over because of size — they remain in Codex.]")
    if turns and header:
        turns[0].parts.insert(0, header)
    return turns


def context_header(date: str, cwd: str, source: str = "Codex App") -> str:
    where, agent = ("ChatGPT (Work mode)", "ChatGPT") if source == WORK else ("Codex", "Codex")
    return (f"{MOVED_PREFIXES[0]}{where} ({date}, cwd {cwd}). The assistant replies below "
            f"were written by the {agent} agent; [Codex tool: …] blocks are commands it ran and "
            "their output. Continue the work with this history in mind.]")


# ---------------------------------------------------------------- discover


@dataclass
class SessionInfo:
    id: str
    path: Path
    cwd: str
    started: str
    updated: float
    title: str
    user_turns: int
    from_claude: bool
    is_chat: bool
    source: str = "Codex App"
    copied_from: Path | None = None  # a copy continued in Codex: the Claude session file Codex imported it from
    copied_mtime: int = 0  # that file's mtime_ns at the time, per Codex's registry


def one_line(s: str, n: int = 60) -> str:
    s = " ".join(s.split())
    return s[: n - 1] + "…" if len(s) > n else s


def is_subagent(meta: dict) -> bool:
    source = meta.get("source")
    return (isinstance(source, dict) and "subagent" in source) or meta.get("thread_source") == "guardian_review"


def source_of(meta: dict) -> str:
    """Which app wrote the rollout: ChatGPT's Work mode, Codex App, Codex CLI or a Codex IDE extension."""
    originator = str(meta.get("originator") or "").lower()
    if "work" in originator:
        return WORK
    if meta.get("source") in ("cli", "exec") or any(k in originator for k in ("tui", "cli", "exec")):
        return "Codex CLI"
    if any(k in originator for k in ("vscode", "jetbrains", "ide")):
        return "Codex IDE"
    return "Codex App"


def _load_titles(codex_home: Path) -> dict[str, str]:
    path = codex_home / "session_index.jsonl"
    if not path.exists():
        return {}
    records, _ = load_jsonl(path)
    return {r["id"]: r["thread_name"] for r in records if r.get("id") and r.get("thread_name")}


def split_header(text: str) -> tuple[str, str]:
    """The context header a message opens with ("" if none) and the rest of the message."""
    if not text.startswith(MOVED_PREFIXES):
        return "", text
    header, _, rest = text.partition("\n\n")
    return header, rest


def _load_claude_origin(codex_home: Path) -> dict[str, dict]:
    data = read_json(codex_home / "external_agent_session_imports.json")
    return {r["imported_thread_id"]: r
            for r in data.get("records", []) if isinstance(r, dict) and r.get("imported_thread_id")}


def _bare_title(title: str) -> str:
    """Title of a copy without what leftoff added to the session it was copied from: the header and the markers."""
    return TITLE_MARKERS.sub("", split_header(title)[1].lstrip())


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _session_files(codex_home: Path) -> list[Path]:
    return (sorted((codex_home / "sessions").glob("*/*/*/rollout-*.jsonl"))
            + sorted((codex_home / "archived_sessions").glob("rollout-*.jsonl")))


def _plain_path(path: str) -> str:
    r"""Drop a Windows `\\?\` long-path prefix: Claude Code names its project folders after the plain path."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def _read_session(path: Path, titles: dict, origin: dict) -> SessionInfo | None:
    records, _ = load_jsonl(path)
    meta = next((r.get("payload") for r in records if r.get("type") == "session_meta"), None)
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    sid = meta["id"]
    # The latest turn_context wins: a chat can be moved to another folder after it starts.
    turn_cwds = [r["payload"]["cwd"] for r in records if r.get("type") == "turn_context"
                 and isinstance(r.get("payload"), dict) and r["payload"].get("cwd")]
    user_texts = [i.text for i in extract_items(records) if i.role == "user"]
    copied = origin.get(sid) or {}
    # Codex imports Claude sessions as threads of its own, leftoff's included. Such a copy holds
    # nothing Claude doesn't have until a turn is run on it in Codex, and only that writes a turn_context.
    registered = sid in origin
    is_copy = registered or bool(user_texts and split_header(user_texts[0])[0])
    continued = is_copy and any(r.get("type") == "turn_context" for r in records)
    candidates = [titles.get(sid), copied.get("title"), user_texts[0] if user_texts else ""]
    if is_copy:
        candidates = [_bare_title(t) for t in candidates if t]
    title = next((t for t in candidates if t), "(untitled)")
    return SessionInfo(
        id=sid, path=path, cwd=_plain_path((turn_cwds[-1] if turn_cwds else meta.get("cwd")) or str(Path.home())),
        started=meta.get("timestamp") or "", updated=path.stat().st_mtime,
        title=one_line(title), user_turns=len(user_texts), from_claude=registered,
        is_chat=not is_subagent(meta) and bool(user_texts) and (continued or not is_copy), source=source_of(meta),
        # Only a continued copy may overwrite the session it came from, so only it gets to know which one.
        copied_from=Path(copied["source_path"]) if continued and copied.get("source_path") else None,
        copied_mtime=_int_or_zero(copied.get("source_modified_at")),
    )


def discover(codex_home: Path, include_all: bool = False) -> list[SessionInfo]:
    titles, origin = _load_titles(codex_home), _load_claude_origin(codex_home)
    sessions = [s for p in _session_files(codex_home) if (s := _read_session(p, titles, origin))]
    if not include_all:
        sessions = [s for s in sessions if s.is_chat]
    return sorted(sessions, key=lambda s: s.updated, reverse=True)


def find_session(codex_home: Path, query: str) -> SessionInfo:
    """Resolve an explicit id. Rollout filenames contain the id, so only matching files are parsed."""
    titles, origin = _load_titles(codex_home), _load_claude_origin(codex_home)
    files = [p for p in _session_files(codex_home) if query in p.name]
    return resolve_id([s for p in files if (s := _read_session(p, titles, origin))], query)


def resolve_id(sessions: list[SessionInfo], query: str) -> SessionInfo:
    for s in sessions:
        if s.id == query:
            return s
    if len(query) < 6:
        raise LookupError("Give the full id or at least 6 of its characters")
    matches = [s for s in sessions if query in s.id]
    if len(matches) > 1:
        matches = [s for s in matches if s.is_chat] or matches
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"No Codex session with an id containing {query}")
    lines = "\n".join(f"  {s.id}  {s.title}" for s in matches)
    raise LookupError(f"Ambiguous id {query}, matches:\n{lines}")


# ---------------------------------------------------------------- write


@dataclass
class ImportResult:
    session_id: str
    path: Path
    cwd: str
    turns: int
    kept_previous: bool


def project_slug(cwd: str) -> str:
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in cwd)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def render_records(turns: list[Turn], session_id: str, cwd: str, version: str, title: str,
                   source: str = "Codex App") -> list[dict]:
    records, parent = [], None
    for n, turn in enumerate(turns):
        rec_uuid = str(uuid.uuid5(NAMESPACE, f"{session_id}:{n}"))
        if turn.role == "user":
            content = [{"type": "text", "text": turn.text}, *turn.images] if turn.images else turn.text
            message = {"role": "user", "content": content}
        else:
            message = {"id": f"msg_codex_{n}", "type": "message", "role": "assistant",
                       "model": "<synthetic>", "content": [{"type": "text", "text": turn.text}],
                       "stop_reason": "end_turn", "stop_sequence": None,
                       "usage": {"input_tokens": 0, "output_tokens": 0}}
        records.append({"parentUuid": parent, "isSidechain": False, "type": turn.role,
                        "message": message, "uuid": rec_uuid, "timestamp": turn.ts or _now_iso(),
                        "userType": "external", "entrypoint": "cli", "cwd": cwd,
                        "sessionId": session_id, "version": version, "gitBranch": ""})
        parent = rec_uuid
    prefix = WORK_TITLE_PREFIX if source == WORK else TITLE_PREFIX
    records.append({"type": "custom-title", "customTitle": f"{prefix}{title}", "sessionId": session_id})
    return records


SOURCES_KEY = "__sources__"  # state entry: rollout path -> [mtime_ns, size] when last imported/synced


def _source_stamp(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_mtime_ns, st.st_size]


def _count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def _grown(path: Path, entry: dict) -> bool:
    """True if a session file has more lines than leftoff wrote: Claude has continued it."""
    return _count_lines(path) > entry.get("lines_written", 0)


def _session_owner(info: SessionInfo, state: dict, project: Path) -> str | None:
    """For a copy continued in Codex: the state key (a Codex id) of the Claude session it may overwrite.

    That is the session Codex copied it from: the copy holds all of it plus the new turns. Only
    a session leftoff wrote, in the copy's own project folder, that Claude hasn't grown since
    and, until the copy has taken it over, that still has the mtime Codex saw. A Claude chat
    leftoff didn't write is never overwritten."""
    source = info.copied_from
    if source is None or not _same_path(str(source.parent), str(project)):
        return None
    if (state.get(info.id) or {}).get("session_id") == source.stem:
        owner = info.id  # taken over at an earlier import; leftoff itself changed the mtime then
    else:
        # After a takeover the chat the session first came from still points at it, with
        # lines_written 0: that entry comes first here and turns a second copy away.
        owner = next((k for k, v in state.items() if k != SOURCES_KEY and isinstance(v, dict)
                      and v.get("session_id") == source.stem), None)
    try:
        if owner is None or _grown(source, state[owner]):
            return None
        if owner != info.id and source.stat().st_mtime_ns != info.copied_mtime:
            return None
    except OSError:
        return None
    return owner


def import_session(info: SessionInfo, claude_dir: Path, state_path: Path, version: str) -> ImportResult:
    cwd = info.cwd if os.path.isdir(info.cwd) else str(Path.home())
    records, _ = load_jsonl(info.path)
    items = extract_items(records)
    header = context_header(info.started[:10], info.cwd, info.source)
    first = next((i for i in items if i.role == "user"), None)
    own_header, rest = split_header(first.text) if first else ("", "")
    if own_header:
        # A copy of a session leftoff wrote: its own header goes back on top after the size cut.
        header, first.text = own_header, rest
        if not rest:
            items.remove(first)
    turns = build_turns(items, header)
    if not turns:
        raise ValueError("This chat has no messages to carry over")

    state = read_json(state_path)
    prev = state.get(info.id) or {}
    project = claude_dir / "projects" / project_slug(cwd)
    project.mkdir(parents=True, exist_ok=True)

    # A continued copy goes into the session it was copied from. Anything else takes the first
    # free session id, or the one written last time if Claude hasn't grown it since.
    k = 0
    owner = _session_owner(info, state, project)
    if owner:
        sid, path = info.copied_from.stem, info.copied_from
    else:
        while True:
            sid = str(uuid.uuid5(NAMESPACE, info.id if k == 0 else f"{info.id}:{k}"))
            path = project / f"{sid}.jsonl"
            if not path.exists():
                break
            if prev.get("session_id") == sid and not _grown(path, prev):
                break
            k += 1

    out = render_records(turns, sid, cwd, version, info.title, info.source)
    atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    if owner and owner != info.id:
        state[owner]["lines_written"] = 0  # the session is the copy's now: the chat it came from gets a new one
    state[info.id] = {"session_id": sid, "lines_written": len(out)}
    state.setdefault(SOURCES_KEY, {})[str(info.path)] = _source_stamp(info.path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=1))
    kept = k > 0 and prev.get("session_id") != sid
    return ImportResult(sid, path, cwd, len(turns), kept)


@dataclass
class SyncResult:
    imported: int
    unchanged: int


@contextlib.contextmanager
def sync_lock(state_path: Path):
    """Yield True if this process got the sync lock, False if another sync holds it."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path.with_name(state_path.name + ".lock"), "a") as f:
        try:
            if WINDOWS:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # BlockingIOError from flock, PermissionError from msvcrt
            yield False
            return
        try:
            yield True
        finally:
            if WINDOWS:  # flock goes away with the file; a Windows byte lock is released explicitly
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def sync(codex_home: Path, claude_dir: Path, state_path: Path, get_version) -> SyncResult:
    """Import every new or changed Codex chat. Rollouts whose mtime and size match the
    last sync are skipped without being opened, so a no-op sync costs one stat per file.
    Claude sessions started together (parallel chats) run one sync, the others return at once."""
    with sync_lock(state_path) as got:
        return _sync(codex_home, claude_dir, state_path, get_version) if got else SyncResult(0, 0)


def _sync(codex_home: Path, claude_dir: Path, state_path: Path, get_version) -> SyncResult:
    known = read_json(state_path).get(SOURCES_KEY, {})
    sources, imported, unchanged = {}, 0, 0
    titles = origin = version = None
    for path in _session_files(codex_home):
        stamp = _source_stamp(path)
        sources[str(path)] = stamp
        if known.get(str(path)) == stamp:
            unchanged += 1
            continue
        if titles is None:
            titles, origin = _load_titles(codex_home), _load_claude_origin(codex_home)
        info = _read_session(path, titles, origin)
        if info is None or not info.is_chat:
            continue
        version = version or get_version()
        import_session(info, claude_dir, state_path, version)
        imported += 1
    state = read_json(state_path)
    state[SOURCES_KEY] = sources
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=1))
    return SyncResult(imported, unchanged)


# Our hook's command, however the program path is quoted: `leftoff sync`, `'…/leftoff.cmd' sync`,
# `'…/leftoff.py' sync`; codex-resume is the name before the rename (replaced on the next `autosync on`).
AUTOSYNC_COMMAND = re.compile(r"""(?:leftoff|codex-resume)(?:\.py|\.cmd)?['"]? sync\b""")


def _load_settings(path: Path) -> dict:
    """Claude settings.json; refuses to proceed on a file it can't parse rather than overwrite it."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _is_autosync_entry(entry) -> bool:
    return isinstance(entry, dict) and any(
        isinstance(h, dict) and AUTOSYNC_COMMAND.search(str(h.get("command", "")))
        for h in entry.get("hooks") or [])


PLUGIN_ID = "leftoff@ostiums"  # plugin@marketplace, as /plugin installs it from this repo
OLD_PLUGIN_ID = "leftoff@leftoff"  # 0.2.0, when the marketplace was named after the plugin


def plugin_enabled(settings_path: Path) -> bool:
    """True if the leftoff Claude Code plugin is on; its own hook then does the syncing."""
    plugins = _load_settings(settings_path).get("enabledPlugins")
    return isinstance(plugins, dict) and any(plugins.get(i) is True for i in (PLUGIN_ID, OLD_PLUGIN_ID))


def autosync_enabled(settings_path: Path) -> bool:
    entries = _load_settings(settings_path).get("hooks", {}).get("SessionStart", [])
    return any(_is_autosync_entry(e) for e in entries)


def set_autosync(settings_path: Path, on: bool, command: str, shell: str | None = None) -> bool:
    """Add or remove the async SessionStart hook that runs `sync`. Returns True if settings changed."""
    data = _load_settings(settings_path)
    hooks = data.get("hooks", {})
    entries = hooks.get("SessionStart", [])
    others = [e for e in entries if not _is_autosync_entry(e)]
    hook = {"type": "command", "command": command, "async": True}
    if shell:
        hook["shell"] = shell
    ours = {"hooks": [hook]}
    if (on and entries == others + [ours]) or (not on and others == entries):
        return False
    if on:
        others.append(ours)
    if others:
        hooks["SessionStart"] = others
    else:
        hooks.pop("SessionStart", None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(settings_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


# ---------------------------------------------------------------- cli


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _state_path() -> Path:
    if os.environ.get("LEFTOFF_STATE"):
        return Path(os.environ["LEFTOFF_STATE"])
    path = Path.home() / ".local/state/leftoff/imports.json"
    legacy = Path.home() / ".local/state/codex-resume/imports.json"  # before the rename
    if legacy.exists() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(legacy, path)
        try:
            legacy.parent.rmdir()
        except OSError:
            pass
    return path


def _claude_version() -> str:
    try:
        claude = shutil.which("claude") or "claude"  # which() also finds the npm claude.cmd on Windows
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=15).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    return out[0] if out else "2.1.0"


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)  # case-insensitive on Windows


def _short_cwd(cwd: str) -> str:
    home = str(Path.home())
    under = _same_path(cwd, home) or os.path.normcase(cwd).startswith(os.path.normcase(home) + os.sep)
    return "~" + cwd[len(home):] if under else cwd


def _shell_path(path: str) -> str:
    """Shell-safe path that keeps a leading ~ unquoted so the shell still expands it."""
    short = _short_cwd(path)
    if short == "~":
        return "~"
    if short.startswith("~/"):
        return "~/" + shlex.quote(short[2:])
    return shlex.quote(path)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def resume_command(cwd: str, session_id: str) -> str:
    if WINDOWS:  # PowerShell; Windows PowerShell 5.1 has no `&&`
        return f"cd {_ps_quote(cwd)}; claude --resume {session_id}"
    return f"cd {_shell_path(cwd)} && claude --resume {session_id}"


def _dir_name(cwd: str) -> str:
    if _same_path(cwd, str(Path.home())):
        return "~"
    return Path(cwd).name or cwd


def rows(sessions: list[SessionInfo], color: bool = False) -> list[str]:
    """One line per chat: visible text, a tab, then the full id (hidden in fzf).

    The folder and source columns appear only when the chats differ in them."""
    def paint(text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color else text

    show_dir = len({s.cwd for s in sessions}) > 1
    show_source = len({s.source for s in sessions}) > 1
    dirs = [one_line(_dir_name(s.cwd), DIR_COLUMN_MAX) for s in sessions]
    width = max(map(len, dirs), default=0)
    source_width = max((len(s.source) for s in sessions), default=0)
    out = []
    for s, d in zip(sessions, dirs):
        cols = [paint(dt.datetime.fromtimestamp(s.updated).strftime("%Y-%m-%d %H:%M"), "2")]
        if show_dir:
            cols.append(d.ljust(width))
        if show_source:
            cols.append(paint(s.source.ljust(source_width), SOURCE_COLORS.get(s.source, "0")))
        cols.append(s.title + (paint(" ↩Claude", "2") if s.from_claude else ""))
        out.append("  ".join(cols) + "\t" + s.id)
    return out


def fzf_args(scope: str, preview_cmd: str) -> list[str]:
    return ["fzf", "--ansi", "--layout=reverse", "--delimiter", "\t", "--with-nth", "1", "--no-sort",
            "--header", f"leftoff{scope} · Enter: open in Claude · Space: preview · Esc: quit",
            "--preview", f"{preview_cmd} {{2}}", "--preview-window", "right,55%,wrap,hidden,<100(down,60%,wrap,hidden)",
            "--bind", "space:toggle-preview"]


def in_dir(sessions: list[SessionInfo], cwd: str) -> list[SessionInfo]:
    here = os.path.realpath(cwd)
    return [s for s in sessions if _same_path(os.path.realpath(s.cwd), here)]


def _chats(global_: bool) -> list[SessionInfo]:
    """Chats to offer: all of them, or only those started in the current directory."""
    sessions = discover(_codex_home())
    return sessions if global_ else in_dir(sessions, os.getcwd())


def _empty_hint(global_: bool) -> str:
    if global_:
        return "No ChatGPT Work or Codex chats"
    return f"No ChatGPT Work or Codex chats in this folder ({_short_cwd(os.getcwd())}). All chats: leftoff global"


def preview_command(python: str, script: str) -> str:
    if WINDOWS:  # fzf runs it with cmd.exe, or with bash under Git Bash: double quotes and / suit both
        return " ".join('"' + p.replace("\\", "/") + '"' for p in (python, script)) + " preview"
    return f"{shlex.quote(python)} {shlex.quote(script)} preview"


def pick(sessions: list[SessionInfo], scope: str) -> SessionInfo | None:
    if shutil.which("fzf"):
        preview_cmd = preview_command(sys.executable, os.path.realpath(__file__))
        proc = subprocess.run(fzf_args(scope, preview_cmd), input="\n".join(rows(sessions, color=True)),
                              stdout=subprocess.PIPE, text=True, encoding="utf-8")
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        chosen_id = proc.stdout.strip().split("\t")[-1]
        return next(s for s in sessions if s.id == chosen_id)
    shown = sessions
    while True:
        for n, line in enumerate(rows(shown), 1):
            print(f"{n:>3}. " + line.split("\t")[0])
        try:
            answer = input("Number or filter text (empty to quit): ").strip()
        except EOFError:
            return None
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1]
        query = answer.lower()
        found = [s for s in sessions if query in f"{s.title} {s.cwd}".lower()]
        if found:
            shown = found
        else:
            print("Nothing found")


def _preview(s: SessionInfo) -> None:
    records, _ = load_jsonl(s.path)
    print(f"{s.title}\n{s.source} · {_short_cwd(s.cwd)} · {s.started[:10]} · {s.user_turns} messages\n")
    for turn in build_turns(extract_items(records))[:PREVIEW_TURNS]:
        print(f"── {turn.role} ──\n{truncate(turn.text, PREVIEW_CHARS)}\n")


def _do_import(s: SessionInfo) -> ImportResult:
    res = import_session(s, _claude_dir(), _state_path(), _claude_version())
    if res.cwd != s.cwd:
        print(f"⚠ Folder {s.cwd} no longer exists — session created in {res.cwd}", file=sys.stderr)
    if res.kept_previous:
        print("ℹ This chat was already continued in Claude — that session is kept, a new one was created", file=sys.stderr)
    print(f"Imported: {s.title} ({res.turns} turns)")
    print(f"Claude session: {res.session_id}")
    print(f"File: {_short_cwd(str(res.path))}")
    print(f"Continue: {resume_command(res.cwd, res.session_id)}")
    return res


def open_in_claude(cwd: str, session_id: str) -> int:
    os.chdir(cwd)
    if not WINDOWS:
        os.execvp("claude", ["claude", "--resume", session_id])
    # Windows has no real exec: os.execvp starts claude and exits, and the shell then reads the
    # same console as claude. So wait for it instead, and leave Ctrl+C to claude.
    claude = shutil.which("claude")
    if claude is None:
        print("claude not found in PATH", file=sys.stderr)
        return 127
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    return subprocess.run([claude, "--resume", session_id]).returncode


def _hook_command() -> str:
    if WINDOWS:
        shim = Path.home() / ".local/bin/leftoff.cmd"
        parts = [shim] if shim.exists() else [Path(sys.executable), Path(os.path.realpath(__file__))]
        return "& " + " ".join(_ps_quote(str(p)) for p in parts) + " sync --quiet"
    link = Path.home() / ".local/bin/leftoff"
    exe = link if link.exists() else Path(os.path.realpath(__file__))
    return f"{shlex.quote(str(exe))} sync --quiet"


def _installer(repo: Path) -> list[str]:
    if WINDOWS:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")]
    return [str(repo / "install.sh")]


def _color_ok(stream) -> bool:
    """ANSI colors on a terminal; on Windows only once the console accepts escape sequences."""
    if not stream.isatty():
        return False
    if not WINDOWS:
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle, mode = kernel32.GetStdHandle(-11), ctypes.c_uint32()  # STD_OUTPUT_HANDLE
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel32.SetConsoleMode(handle, mode.value | 0x4))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return False


def update(repo: Path) -> int:
    """git pull the tool's own checkout and re-run its installer."""
    plugins = Path(os.path.realpath(_claude_dir() / "plugins"))
    if plugins == repo or plugins in repo.parents:
        print(f"This copy of leftoff belongs to the Claude Code plugin: update it in /plugin "
              f"or with `claude plugin update {PLUGIN_ID}`", file=sys.stderr)
        return 1
    if not (repo / ".git").exists():
        print(f"{repo} is not a git repository, can't update via git", file=sys.stderr)
        return 1
    if subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "-q"]).returncode != 0:
        print("git pull failed", file=sys.stderr)
        return 1
    return subprocess.run(_installer(repo)).returncode


def main(argv: list[str] | None = None) -> int:
    def add_global_flag(p: argparse.ArgumentParser, default) -> argparse.ArgumentParser:
        p.add_argument("-g", "--global", dest="global_", action="store_true", default=default,
                       help="chats from all folders (same as leftoff global)")
        return p

    # -g works before or after the subcommand; SUPPRESS keeps a subparser from resetting it.
    parser = add_global_flag(argparse.ArgumentParser(
        prog="leftoff", description="Pick up ChatGPT Work and Codex chats where you left off, in Claude Code"), False)
    sub = parser.add_subparsers(dest="cmd")
    p_list = add_global_flag(sub.add_parser("list", help="list ChatGPT Work and Codex chats"), argparse.SUPPRESS)
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--all", action="store_true", help="include internal sessions")
    sub.add_parser("import", help="convert a chat").add_argument("id")
    add_global_flag(sub.add_parser("resume", help="convert and open in claude"),
                    argparse.SUPPRESS).add_argument("id", nargs="?")
    sub.add_parser("global", help="pick a chat from all folders and open in claude").add_argument("id", nargs="?")
    sub.add_parser("preview", help="show the beginning of a chat").add_argument("id")
    sub.add_parser("update", help="update leftoff (git pull + the installer)")
    sub.add_parser("autosync", help="sync chats in the background at every Claude start").add_argument(
        "state", nargs="?", choices=["on", "off", "status"], default="status")
    sub.add_parser("sync", help="import all new and changed chats (for /resume)").add_argument(
        "--quiet", action="store_true")
    args = parser.parse_args(argv)
    if WINDOWS:  # through a pipe (fzf preview, /leftoff) Python would write the ANSI code page
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        if args.cmd == "list":
            sessions = discover(_codex_home(), include_all=True) if args.all else _chats(args.global_)
            if not sessions and not args.json:
                print(_empty_hint(args.global_), file=sys.stderr)
            if args.json:
                print(json.dumps([{"id": s.id, "title": s.title, "cwd": s.cwd,
                                   "updated": dt.datetime.fromtimestamp(s.updated).isoformat(timespec="minutes"),
                                   "user_turns": s.user_turns, "from_claude": s.from_claude,
                                   "source": s.source}
                                  for s in sessions], ensure_ascii=False, indent=1))
            else:
                for line in rows(sessions, color=_color_ok(sys.stdout)):
                    print(line)
            return 0
        if args.cmd == "sync":
            res = sync(_codex_home(), _claude_dir(), _state_path(), _claude_version)
            if not args.quiet:
                print(f"Imported: {res.imported}, unchanged: {res.unchanged}")
            return 0
        if args.cmd == "autosync":
            settings = _claude_dir() / "settings.json"
            plugin = plugin_enabled(settings)
            if args.state != "status":
                # On Windows hooks otherwise run in Git Bash, which may not be installed.
                shell = "powershell" if WINDOWS else None
                # With the plugin on, a hook in settings.json would only run a second sync.
                set_autosync(settings, args.state == "on" and not plugin, _hook_command(), shell)
                if args.state == "on":
                    res = sync(_codex_home(), _claude_dir(), _state_path(), _claude_version)
                    print(f"First sync: imported {res.imported}")
            if plugin:
                print("Autosync is on: the leftoff plugin syncs at every Claude start (turn it off in /plugin)")
                if autosync_enabled(settings):
                    print(f"The installer's hook is still in {settings} and runs a second sync: "
                          "`leftoff autosync on` removes it")
            else:
                print(f"Autosync is {'on' if autosync_enabled(settings) else 'off'} ({settings})")
            return 0
        if args.cmd == "update":
            return update(Path(os.path.realpath(__file__)).parent)
        if args.cmd == "preview":
            _preview(find_session(_codex_home(), args.id))
            return 0
        if args.cmd == "import":
            _do_import(find_session(_codex_home(), args.id))
            return 0
        # resume (default) / global
        chosen_id = getattr(args, "id", None)
        if chosen_id:
            chosen = find_session(_codex_home(), chosen_id)
        else:
            global_ = args.cmd == "global" or args.global_
            sessions = _chats(global_)
            if not sessions:
                print(_empty_hint(global_), file=sys.stderr)
                return 1
            chosen = pick(sessions, " · all folders" if global_ else f" · {_dir_name(os.getcwd())}")
        if chosen is None:
            return 130
        res = _do_import(chosen)
        return open_in_claude(res.cwd, res.session_id)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
