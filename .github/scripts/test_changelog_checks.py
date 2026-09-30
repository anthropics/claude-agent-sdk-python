"""Test only: runs the two CHANGELOG.md checks, taken verbatim from the real workflow files,
on good and bad files, and fails if either check lets a bad file through or stops a good one."""

import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

WF = pathlib.Path(".github/workflows")
gen = yaml.safe_load((WF / "generate-changelog.yml").read_text())
bap = yaml.safe_load((WF / "build-and-publish.yml").read_text())
GEN_CHECK = next(s for s in gen["jobs"]["generate"]["steps"] if s.get("id") == "check")["run"]
REL_COMMIT = next(s for s in bap["jobs"]["release"]["steps"] if s.get("name") == "Commit the changelog")["run"]
BASE = pathlib.Path("CHANGELOG.md").read_text()
GOOD = BASE.replace("# Changelog\n\n", "# Changelog\n\n## 0.2.163\n\n### Internal/Other Changes\n\n- Test entry\n\n", 1)


def bash(script, cwd, env=None):
    path = pathlib.Path(cwd) / ".step.sh"
    path.write_text(script)
    r = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
        cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
    )
    path.unlink()
    return r.returncode, (r.stdout + r.stderr).strip().replace("\n", " | ")


def make(where, kind, text):
    """Create CHANGELOG.md in directory `where` for a case."""
    f = pathlib.Path(where) / "CHANGELOG.md"
    if kind == "missing":
        return
    if kind == "symlink":
        f.symlink_to("/etc/hostname")
        return
    if kind == "binary":
        f.write_bytes(text.encode() + b"\x00tail\n")
        return
    f.write_text(text)
    if kind == "hardlink":
        os.link(f, pathlib.Path(where) / "second-name")


CASES = [
    # (name, kind, text, should_pass, the check's message when it should refuse)
    ("good entry", "file", GOOD, True, ""),
    ("words that are not credentials", "file", GOOD + "\n- Mentions eyJ, sk-ant, ghp_, pypi-token and BEGIN PUBLIC KEY\n", True, ""),
    ("missing", "missing", "", False, "not a plain file"),
    ("symlink", "symlink", "", False, "not a plain file"),
    ("hardlink", "hardlink", GOOD, False, "not a plain file"),
    ("over 1 MiB", "file", GOOD + "x" * (1024 * 1024), False, "larger than 1 MiB"),
    ("NUL byte", "binary", GOOD, False, "not a text file"),
    ("private key", "file", GOOD + "-----BEGIN OPENSSH PRIVATE KEY-----\n", False, "looks like a credential"),
    ("RSA private key", "file", GOOD + "-----BEGIN RSA PRIVATE KEY-----\n", False, "looks like a credential"),
    ("JWT (OIDC token)", "file", GOOD + "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJyZXBvOnRlc3QifQ.sig\n", False, "looks like a credential"),
    ("Anthropic key", "file", GOOD + "sk-ant-api03-" + "A" * 24 + "\n", False, "looks like a credential"),
    ("GitHub token ghp_", "file", GOOD + "ghp_" + "a" * 36 + "\n", False, "looks like a credential"),
    ("GitHub token ghs_", "file", GOOD + "ghs_" + "a" * 36 + "\n", False, "looks like a credential"),
    ("GitHub PAT", "file", GOOD + "github_pat_" + "a" * 40 + "\n", False, "looks like a credential"),
    ("PyPI token", "file", GOOD + "pypi-" + "A" * 40 + "\n", False, "looks like a credential"),
]

failures = []
for name, kind, text, should_pass, reason in CASES:
    # Generate-side check: runs in the checkout on ./CHANGELOG.md.
    with tempfile.TemporaryDirectory() as d:
        make(d, kind, text)
        rc, out = bash(GEN_CHECK, d)
    gen_ok = rc == 0 if should_pass else (rc != 0 and reason in out)
    # Release-side step: checks $RUNNER_TEMP/changelog/CHANGELOG.md, then commits it.
    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as tmp:
        g = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        (pathlib.Path(repo) / "CHANGELOG.md").write_text(BASE)
        g("add", "CHANGELOG.md")
        g("commit", "-q", "-m", "base")
        (pathlib.Path(tmp) / "changelog").mkdir()
        make(pathlib.Path(tmp) / "changelog", kind, text)
        rrc, rout = bash(REL_COMMIT, repo, {"RUNNER_TEMP": tmp, "VERSION": "0.2.163"})
        subject = g("log", "-1", "--format=%s")
        files = g("show", "--name-only", "--format=", "HEAD")
        committed_text = (pathlib.Path(repo) / "CHANGELOG.md").read_text(errors="replace")
    if should_pass:
        rel_ok = rrc == 0 and subject == "docs: update changelog for v0.2.163" and files == "CHANGELOG.md" and committed_text == text
    else:
        rel_ok = rrc != 0 and reason in rout and subject == "base" and committed_text == BASE
    status = "ok  " if gen_ok and rel_ok else "FAIL"
    print(f"{status} {name:34} generate-check exit {rc}, release-step exit {rrc}, last commit '{subject}'")
    if not (gen_ok and rel_ok):
        failures.append(f"{name}: generate exit {rc} ({out[:200]}); release exit {rrc} ({rout[:200]})")

summary = f"{len(CASES) - len(failures)}/{len(CASES)} cases behaved as expected"
if failures:
    print(f"::error title=check scripts::{summary}. " + " || ".join(failures))
    sys.exit(1)
print(f"::notice title=check scripts::{summary} (both the generate-side check and the release-side step).")
