#!/usr/bin/env python3
"""Disposable, no-network regression evidence for pinned base vs PR head.

Only child processes get a synthetic HOME. Every CCD data path is overridden.
No real desktop process, launchctl, uninstall, second instance, or real data used.
Shared fixtures; invoke tests/run_suite.py to prepare the source snapshots.
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time

if not os.environ.get("CCD_TEST_ROOT"):
    raise SystemExit("Run python3 tests/run_suite.py; this helper needs disposable source snapshots.")
ROOT = Path(os.environ["CCD_TEST_ROOT"]).resolve()
if not (ROOT / ".ccd-synthetic-fixture").is_file():
    raise SystemExit("Missing disposable-fixture marker; refusing to run.")
RUN = ROOT / "runs" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
RUN.mkdir(parents=True)
LOG = RUN / "commands.jsonl"
RESULTS = []
GIT = shutil.which("git")
PYTHON = shutil.which("python3")
A = "aaaaaaaa-1111-1111-1111-111111111111"
B = "bbbbbbbb-2222-2222-2222-222222222222"
C = "cccccccc-3333-3333-3333-333333333333"
ORG = "org-synthetic"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def digest_tree(p):
    return {str(f.relative_to(p)): [sha(f), f.stat().st_mtime_ns]
            for f in sorted(p.rglob("*")) if f.is_file()}


def write(p, value, mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value) if not isinstance(value, str) else value)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


class Fixture:
    def __init__(self, version, case):
        self.version = version
        self.name = case
        self.dir = RUN / version / case
        self.home = self.dir / "home"
        self.wt = self.home / "Library/Application Support/Claude"
        self.gd = self.home / ".ccd-sessions-git"
        self.src = ROOT / version
        for acct in (A, B, C):
            self.org(acct).mkdir(parents=True)
        (self.home / "tmp").mkdir()
        self.env = {
            "HOME": str(self.home), "TMPDIR": str(self.home / "tmp"),
            "PATH": ":".join(dict.fromkeys([str(Path(PYTHON).parent), str(Path(GIT).parent),
                                           "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])),
            "LC_ALL": "C", "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "/dev/null",
            "CCD_CLAUDE_DIR": str(self.wt), "CCD_GIT_DIR": str(self.gd),
            "CCD_FROM": A[:8], "CCD_TO": B[:8], "CCD_BIN": str(self.home / "bin"),
            "CCD_ARCHIVE": str(self.home / "ClaudeArchive"),
            "CCD_ARCHIVE_DIR": str(self.home / "ClaudeArchive/claude-projects"),
            "CCD_PROJECTS_DIR": str(self.home / ".claude/projects"),
            "CCD_LOG_DIR": str(self.home / "ClaudeArchive"),
            "CCD_SECOND_DATADIR": str(self.home / "Claude-Second"),
        }
        write(self.wt / "cowork-enabled-cli-ops.json", {"ownerAccountId": B})
        write(self.org(C) / "local_unrelated.json", {"title": "third-account sentinel"}, 1000)
        write(self.wt / "unrelated/preferences.json", {"synthetic": True}, 1000)
        write(self.home / ".claude/projects/test/transcript.jsonl", '{"synthetic":"transcript sentinel"}\n', 1000)
        write(self.home / "ClaudeArchive/claude-projects/kept.jsonl", "archive sentinel\n", 1000)
        self.sentinels = {str(p): [sha(p), p.stat().st_mtime_ns] for p in [
            self.org(C) / "local_unrelated.json", self.wt / "unrelated/preferences.json",
            self.home / ".claude/projects/test/transcript.jsonl",
            self.home / "ClaudeArchive/claude-projects/kept.jsonl"]}

    def org(self, acct):
        return self.wt / "claude-code-sessions" / acct / ORG

    def entry(self, acct, name, title, mtime=1000, cli=None):
        p = self.org(acct) / ("local_" + name + ".json")
        write(p, {"title": title, "cliSessionId": cli or "cli-" + name}, mtime)
        return p

    def run(self, argv, check=False, timeout=30, env=None):
        assert self.home.resolve().is_relative_to(RUN)
        assert not any(p.is_symlink() for p in self.home.rglob("*"))
        ts = time.monotonic()
        try:
            p = subprocess.run([str(a) for a in argv], cwd=self.dir, env=env or self.env,
                               text=True, capture_output=True, timeout=timeout)
            data = {"version": self.version, "case": self.name, "command": shlex.join(map(str, argv)),
                    "cwd": str(self.dir), "env": env or self.env, "exit_code": p.returncode,
                    "stdout": p.stdout, "stderr": p.stderr, "seconds": round(time.monotonic()-ts, 3)}
        except PermissionError as e:
            p = subprocess.CompletedProcess(argv, 126, "", str(e))
            data = {"version": self.version, "case": self.name, "command": shlex.join(map(str, argv)),
                    "exit_code": 126, "stdout": "", "stderr": str(e), "observation": "OS EACCES mapped to shell exit 126"}
        with LOG.open("a") as f:
            f.write(json.dumps(data) + "\n")
        if check and p.returncode:
            raise RuntimeError(f"{self.version}/{self.name}: rc={p.returncode} {argv}\n{p.stderr}")
        return p

    def install(self, direct=False):
        argv = [self.src / "install.sh", "--no-agents"]
        return self.run(argv if direct else ["/bin/bash", *argv])

    def migrate(self, *args):
        return self.run(["/bin/bash", self.src / "bin/ccd-migrate", *args])

    def git(self, *args, check=True):
        return self.run([GIT, "--git-dir=" + str(self.gd), "--work-tree=" + str(self.wt), *args], check=check)

    def commit(self, msg):
        self.git("add", "-A")
        return self.git("commit", "--allow-empty", "-q", "-m", msg)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def preserved(self):
        return all(Path(p).exists() and [sha(Path(p)), Path(p).stat().st_mtime_ns] == before
                   for p, before in self.sentinels.items())
