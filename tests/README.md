# Synthetic acceptance tests

From a full clone on macOS, with Git, Python 3.9+ and rsync already available:

```sh
python3 tests/run_suite.py
# Or retain everything in a chosen NEW directory:
python3 tests/run_suite.py --output /tmp/ccd-synthetic-review
```

The runner does not fetch, install dependencies, or require Claude Desktop. It
copies tool sources and creates synthetic account directories, a separate HOME,
Git registry, archive and transcript sentinels in disposable storage. Every child
tool receives synthetic paths. Agent-install tests use inert launchctl/plutil
stubs; no actual agent is loaded. Existing output directories are refused.
Inspect the printed `summary.json`, per-suite stdout/stderr and each run's command
log. They contain synthetic data and local temporary paths; do not publish logs
from real installations.

The candidate is copied from the current checkout (including local code edits).
Controls are exported from these pinned, public ancestors:

| Source | Commit | Purpose |
|---|---|---|
| Original main | `d48986b4ee27088e34d6f078dbd100a174b5267f` | Exit-141 reproduction |
| Contributor PR #1 | `e9c9f6a7063f52182bf3d20e06bb6f804668b6c4` | SIGPIPE fix and unresolved issue controls |
| Reviewed repair | `f366ff54857878b114d11bb275366e28a49e1d80` | Base for the intermediate regression fixture |

`fixtures/pre-review.patch` reconstructs an earlier, deliberately vulnerable
repair from the reviewed repair commit. It is **test data**, not an alternative
installation. Before/after checks verify reviewers' counterexamples rather than
assuming every old implementation should pass. Production code is never patched
by this runner. Fault injection and mutations change disposable copies only.

| Suite | Expected groups | Coverage |
|---|---:|---|
| `adversarial.py` | 63 | Core behavior, base/PR negative controls, 60 seeded state combinations, interruption and mutation observations |
| `independent_edges.py` | 14 | Separate unittest surface: account isolation, payload retention, history, Git configuration, installer and auto-entry boundaries |
| `review_regressions.py` | 13 | Review counterexamples including encodings, staging parity, literal paths, filters, reinstall and deletion aliases |

Counts are observation/test groups, not unique scenarios. The generated group
uses seed 20261016. Four mutation checks expose missing safeguards (two data
guards and two selection/diagnostic checks). A fifth observes that removing one
pending-operation guard remains safe because exclusive journal creation still
blocks it; it is explicitly **not** claimed as a killed integrity mutant. Suite
summaries bind results to SHA-256 hashes of the candidate files.

## Review scope

The repair retains Markus Kettunen's two commits from PR #1 in its ancestry.
PR #1 fixes exit 141 and executable modes. This follow-up adds target-local
deletion-marker handling, a clear stop for an already-reverted latest migration,
and conservative validation, staging and interruption guards. It does not step
back through older migrations or propagate deletion across accounts.

Local Codex and the existing isolated local Claude setup independently reviewed
the source, supplied counterexamples, and reconciled the final source hashes and
completed results. The coordinating agent implemented repairs and ran tests;
the reviewers did not independently execute these suites. Raw model transcripts
and local session data are excluded.

These tests establish bounded filesystem/Git behavior, **not** real Desktop UI
compatibility, concurrent Desktop/same-user race safety, power-loss durability or
complete transactional rollback. Archive execution, real launchd, notifications,
uninstall and purge are not exercised; the auto-entry check takes its same-owner
no-op branch. Undo retains payload directories, does not restore original mtimes,
and may prune empty account/org directories. Marker naming is an undocumented
Desktop convention. Keep real data backed up and Desktop quit before evaluating
the tool; see the main README's recovery guidance.
