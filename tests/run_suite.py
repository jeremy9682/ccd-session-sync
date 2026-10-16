#!/usr/bin/env python3
"""Run the synthetic acceptance suites without using any real Desktop data."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
BASE = "d48986b4ee27088e34d6f078dbd100a174b5267f"
PR_HEAD = "e9c9f6a7063f52182bf3d20e06bb6f804668b6c4"
REVIEWED = "f366ff54857878b114d11bb275366e28a49e1d80"
SOURCE_FILES = [
    ".gitignore", "LICENSE", "README.md", "install.sh", "uninstall.sh",
    "bin/ccd-migrate", "bin/ccd-migrate-auto", "bin/ccd-safety.py",
    "bin/claude-archive-sync", "bin/claude-second",
    "launchagents/com.ccd-session-sync.migrate-auto.plist.template",
    "launchagents/com.ccd-session-sync.projects-archive.plist.template",
]


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="New, nonexistent directory for disposable fixtures and evidence")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("This acceptance suite requires macOS; installers check uname and use BSD tools.")
    if sys.version_info < (3, 9):
        parser.error("Python 3.9+ is required.")
    executables = {name: shutil.which(name) for name in ("git", "python3", "rsync")}
    if not all(executables.values()):
        parser.error("Required executables: git, python3 and rsync (nothing is installed automatically).")
    if args.output:
        output = args.output.expanduser().absolute()
        if output.exists() or output.is_symlink():
            parser.error("--output must not exist; existing directories are never reused or removed.")
        output.mkdir(parents=True)
        output = output.resolve()
    else:
        output = Path(tempfile.mkdtemp(prefix="ccd-synthetic-")).resolve()
    (output / ".ccd-synthetic-fixture").write_text("Disposable synthetic data only.\n")
    for name in ("home", "tmp", "implementation-evidence"):
        (output / name).mkdir()
    env = {
        "HOME": str(output / "home"), "TMPDIR": str(output / "tmp"),
        "PATH": ":".join(dict.fromkeys(
            [str(Path(p).parent) for p in [sys.executable, *executables.values()]] +
            ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])),
        "LC_ALL": "C", "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1", "CCD_TEST_ROOT": str(output),
    }

    def git(*argv, cwd=REPO):
        command_env = dict(env)
        if cwd != REPO:
            # A chosen output directory may itself be inside a Git checkout.
            # Fixture patching must never discover or use that parent repository.
            command_env["GIT_CEILING_DIRECTORIES"] = str(output)
        return subprocess.run([executables["git"], *argv], cwd=cwd, env=command_env,
                              check=True, capture_output=True).stdout

    def snapshot(ref, destination):
        # Export only the public tool files, never .git, logs, or user data.
        existing = git("ls-tree", "-r", "--name-only", ref).decode().splitlines()
        selected = [name for name in SOURCE_FILES if name in existing]
        archive = git("archive", "--format=tar", ref, "--", *selected)
        destination.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for entry in tar:
                if entry.isdir():
                    continue
                if entry.name not in SOURCE_FILES or not entry.isfile():
                    raise RuntimeError("Unexpected archive entry: " + entry.name)
                target = destination / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(entry).read())
                target.chmod(entry.mode & 0o777)

    print("Disposable fixtures and evidence:", output, flush=True)
    try:
        for name, ref in (("base", BASE), ("head", PR_HEAD), ("pre-review-fixes", REVIEWED)):
            snapshot(ref, output / name)
        # Reconstruct the deliberately vulnerable intermediate code solely for
        # before/after controls. Never install it outside its synthetic HOME.
        git("apply", "--check", str(REPO / "tests/fixtures/pre-review.patch"),
            cwd=output / "pre-review-fixes")
        git("apply", str(REPO / "tests/fixtures/pre-review.patch"),
            cwd=output / "pre-review-fixes")
        candidate = output / "candidate"
        candidate.mkdir()
        for name in SOURCE_FILES:
            source, target = REPO / name, candidate / name
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("Expected regular source file: " + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        sources = {name: hashes(output / name)
                   for name in ("base", "head", "candidate", "pre-review-fixes")}
        suites = [
            ("adversarial.py", "latest-all.txt", "manifest.json", 63),
            ("independent_edges.py", "latest-independent.txt", "unittest-results.json", 14),
            ("review_regressions.py", "latest-review-regressions.txt", "review-regressions.json", 13),
        ]
        summaries = []
        for script, pointer, result_file, expected in suites:
            command = [sys.executable, str(REPO / "tests" / script)]
            print("Running", script, flush=True)
            process = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True)
            (output / (script + ".stdout.txt")).write_text(process.stdout)
            (output / (script + ".stderr.txt")).write_text(process.stderr)
            summary = {"suite": script, "exit_code": process.returncode, "expected_groups": expected}
            pointer_path = output / "implementation-evidence" / pointer
            if pointer_path.exists():
                run = Path(pointer_path.read_text().strip()).resolve()
                if not run.is_relative_to(output / "runs"):
                    raise RuntimeError("Result pointer outside disposable run directory")
                result = json.loads((run / result_file).read_text())
                if script == "adversarial.py":
                    count, failed = sum(result["counts"].values()), result["counts"]["FAIL"]
                else:
                    count, failed = result["tests"], result["failures"] + result["errors"]
                matches = result["candidate_sha256"] == sources["candidate"]
                summary.update(groups=count, failed=failed, source_hashes_match=matches,
                               evidence=str((run / result_file).relative_to(output)))
                summary["status"] = "PASS" if process.returncode == 0 and count == expected and not failed and matches else "FAIL"
            else:
                summary["status"] = "FAIL"
            summaries.append(summary)
            print(json.dumps(summary), flush=True)
        report = {
            "refs": {"base": BASE, "original_pr": PR_HEAD, "reviewed_repair": REVIEWED,
                     "checkout_head": git("rev-parse", "HEAD").decode().strip()},
            "platform": platform.platform(), "python": sys.version.split()[0],
            "git": git("--version").decode().strip(), "source_sha256": sources,
            "test_sha256": hashes(REPO / "tests"), "suites": summaries,
            "status": "PASS" if all(s["status"] == "PASS" for s in summaries) else "FAIL",
        }
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(report["status"], "— summary:", output / "summary.json", flush=True)
        return 0 if report["status"] == "PASS" else 1
    except (OSError, subprocess.CalledProcessError, ValueError, RuntimeError) as error:
        print("FAIL:", error, file=sys.stderr)
        print("Sources must include the pinned history; use a full clone (no network fetch is performed).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
