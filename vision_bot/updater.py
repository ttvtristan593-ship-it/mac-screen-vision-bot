"""
GitHub Releases updater.

Configure GITHUB_REPOSITORY as "owner/repo" in this file or via the environment.
Release ZIP must contain:
  update_manifest.json:
    {"version":"1.1.0","files":{"vision_bot/app.py":"<sha256>", ...}}
  plus each listed file at its relative path.
Only files explicitly listed in the manifest are installed. Absolute paths, parent
traversal, symlinks, and unexpected paths are rejected. Current source is backed
up before changes and restored if any install step fails.
"""
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path, PurePosixPath
from packaging.version import Version, InvalidVersion

APP_ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = os.environ.get("MAC_SCREEN_VISION_BOT_REPOSITORY", "ttvtristan593-ship-it/mac-screen-vision-bot")
CURRENT_VERSION = "1.0.0"
API_BASE = "https://api.github.com"
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
ALLOWED_PREFIXES = ("vision_bot/", "requirements.txt", "README.md", "run_mac.command")

class UpdateError(RuntimeError):
    pass

def _request(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": "MacScreenVisionBot-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read()

def check_for_update():
    if not GITHUB_REPOSITORY or "YOUR_GITHUB_USERNAME" in GITHUB_REPOSITORY:
        return None
    if "/" not in GITHUB_REPOSITORY or GITHUB_REPOSITORY.count("/") != 1:
        raise UpdateError("Repository must be owner/repo.")
    raw = _request(f"{API_BASE}/repos/{GITHUB_REPOSITORY}/releases/latest")
    release = json.loads(raw.decode("utf-8"))
    tag = str(release.get("tag_name", "")).strip().lstrip("v")
    try:
        if Version(tag) <= Version(CURRENT_VERSION):
            return None
    except InvalidVersion as exc:
        raise UpdateError("Release tag must be a semantic version, such as v1.1.0.") from exc
    assets = release.get("assets", [])
    candidates = [a for a in assets if a.get("name", "").endswith(".zip")]
    if len(candidates) != 1:
        raise UpdateError("Release must contain exactly one .zip update asset.")
    asset = candidates[0]
    return {
        "version": tag,
        "download_url": asset.get("browser_download_url"),
        "asset_name": asset.get("name"),
        "release_url": release.get("html_url", ""),
        "notes": release.get("body", ""),
    }

def _safe_relpath(name):
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts):
        raise UpdateError(f"Unsafe update path: {name}")
    if not (name.startswith("vision_bot/") or name in ("requirements.txt", "README.md", "run_mac.command")):
        raise UpdateError(f"Update contains disallowed path: {name}")
    return Path(*p.parts)

def _sha256(data):
    return hashlib.sha256(data).hexdigest()

def download_and_install_update(info):
    if not info.get("download_url", "").startswith("https://"):
        raise UpdateError("Release download URL is missing or not HTTPS.")
    with urllib.request.urlopen(urllib.request.Request(
        info["download_url"], headers={"User-Agent": "MacScreenVisionBot-Updater"}
    ), timeout=60) as response:
        archive_data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive_data) > MAX_ARCHIVE_BYTES:
        raise UpdateError("Update archive exceeds 30 MB limit.")

    temp_root = Path(tempfile.mkdtemp(prefix="macsvb-update-"))
    extract_root = temp_root / "extract"
    extract_root.mkdir()
    backup_root = Path.home() / "Library" / "Application Support" / "MacScreenVisionBot" / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"backup-{CURRENT_VERSION}"
    staged = {}
    changed = []
    try:
        archive_path = temp_root / "update.zip"
        archive_path.write_bytes(archive_data)
        with zipfile.ZipFile(archive_path) as zf:
            total = 0
            for item in zf.infolist():
                if item.is_dir():
                    continue
                if item.filename == "update_manifest.json":
                    rel = Path("update_manifest.json")
                else:
                    rel = _safe_relpath(item.filename)
                # reject unix symlinks and other special file types
                mode = (item.external_attr >> 16) & 0xFFFF
                if mode and (mode & 0o170000) == 0o120000:
                    raise UpdateError("Symlinks are not allowed in update archives.")
                total += item.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise UpdateError("Expanded update exceeds 30 MB.")
                staged[rel.as_posix()] = zf.read(item)

        manifest_bytes = staged.pop("update_manifest.json", None)
        if not manifest_bytes:
            raise UpdateError("Update is missing update_manifest.json.")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if str(manifest.get("version", "")).lstrip("v") != str(info["version"]).lstrip("v"):
            raise UpdateError("Manifest version does not match the GitHub release.")
        expected = manifest.get("files")
        if not isinstance(expected, dict) or not expected:
            raise UpdateError("Manifest has no file hashes.")
        if set(staged) != set(expected):
            raise UpdateError("Archive file list does not exactly match the signed-off manifest list.")
        for name, expected_hash in expected.items():
            _safe_relpath(name)
            if not isinstance(expected_hash, str) or _sha256(staged[name]) != expected_hash.lower():
                raise UpdateError(f"SHA-256 validation failed for {name}.")

        # Keep settings out of the source tree. Back up only paths that will be changed.
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(parents=True)
        for name in staged:
            dest = APP_ROOT / _safe_relpath(name)
            if dest.exists() and dest.is_file():
                saved = backup / _safe_relpath(name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)

        try:
            for name, content in staged.items():
                rel = _safe_relpath(name)
                dest = APP_ROOT / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".update-tmp")
                tmp.write_bytes(content)
                tmp.replace(dest)
                changed.append(rel)
            (backup / "backup_manifest.json").write_text(json.dumps({
                "version": CURRENT_VERSION, "changed": [p.as_posix() for p in changed]
            }, indent=2))
        except Exception:
            for rel in reversed(changed):
                dest = APP_ROOT / rel
                saved = backup / rel
                if saved.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, dest)
                else:
                    try: dest.unlink()
                    except OSError: pass
            raise
        return {"backup": str(backup)}
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError) as exc:
        raise UpdateError(f"Update failed validation or installation: {exc}") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
