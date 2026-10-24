"""Fetching and packaging application source."""
import os
import re
import subprocess
import zipfile
from pathlib import Path

# Only public HTTPS remotes: file://, ssh and local paths would let a user read the backend's own disk.
REPO_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/-]+$")


class SourceError(Exception):
    pass


def validate_repo_url(url: str) -> str:
    if not REPO_URL_RE.match(url):
        raise SourceError("repo_url must be a public https:// Git URL, e.g. https://github.com/user/repo.git")
    return url


def normalize_repo_url(url: str) -> str:
    """Canonical form used to match webhook payloads to registered apps."""
    url = url.strip().lower().rstrip("/")
    return url[:-4] if url.endswith(".git") else url


def clone(repo_url: str, branch: str, dest: Path) -> str:
    """Shallow-clone `branch` into `dest` and return the commit SHA. Requires a Dockerfile at the root."""
    validate_repo_url(repo_url)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang waiting for credentials
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, "--", repo_url, str(dest)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    if result.returncode != 0:
        raise SourceError(f"git clone failed: {result.stderr.strip()[-500:]}")
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if not (dest / "Dockerfile").is_file():
        raise SourceError("repository has no Dockerfile at its root; the platform only builds Dockerfile-based apps")
    return sha


def zip_source(src_dir: Path, out_file: Path) -> Path:
    """Zip the working tree (without .git) for upload to S3 / CodeBuild."""
    with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                path = Path(root) / f
                zf.write(path, path.relative_to(src_dir).as_posix())
    return out_file
