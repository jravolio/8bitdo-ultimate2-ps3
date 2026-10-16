"""The XPAD Revolution files this project installs, pinned to one audited commit.

They are not redistributed here: they are downloaded from XPAD Revolution's own GitHub repository
(the git tree, NOT the release zip, which Microsoft Defender flags) and every file must match the
SHA-256 below before anything is sent to the PS3. The in-memory patch in ps3mapi.py is only valid
for exactly this build of xpad_vsh_autodiscovery.sprx.
"""
from __future__ import annotations

import hashlib
import subprocess
import urllib.request
from pathlib import Path

from . import PS3_PAYLOAD, XPAD_REPO

XPAD_GITHUB = "https://github.com/GabaRSk/XPAD-Revolution"
XPAD_COMMIT = "05929739baaab0e57b0d8ca3ad5c6407eccb19c9"  # v1.0.0
RAW_BASE = f"https://raw.githubusercontent.com/GabaRSk/XPAD-Revolution/{XPAD_COMMIT}/plugin/ps3xpad"

# sha256 of the files exactly as stored in git at XPAD_COMMIT (text files with LF line endings)
PINNED = {
    "xpad_vsh_autodiscovery.sprx": "cd956976951fdf2306acdb6c2cb1bed2bcb6bfa979c856022934063493ec2cb7",
    "xpad_game.sprx": "5c5fcb90bb6059102e50b0717e8a55192bdda530aa66479380ebd286d01030c6",
    "xpad_devices.txt": "17fcab5766510dcadf58bfdf5805f154ae9de73e05a667bf40fd7dca57c8aa3c",
    "xpad_remap.txt": "2ad1e7308bf28c8602d55093fe2a312e7b3885e0612c71992840f8ac7744cd3b",
    "xpad_analog.txt": "12b91034c979153e6f598a24070cc93198c0c29f89207f958ccc52310c6f7b8a",
}
SIZES = {"xpad_vsh_autodiscovery.sprx": 30343, "xpad_game.sprx": 7731}
PAYLOAD_FILES = list(PINNED)


def verified(name: str, data: bytes) -> bool:
    """True if data is the pinned file. Text files may have been checked out with CRLF endings
    (git autocrlf); XPAD's parser strips '\\r', so those count as the same file."""
    if hashlib.sha256(data).hexdigest() == PINNED[name]:
        return True
    return name.endswith(".txt") and hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() == PINNED[name]


def fetch(dest: Path = PS3_PAYLOAD, log=print) -> Path:
    """Make sure dest holds the five pinned files: keep the ones already there and correct,
    download the rest. Raises if a download does not match its hash."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD_FILES:
        path = dest / name
        if path.exists() and verified(name, path.read_bytes()):
            continue
        local = XPAD_REPO / "plugin" / "ps3xpad" / name  # a local clone at the pinned commit, if any
        if local.exists() and verified(name, local.read_bytes()):
            data = local.read_bytes()
        else:
            log(f"  downloading {name} from {XPAD_GITHUB} @ {XPAD_COMMIT[:7]}")
            with urllib.request.urlopen(f"{RAW_BASE}/{name}", timeout=30) as r:
                data = r.read()
        if not verified(name, data):
            raise RuntimeError(f"{name}: SHA-256 {hashlib.sha256(data).hexdigest()} does not match the pinned "
                               f"{PINNED[name]}; refusing to use it")
        if hashlib.sha256(data).hexdigest() != PINNED[name]:
            data = data.replace(b"\r\n", b"\n")  # store exactly the pinned bytes
        path.write_bytes(data)
    return dest


def ensure_source(log=print) -> Path:
    """The plugin source for the simulator: a git clone of XPAD Revolution at XPAD_COMMIT."""
    main_c = XPAD_REPO / "src" / "ps3" / "main.c"
    if main_c.exists():
        return XPAD_REPO
    log(f"cloning {XPAD_GITHUB} @ {XPAD_COMMIT[:7]} into {XPAD_REPO} (simulator source)")
    XPAD_REPO.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", XPAD_GITHUB + ".git", str(XPAD_REPO)], check=True)
    subprocess.run(["git", "-C", str(XPAD_REPO), "checkout", "--quiet", XPAD_COMMIT], check=True)
    return XPAD_REPO
