"""Run the release workflow's own steps with a stand-in CLI and a local upload server.

Reads the steps out of build-and-publish.yml, so what runs is what a release runs.
Nothing is sent to PyPI: the upload hosts point at a local TLS server for that step.
"""
import http.server, json, os, shutil, ssl, subprocess, sys, tempfile, threading
from pathlib import Path
import yaml

REPO = Path.cwd()
WORK = Path(tempfile.mkdtemp(prefix="dry-run-"))
VERSION = "9.9.9"
TAGS = ["macosx_11_0_arm64", "macosx_11_0_x86_64", "manylinux_2_17_x86_64", "manylinux_2_17_aarch64", "win_amd64"]
HOSTS = ["upload.pypi.org", "pypi.python.org"]
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"
results: list[str] = []


def note(line: str) -> None:
    results.append(line)
    print(">>>", line, flush=True)


def checkout(name: str) -> Path:
    dest = WORK / name
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "dist", "sdist", "build"))
    return dest


def venv(name: str) -> dict[str, str]:
    path = WORK / f"venv-{name}"
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PIP_CONSTRAINT", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")}
    env["PATH"] = f"{path}/bin:{env['PATH']}"
    env["VIRTUAL_ENV"] = str(path)
    env["VERSION"] = VERSION
    return env


