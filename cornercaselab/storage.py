"""Append-only run records, atomic JSON files and reproducibility metadata."""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
from typing import Any

from . import __version__

TRACKED_PACKAGES = ("highway-env", "gymnasium", "numpy", "pygame", "pandas", "scipy", "matplotlib", "Pillow")


def source_digest() -> str:
    h = sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        # Normalise checkout line endings; contents still affect the hash.
        h.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return h.hexdigest()


def metadata() -> dict[str, Any]:
    packages = {}
    for name in TRACKED_PACKAGES:
        try:
            packages[name] = md.version(name)
        except md.PackageNotFoundError:
            packages[name] = None
    root = Path(__file__).resolve().parent.parent
    git = {"commit": None, "dirty": None}
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=3, check=True)
        state = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                               text=True, timeout=3, check=True)
        git = {"commit": commit.stdout.strip(), "dirty": bool(state.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"project_version": __version__, "python": platform.python_version(),
            "platform": platform.platform(), "packages": packages,
            "source_sha256": source_digest(), "git": git,
            "created_utc": datetime.now(timezone.utc).isoformat()}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+"\n"
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+"\n")
        f.flush()
        os.fsync(f.fileno())


def fingerprint(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta[k] for k in ("project_version", "python", "platform", "packages", "source_sha256")}
