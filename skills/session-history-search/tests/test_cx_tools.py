"""Tests for the Codex session-history tools (cx-sessions, cx-index, cx-search,
cx-transcript) and setup.sh.

All tests run against a synthetic CODEX_HOME built in a temp directory — they
never read or write the live ~/.codex or ~/.claude. Run with:

    python3 -m unittest discover -s skills/session-history-search/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = SKILL_DIR / "bin"
SETUP_SH = SKILL_DIR / "setup.sh"

UUID1 = "aaaa1111-1111-4111-8111-111111111111"  # interactive, alpha project
UUID2 = "aaaa2222-2222-4222-8222-222222222222"  # interactive, beta project, has secrets
UUID3 = "cccc3333-3333-4333-8333-333333333333"  # archived, alpha project
UUID4 = "dddd4444-4444-4444-8444-444444444444"  # forked session, replays UUID1's meta

SECRET_OPENAI = "sk-proj-ABC123DEF456GHI789JKL012MNO345PQR678"
SECRET_GITHUB = "ghp_0123456789abcdefghijklmnopqrstuv1234"


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def _envelope(ts, rec_type, payload):
    return json.dumps({"timestamp": _iso(ts), "type": rec_type, "payload": payload})


def _session_meta(ts, sid, cwd, branch="", source="user"):
    payload = {
        "id": sid,
        "timestamp": _iso(ts),
        "cwd": cwd,
        "originator": "codex-tui",
        "cli_version": "0.145.0",
        "source": source,
        "git": {"branch": branch} if branch else {},
    }
    return _envelope(ts, "session_meta", payload)


def _turn_context(ts, cwd, model="gpt-5.6-sol"):
    return _envelope(ts, "turn_context", {"cwd": cwd, "model": model, "effort": "high"})


def _user_event(ts, text):
    return _envelope(ts, "event_msg", {"type": "user_message", "message": text, "images": []})


def _user_response_item(ts, text):
    return _envelope(ts, "response_item", {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}],
    })


def _assistant_response_item(ts, text):
    return _envelope(ts, "response_item", {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    })


def _tool_call(ts, name, args):
    return _envelope(ts, "response_item", {
        "type": "custom_tool_call", "name": name, "input": args,
        "call_id": "call_1", "id": "ct_1", "status": "completed",
    })


def _tool_output(ts, output):
    return _envelope(ts, "response_item", {
        "type": "custom_tool_call_output", "call_id": "call_1", "output": output,
    })


def _token_count(ts, total):
    return _envelope(ts, "event_msg", {
        "type": "token_count",
        "info": {"total_token_usage": {
            "input_tokens": total - 100, "cached_input_tokens": 0,
            "cache_write_input_tokens": 0, "output_tokens": 100,
            "reasoning_output_tokens": 0, "total_tokens": total,
        }},
    })


def build_fixture(root):
    """Create a synthetic CODEX_HOME with 2 live sessions + 1 archived."""
    now = time.time()
    codex_home = root / "codex-home"

    # Session 1: interactive session in ~/dev/alpha, 1h ago.
    t1 = now - 3600
    s1_dir = codex_home / "sessions" / "2026" / "07" / "20"
    s1_dir.mkdir(parents=True)
    s1 = s1_dir / f"rollout-2026-07-20T10-00-00-{UUID1}.jsonl"
    s1.write_text("\n".join([
        _session_meta(t1, UUID1, "/home/evan/dev/alpha", branch="main"),
        _turn_context(t1, "/home/evan/dev/alpha"),
        _user_response_item(t1, "<environment_context>\n  <cwd>/home/evan/dev/alpha</cwd>\n</environment_context>"),
        _user_response_item(t1, "# AGENTS.md instructions\n\n<INSTRUCTIONS>stuff</INSTRUCTIONS>"),
        _user_event(t1 + 1, "Deploy the alpha family tree service"),
        _user_response_item(t1 + 1, "Deploy the alpha family tree service"),
        _tool_call(t1 + 2, "exec", {"cmd": "systemctl status alpha"}),
        _tool_output(t1 + 3, "active (running)"),
        _assistant_response_item(t1 + 4, "Deployed the family tree service successfully."),
        _token_count(t1 + 5, 1234),
    ]) + "\n")
    os.utime(s1, (t1 + 5, t1 + 5))

    # Session 2: interactive session in ~/dev/beta, 2h ago, two prompts + secrets.
    t2 = now - 7200
    s2_dir = codex_home / "sessions" / "2026" / "07" / "19"
    s2_dir.mkdir(parents=True)
    s2 = s2_dir / f"rollout-2026-07-19T09-00-00-{UUID2}.jsonl"
    s2.write_text("\n".join([
        _session_meta(t2, UUID2, "/home/evan/dev/beta", branch="fix-parser"),
        _turn_context(t2, "/home/evan/dev/beta"),
        _user_event(t2 + 1, "Fix the beta parser bug"),
        _assistant_response_item(t2 + 2, "Fixed the off-by-one in the parser."),
        _user_event(t2 + 3, f"add tests for tokenizer using key {SECRET_OPENAI} and token {SECRET_GITHUB}"),
        _assistant_response_item(t2 + 4, "Added tokenizer tests."),
        _token_count(t2 + 5, 999),
    ]) + "\n")
    os.utime(s2, (t2 + 5, t2 + 5))

    # Session 3: archived session in ~/dev/alpha, 30 days ago.
    t3 = now - 30 * 86400
    s3_dir = codex_home / "archived_sessions" / "2026" / "06" / "20"
    s3_dir.mkdir(parents=True)
    s3 = s3_dir / f"rollout-2026-06-20T08-00-00-{UUID3}.jsonl"
    s3.write_text("\n".join([
        _session_meta(t3, UUID3, "/home/evan/dev/alpha"),
        _user_event(t3 + 1, "archived gamma experiment notes"),
        _assistant_response_item(t3 + 2, "Recorded the gamma experiment."),
    ]) + "\n")
    os.utime(s3, (t3 + 2, t3 + 2))

    # Session 4: forked session, 3h ago. Codex replays the *parent* session's
    # session_meta later in the child's rollout file; the child's own identity
    # is the first meta record (which matches the filename uuid).
    t4 = now - 3 * 3600
    s4_dir = codex_home / "sessions" / "2026" / "07" / "19"
    s4 = s4_dir / f"rollout-2026-07-19T21-00-00-{UUID4}.jsonl"
    s4.write_text("\n".join([
        _session_meta(t4, UUID4, "/home/evan/dev/alpha", branch="main"),
        _user_event(t4 + 1, "continue the forked delta work"),
        _session_meta(t4 - 9999, UUID1, "/home/evan/dev/alpha", branch="main"),
        _assistant_response_item(t4 + 2, "Continuing the delta work."),
    ]) + "\n")
    os.utime(s4, (t4 + 2, t4 + 2))

    # history.jsonl mirrors the prompts (epoch-seconds ts).
    history = codex_home / "history.jsonl"
    history.write_text("\n".join([
        json.dumps({"session_id": UUID1, "ts": int(t1 + 1),
                    "text": "Deploy the alpha family tree service"}),
        json.dumps({"session_id": UUID2, "ts": int(t2 + 1),
                    "text": "Fix the beta parser bug"}),
        json.dumps({"session_id": UUID2, "ts": int(t2 + 3),
                    "text": f"add tests for tokenizer using key {SECRET_OPENAI} and token {SECRET_GITHUB}"}),
        json.dumps({"session_id": UUID1, "ts": int(t1 + 2),
                    "text": "see https://example.com/family-tree for details"}),
        json.dumps({"session_id": UUID1, "ts": int(t1 + 3),
                    "text": "/rate-limit-options"}),
        json.dumps({"session_id": UUID1, "ts": int(t1 + 4), "text": ""}),
    ]) + "\n")

    return codex_home


class CxToolsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cx-tools-test-"))
        cls.codex_home = build_fixture(cls.tmp)
        cls.db_path = cls.tmp / "state" / "sessions.db"
        cls.env = dict(os.environ)
        cls.env["CODEX_HOME"] = str(cls.codex_home)
        cls.env["CX_SESSIONS_DB"] = str(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_tool(cls, tool, *args, check=True):
        proc = subprocess.run(
            [sys.executable, str(BIN_DIR / tool), *args],
            capture_output=True, text=True, env=cls.env,
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"{tool} {args} failed rc={proc.returncode}\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc


class TestCxSessions(CxToolsBase):
    def test_lists_all_sessions_newest_first(self):
        out = self.run_tool("cx-sessions", "--count", "10").stdout
        self.assertIn(UUID1[:8], out)
        self.assertIn(UUID2[:8], out)
        self.assertIn(UUID3[:8], out)
        self.assertLess(out.index(UUID1[:8]), out.index(UUID2[:8]))
        self.assertLess(out.index(UUID2[:8]), out.index(UUID3[:8]))
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_first_prompt_shown_not_injected_context(self):
        out = self.run_tool("cx-sessions", "--count", "10").stdout
        self.assertIn("Deploy the alpha family tree service", out)
        self.assertNotIn("environment_context", out)
        self.assertNotIn("AGENTS.md instructions", out)

    def test_project_filter(self):
        out = self.run_tool("cx-sessions", "--project", "beta").stdout
        self.assertIn(UUID2[:8], out)
        self.assertNotIn(UUID1[:8], out)
        self.assertNotIn(UUID3[:8], out)

    def test_days_filter(self):
        out = self.run_tool("cx-sessions", "--days", "7").stdout
        self.assertIn(UUID1[:8], out)
        self.assertIn(UUID2[:8], out)
        self.assertNotIn(UUID3[:8], out)

    def test_count_limit(self):
        out = self.run_tool("cx-sessions", "--count", "1").stdout
        self.assertIn(UUID1[:8], out)
        self.assertNotIn(UUID2[:8], out)

    def test_json_output(self):
        out = self.run_tool("cx-sessions", "--count", "10", "--json").stdout
        data = json.loads(out)
        self.assertEqual(len(data), 4)
        first = data[0]
        for key in ("sessionId", "project", "firstPrompt", "messageCount"):
            self.assertIn(key, first)
        self.assertEqual(first["sessionId"], UUID1)

    def test_forked_session_keeps_own_identity(self):
        out = self.run_tool("cx-sessions", "--count", "10", "--json").stdout
        by_id = {s["sessionId"]: s for s in json.loads(out)}
        self.assertIn(UUID4, by_id)
        self.assertIn("forked delta", by_id[UUID4]["firstPrompt"])

    def test_long_shows_model_and_tokens(self):
        out = self.run_tool("cx-sessions", "--count", "10", "--long").stdout
        self.assertIn("gpt-5.6-sol", out)
        self.assertIn("1,234", out)

    def test_project_summary(self):
        out = self.run_tool("cx-sessions", "--project-summary").stdout
        self.assertIn("alpha", out)
        self.assertIn("beta", out)


class TestCxIndex(CxToolsBase):
    def test_1_build_index(self):
        out = self.run_tool("cx-index").stdout
        self.assertTrue(self.db_path.exists())
        self.assertIn("Indexed 4 new", out)

    def test_2_incremental_skips_unchanged(self):
        self.run_tool("cx-index")
        out = self.run_tool("cx-index").stdout
        self.assertIn("skipped 4", out)
        self.assertIn("Indexed 0 new", out)

    def test_2b_forked_session_indexed_under_own_id(self):
        self.run_tool("cx-index")
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        ids = {r[0] for r in conn.execute("SELECT session_id FROM sessions")}
        conn.close()
        self.assertEqual(ids, {UUID1, UUID2, UUID3, UUID4})

    def test_2c_second_run_after_full_skips_everything(self):
        self.run_tool("cx-index", "--full")
        out = self.run_tool("cx-index").stdout
        self.assertIn("Indexed 0 new", out)

    def test_3_secrets_not_in_db(self):
        self.run_tool("cx-index")
        raw = self.db_path.read_bytes()
        self.assertNotIn(SECRET_OPENAI.encode(), raw)
        self.assertNotIn(SECRET_GITHUB.encode(), raw)

    def test_4_stats(self):
        self.run_tool("cx-index")
        out = self.run_tool("cx-index", "--stats").stdout
        self.assertIn("3", out)

    def test_5_full_rebuild(self):
        self.run_tool("cx-index")
        out = self.run_tool("cx-index", "--full").stdout
        self.assertNotIn("skipped 3", out)


class TestCxSearch(CxToolsBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.run_tool("cx-index")

    def test_stemmed_and_search(self):
        # "trees" stems to "tree"; terms AND together across the session.
        out = self.run_tool("cx-search", "family trees").stdout
        self.assertIn(UUID1[:8], out)
        self.assertNotIn(UUID2[:8], out)

    def test_phrase_search(self):
        out = self.run_tool("cx-search", '"family tree"').stdout
        self.assertIn(UUID1[:8], out)

    def test_later_prompts_are_indexed(self):
        # "tokenizer" only appears in session 2's *second* prompt.
        out = self.run_tool("cx-search", "tokenizer").stdout
        self.assertIn(UUID2[:8], out)

    def test_project_filter(self):
        out = self.run_tool("cx-search", "parser", "--project", "alpha").stdout
        self.assertNotIn(UUID2[:8], out)

    def test_secrets_redacted_everywhere(self):
        for args in (["tokenizer"], ["tokenizer", "--literal"], ["--recent", "10"]):
            out = self.run_tool("cx-search", *args).stdout
            self.assertNotIn(SECRET_OPENAI, out, f"secret leaked via {args}")
            self.assertNotIn(SECRET_GITHUB, out, f"secret leaked via {args}")

    def test_literal_url_search(self):
        out = self.run_tool("cx-search", "https://example.com/family-tree", "--literal").stdout
        self.assertIn(UUID1[:8], out)

    def test_recent(self):
        out = self.run_tool("cx-search", "--recent", "2").stdout
        self.assertIn("Deploy the alpha family tree service", out)
        self.assertNotIn("rate-limit-options", out)

    def test_recent_honors_project_filter(self):
        out = self.run_tool("cx-search", "--recent", "10", "--project", "alpha").stdout
        self.assertIn(UUID1[:8], out)
        self.assertNotIn(UUID2[:8], out)

    def test_no_match(self):
        out = self.run_tool("cx-search", "zzzznonexistentterm").stdout
        self.assertIn("No matches", out)


class TestCxTranscript(CxToolsBase):
    def test_unique_prefix_renders(self):
        proc = self.run_tool("cx-transcript", UUID1[:8])
        self.assertIn("Deploy the alpha family tree service", proc.stdout)
        self.assertIn("Deployed the family tree service successfully.", proc.stdout)
        # Injected context is not part of the conversation.
        self.assertNotIn("environment_context", proc.stdout)
        self.assertNotIn("AGENTS.md", proc.stdout)

    def test_ambiguous_prefix_errors(self):
        proc = self.run_tool("cx-transcript", "aaaa", check=False)
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn(UUID1, combined)
        self.assertIn(UUID2, combined)

    def test_unknown_prefix_errors(self):
        proc = self.run_tool("cx-transcript", "ffffffff", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_archived_session_found(self):
        proc = self.run_tool("cx-transcript", UUID3[:8])
        self.assertIn("archived gamma experiment notes", proc.stdout)

    def test_summary(self):
        out = self.run_tool("cx-transcript", UUID2[:8], "--summary").stdout
        self.assertIn("Fix the beta parser bug", out)
        self.assertIn("2 user", out)

    def test_user_only(self):
        out = self.run_tool("cx-transcript", UUID2[:8], "--user-only").stdout
        self.assertIn("Fix the beta parser bug", out)
        self.assertNotIn("Fixed the off-by-one", out)

    def test_tail(self):
        out = self.run_tool("cx-transcript", UUID2[:8], "--tail", "1").stdout
        self.assertIn("Added tokenizer tests.", out)
        self.assertNotIn("Fix the beta parser bug", out)

    def test_tools_flag_shows_tool_calls(self):
        out = self.run_tool("cx-transcript", UUID1[:8], "--tools").stdout
        self.assertIn("exec", out)

    def test_transcript_redacts_secrets(self):
        out = self.run_tool("cx-transcript", UUID2[:8]).stdout
        self.assertNotIn(SECRET_OPENAI, out)
        self.assertNotIn(SECRET_GITHUB, out)

    def test_raw_json(self):
        out = self.run_tool("cx-transcript", UUID2[:8], "--raw").stdout
        json.loads(out)


class TestSetupScript(unittest.TestCase):
    ALL_TOOLS = [
        "cc-sessions", "cc-search", "cc-transcript", "cc-index",
        "cx-sessions", "cx-search", "cx-transcript", "cx-index",
    ]

    def test_dry_run_installs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            proc = subprocess.run(
                ["bash", str(SETUP_SH), "--dry-run", "--bin-dir", str(bin_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(bin_dir.exists())
            for tool in self.ALL_TOOLS:
                self.assertIn(tool, proc.stdout)

    def test_install_to_custom_bin_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            proc = subprocess.run(
                ["bash", str(SETUP_SH), "--bin-dir", str(bin_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for tool in self.ALL_TOOLS:
                self.assertTrue((bin_dir / tool).exists(), f"{tool} missing")
            # Shared module for the cx tools must be installed too.
            self.assertTrue((bin_dir / "cx_common.py").exists())

    def test_copy_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            proc = subprocess.run(
                ["bash", str(SETUP_SH), "--copy", "--bin-dir", str(bin_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for tool in self.ALL_TOOLS:
                dest = bin_dir / tool
                self.assertTrue(dest.exists(), f"{tool} missing")
                self.assertFalse(dest.is_symlink(), f"{tool} should be a copy")


if __name__ == "__main__":
    unittest.main()