def run(step: dict, cwd: Path, env: dict[str, str], extra: str = "") -> subprocess.CompletedProcess:
    script = step["run"].replace("${{ inputs.version }}", VERSION).rstrip() + extra + "\n"
    file = WORK / "step.sh"
    file.write_text(script)
    done = subprocess.run(["bash", "-e", str(file)], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(f"--- {step['name']} (exit {done.returncode})\n{done.stdout[-3000:]}", flush=True)
    return done


def step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


class Stub(http.server.BaseHTTPRequestHandler):
    uploads: list[str] = []
    skipped_wheel = ""
    duplicate_wheel = ""

    def do_GET(self) -> None:
        body = json.dumps({"releases": {VERSION: [{"filename": Stub.skipped_wheel}]}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_POST(self) -> None:
        data = self.rfile.read(int(self.headers["Content-Length"]))
        name = data.split(b'filename="')[1].split(b'"')[0].decode()
        Stub.uploads.append(name)
        if name == Stub.duplicate_wheel:
            self.send_response(400, "File already exists"); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

    def log_message(self, *args: object) -> None:
        pass


def main() -> int:
    flow = yaml.safe_load((REPO / ".github/workflows/build-and-publish.yml").read_text(encoding="utf-8"))
    jobs = flow["jobs"]

    # build-wheels job, in its own checkout and environment.
    build = checkout("build-wheels"); env = venv("build-wheels")
    assert run(step(jobs["build-wheels"], "Install build dependencies"), build, env).returncode == 0
    bundled = build / "src/claude_agent_sdk/_bundled"; bundled.mkdir(exist_ok=True)
    (bundled / "claude").write_bytes(os.urandom(300_000)); (bundled / "claude").chmod(0o755)
    done = run(step(jobs["build-wheels"], "Build wheel with bundled CLI"), build, env, " --skip-download")
    assert done.returncode == 0, "wheel build failed"
    wheel = next((build / "dist").glob("*.whl"))
    note(f"build-wheels: pinned install and --no-isolation build made {wheel.name}")

    # publish job, in a fresh checkout and environment. Stand-ins for the other platforms.
    pub = checkout("publish"); env = venv("publish"); job = jobs["publish"]
    (pub / "dist").mkdir()
    base = wheel.name.rsplit("-", 1)[0]
    for tag in TAGS:
        scratch = WORK / f"tag-{tag}"; scratch.mkdir()
        any_wheel = scratch / wheel.name; shutil.copy(wheel, any_wheel)
        subprocess.run([str(build.parent / "venv-build-wheels/bin/python"), "-m", "wheel", "tags", "--platform-tag", tag, "--remove", str(any_wheel)], check=True, stdout=subprocess.DEVNULL)
        shutil.copy(next(scratch.glob("*.whl")), pub / "dist")

    assert run(step(job, "Update version files"), pub, env).returncode == 0
    assert run(step(job, "Verify all expected wheels are present"), pub, env).returncode == 0
    assert run(step(job, "Install build dependencies"), pub, env).returncode == 0
    assert run(step(job, "Build sdist"), pub, env).returncode == 0
    versions = subprocess.run(["pip", "list", "--format=freeze"], env=env, stdout=subprocess.PIPE, text=True).stdout.split()
    picked = [v for v in versions if v.split("==")[0] in ("build", "hatchling", "twine", "wheel", "certifi", "requests")]
    note("publish: pinned install and sdist ok: " + ", ".join(picked))
    check = subprocess.run(["pip", "check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    note(f"publish: pip check: {check.stdout.strip()}")

    quota = run(step(job, "Pre-flight PyPI quota check"), pub, env)
    lines = [l for l in quota.stdout.splitlines() if "usage" in l.lower() or "::error" in l]
    note(f"publish: quota step exit {quota.returncode}: " + " | ".join(l[:130] for l in lines[:3]))

    # TLS from the pinned requests and certifi. A GET with no credentials.
    probe = (
        "import certifi, requests, socket, ssl\n"
        "for host in ('upload.pypi.org', 'pypi.org', 'pypi.python.org'):\n"
        "    try:\n"
        "        r = requests.get(f'https://{host}/', timeout=20, allow_redirects=False)\n"
        "        ctx = ssl.create_default_context(cafile=certifi.where())\n"
        "        with socket.create_connection((host, 443), timeout=20) as s, ctx.wrap_socket(s, server_hostname=host) as t:\n"
        "            issuer = dict(x[0] for x in t.getpeercert()['issuer'])\n"
        "        print(f'{host}: TLS ok with certifi {certifi.__version__}, HTTP {r.status_code}, issuer {issuer.get(\"organizationName\")} / {issuer.get(\"commonName\")}')\n"
        "    except Exception as e:\n"
        "        print(f'{host}: TLS FAILED {type(e).__name__}: {str(e)[:120]}')\n"
    )
    tls = subprocess.run(["python", "-c", probe], cwd=WORK, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    note("real TLS with the pinned bundle: " + " | ".join(tls.stdout.strip().splitlines()))

    # The upload step itself, against a local server standing in for PyPI.
    certs = WORK / "certs"; certs.mkdir()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=stand-in",
                    "-addext", "subjectAltName=" + ",".join(f"DNS:{h}" for h in HOSTS),
                    "-keyout", str(certs / "key.pem"), "-out", str(certs / "cert.pem")], check=True, stderr=subprocess.DEVNULL)
    names = sorted(p.name for p in (pub / "dist").glob("*.whl"))
    Stub.skipped_wheel = next(n for n in names if "win_amd64" in n)
    Stub.duplicate_wheel = next(n for n in names if "macosx_11_0_arm64" in n)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 443), Stub)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(certs / "cert.pem", certs / "key.pem")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    hosts = Path("/etc/hosts"); original = hosts.read_text()
    hosts.write_text(original + "".join(f"\n127.0.0.1 {h}" for h in HOSTS) + "\n")
    try:
        upload_env = dict(env, TWINE_USERNAME="__token__", TWINE_PASSWORD="pypi-stand-in", TWINE_CERT=str(certs / "cert.pem"), TWINE_NON_INTERACTIVE="1",
                          NO_PROXY=",".join(HOSTS), no_proxy=",".join(HOSTS))
        done = run(step(job, "Publish to PyPI (sdist first, then wheels)"), pub, upload_env)
    finally:
        hosts.write_text(original)
    first = Stub.uploads[0] if Stub.uploads else "none"
    note(f"publish: upload step exit {done.returncode}: {len(Stub.uploads)} POSTs, sdist first={first.endswith('.tar.gz')}, "
         f"already-on-index wheel skipped={Stub.skipped_wheel not in Stub.uploads}, duplicate wheel tolerated={Stub.duplicate_wheel in Stub.uploads}")
    ok = done.returncode == 0 and first.endswith(".tar.gz") and Stub.skipped_wheel not in Stub.uploads and len(Stub.uploads) == 5

    # A wrong hash must stop the install.
    bad = WORK / "tampered.txt"
    text = (REPO / "scripts/requirements-build.txt").read_text()
    pin = text.index("certifi=="); at = text.index("sha256:", pin) + 7
    bad.write_text(text[:at] + "0" * 8 + text[at + 8 :])
    # Every certifi hash must change, or pip accepts the other one.
    at2 = text.index("sha256:", at) + 7
    bad.write_text(text[:at] + "0" * 8 + text[at + 8 : at2] + "0" * 8 + text[at2 + 8 :])
    env3 = venv("tampered")
    t = subprocess.run(["pip", "install", "--require-hashes", "--only-binary", ":all:", "-r", str(bad)], env=env3, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    note(f"tampered hash: pip exit {t.returncode}, hash error reported={'DO NOT MATCH' in t.stdout}")
    ok = ok and t.returncode != 0 and "DO NOT MATCH" in t.stdout

    summary = "\n".join(results)
    print("\n=== RESULTS ===\n" + summary)
    if True:
        for i in range(0, len(results), 2):
            print("::notice title=dry run::" + "%0A".join(results[i:i + 2]))
    return 0 if ok and "FAILED" not in summary else 1


if __name__ == "__main__":
    sys.exit(main())
