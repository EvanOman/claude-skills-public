"""Shared helpers for the Codex session-history tools (cx-*).

Data sources (all under $CODEX_HOME, default ~/.codex):
- sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl   — session transcripts
- archived_sessions/YYYY/MM/DD/rollout-*.jsonl    — archived transcripts
- history.jsonl                                   — {session_id, ts, text} per prompt

Never reads auth.json or any other credential file. Text destined for the
index or the terminal passes through redact() so secret-shaped strings
(API keys, tokens) are masked deterministically.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SESSION_DIR_NAMES = ("sessions", "archived_sessions")

# uuid at the end of a rollout filename stem
_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)

# Secret-shaped strings masked before indexing or display. Conservative,
# high-precision patterns only.
_REDACT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                     # OpenAI / Anthropic style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),            # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),          # GitHub fine-grained PATs
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),          # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key IDs
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWTs
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"),   # Authorization headers
]

# Injected (non-user-authored) plain user messages to skip when falling back
# to response_item user text.
_INJECTED_PREFIXES = ("<", "# AGENTS.md instructions", "Caveat:")
_SYSTEM_HISTORY_PROMPTS = {"/rate-limit-options"}


def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def history_file():
    return codex_home() / "history.jsonl"


def db_path():
    override = os.environ.get("CX_SESSIONS_DB")
    if override:
        return Path(override)
    return codex_home() / "usage-data" / "sessions.db"


def session_roots():
    """Existing session directories, live before archived."""
    return [d for name in SESSION_DIR_NAMES if (d := codex_home() / name).is_dir()]


def iter_session_files():
    """Yield rollout JSONL paths across live and archived sessions.

    Deterministic order: live sessions dir first, then archived, each sorted
    by path.
    """
    for root in session_roots():
        yield from sorted(root.rglob("rollout-*.jsonl"))


def session_id_from_path(path):
    m = _UUID_RE.search(Path(path).stem)
    return m.group(1) if m else Path(path).stem


def redact(text):
    """Mask secret-shaped substrings. Deterministic, idempotent."""
    if not text:
        return text
    for pat in _REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def is_history_noise(text):
    """Return True for known non-user history entries."""
    stripped = (text or "").strip()
    return not stripped or stripped in _SYSTEM_HISTORY_PROMPTS


def _content_text(content):
    """Concatenate the text parts of a response_item message content list."""
    parts = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"):
                t = c.get("text", "")
                if t:
                    parts.append(t)
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(parts)


def _is_injected(text):
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _INJECTED_PREFIXES)


def parse_session(path, meta_only=False):
    """Parse a rollout file into a metadata dict.

    Returns dict with: session_id, timestamp, cwd, git_branch, model,
    originator, cli_version, source_kind, first_prompt, prompts,
    message_count, user_count, assistant_count, total_tokens, path.

    With meta_only=True only the first line is read (cheap listing path);
    prompt/message fields stay empty.
    """
    info = {
        "session_id": session_id_from_path(path),
        "timestamp": "",
        "cwd": "",
        "git_branch": "",
        "model": "",
        "originator": "",
        "cli_version": "",
        "source_kind": "interactive",
        "first_prompt": "",
        "prompts": [],
        "message_count": 0,
        "user_count": 0,
        "assistant_count": 0,
        "total_tokens": 0,
        "path": str(path),
    }

    event_prompts = []     # from event_msg/user_message (authoritative)
    fallback_prompts = []  # plain response_item user text (subagent sessions)
    assistant_count = 0
    meta_seen = False

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = entry.get("type")
                payload = entry.get("payload")

                if rec_type == "session_meta" and isinstance(payload, dict):
                    # Forked/branched sessions replay the *parent* session's
                    # meta later in the file; only the first meta record (which
                    # matches the filename uuid) is this session's identity.
                    if meta_seen:
                        continue
                    meta_seen = True
                    info["session_id"] = payload.get("id") or info["session_id"]
                    info["timestamp"] = payload.get("timestamp") or ""
                    info["cwd"] = payload.get("cwd") or ""
                    info["originator"] = payload.get("originator") or ""
                    info["cli_version"] = payload.get("cli_version") or ""
                    git = payload.get("git")
                    if isinstance(git, dict):
                        info["git_branch"] = git.get("branch") or ""
                    source = payload.get("source")
                    if isinstance(source, dict) and "subagent" in source:
                        info["source_kind"] = "subagent"
                    if meta_only:
                        break
                    continue

                if rec_type is None and "instructions" in entry and "id" in entry:
                    # Pre-2026 rollout format: bare meta line, no envelope.
                    info["session_id"] = entry.get("id") or info["session_id"]
                    info["timestamp"] = entry.get("timestamp") or ""
                    if meta_only:
                        break
                    continue

                if meta_only:
                    break

                if not isinstance(payload, dict):
                    continue
                ptype = payload.get("type")

                if rec_type == "turn_context":
                    info["model"] = payload.get("model") or info["model"]
                elif rec_type == "event_msg":
                    if ptype == "user_message":
                        msg = payload.get("message") or ""
                        if not is_history_noise(msg):
                            event_prompts.append(msg)
                    elif ptype == "token_count":
                        total = (
                            (payload.get("info") or {})
                            .get("total_token_usage", {})
                            .get("total_tokens")
                        )
                        if isinstance(total, (int, float)):
                            info["total_tokens"] = int(total)
                elif rec_type == "response_item" and ptype == "message":
                    role = payload.get("role")
                    text = _content_text(payload.get("content"))
                    if role == "assistant" and text.strip():
                        assistant_count += 1
                    elif role == "user" and text.strip() and not _is_injected(text):
                        fallback_prompts.append(text)
    except OSError:
        pass

    prompts = event_prompts if event_prompts else fallback_prompts
    prompts = [redact(p) for p in prompts]
    info["prompts"] = prompts
    info["first_prompt"] = prompts[0] if prompts else ""
    info["user_count"] = len(prompts)
    info["assistant_count"] = assistant_count
    info["message_count"] = len(prompts) + assistant_count
    return info


def load_messages(path, include_tools=False):
    """Ordered conversation messages for transcript rendering.

    Returns a list of {role, text, timestamp}. Roles: user, assistant, and
    (with include_tools) tool / tool_result. User messages come from
    event_msg/user_message records; when a session has none (subagent
    sessions), plain non-injected response_item user texts are used instead.
    All text is passed through redact().
    """
    raw = []  # (source_tag, role, text, timestamp)
    has_event_user = False

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp", "")
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                rec_type = entry.get("type")
                ptype = payload.get("type")

                if rec_type == "event_msg" and ptype == "user_message":
                    msg = payload.get("message") or ""
                    if not is_history_noise(msg):
                        has_event_user = True
                        raw.append(("event_user", "user", msg, ts))
                elif rec_type == "response_item" and ptype == "message":
                    role = payload.get("role")
                    text = _content_text(payload.get("content"))
                    if not text.strip():
                        continue
                    if role == "assistant":
                        raw.append(("assistant", "assistant", text, ts))
                    elif role == "user" and not _is_injected(text):
                        raw.append(("fallback_user", "user", text, ts))
                elif include_tools and rec_type == "response_item":
                    if ptype in ("custom_tool_call", "function_call"):
                        name = payload.get("name", "?")
                        args = payload.get("input", payload.get("arguments", ""))
                        if not isinstance(args, str):
                            args = json.dumps(args)
                        raw.append(("tool", "tool", f"[Tool: {name}] {args[:200]}", ts))
                    elif ptype in ("custom_tool_call_output", "function_call_output"):
                        out = payload.get("output", "")
                        if not isinstance(out, str):
                            out = json.dumps(out)
                        raw.append(("tool", "tool_result", f"[Tool Result: {out[:200]}]", ts))
    except OSError:
        pass

    messages = []
    for source, role, text, ts in raw:
        # With event user messages present, drop the duplicate response_item
        # echoes of the same prompts.
        if source == "fallback_user" and has_event_user:
            continue
        messages.append({"role": role, "text": redact(text), "timestamp": ts})
    return messages


def find_session_files(prefix):
    """All rollout files whose session uuid starts with prefix (case-insensitive)."""
    prefix = prefix.lower()
    return [p for p in iter_session_files()
            if session_id_from_path(p).lower().startswith(prefix)]


def parse_iso(ts):
    """Parse an ISO-8601 timestamp (Z or offset) to an aware datetime, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def format_dt(dt):
    """Human-friendly relative timestamp (same format as the cc-* tools)."""
    if dt is None:
        return "unknown"
    now = datetime.now(tz=timezone.utc)
    diff = now - dt
    if diff.days == 0:
        hours = diff.seconds // 3600
        if hours == 0:
            return f"{diff.seconds // 60}m ago"
        return f"{hours}h ago"
    elif diff.days == 1:
        return "yesterday"
    elif diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def display_path(path_str):
    home = str(Path.home())
    if path_str.startswith(home + "/") or path_str == home:
        return "~" + path_str[len(home):]
    return path_str
