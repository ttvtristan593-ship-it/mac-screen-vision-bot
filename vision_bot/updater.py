"""GitHub Releases updater for source-mode and packaged macOS app updates.

For packaged releases, attach exactly one ZIP containing the complete
"Mac Screen Vision Bot.app" bundle at its archive root. The app bundle's
CFBundleIdentifier must be com.localscreenvision.bot and its version must match
its GitHub release tag. The updater downloads and validates the archive, then
starts a separate helper that waits for this app to exit before swapping bundles.

For source-mode development releases, the legacy update_manifest.json format is
also supported. Never use source patches as a substitute for a complete .app
bundle update for a PyInstaller-built application.
"""
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from packaging.version import Version, InvalidVersion

APP_ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = os.environ.get(
    "MAC_SCREEN_VISION_BOT_REPOSITORY",
    "ttvtristan593-ship-it/mac-screen-vision-bot",
)
def _read_current_version():
    candidates = [APP_ROOT / "VERSION"]
    # In a PyInstaller app, resources may live under sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "VERSION")
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    # Safe fallback for the currently installed v1.0.4 app.
    return "1.0.4"

CURRENT_VERSION = _read_current_version()
API_BASE = "https://api.github.com"
MAX_DOWNLOAD_BYTES = 1_500 * 1024 * 1024
MAX_SOURCE_EXPANDED_BYTES = 30 * 1024 * 1024
ALLOWED_SOURCE_NAMES = ("requirements.txt", "README.md", "run_mac.command")
APP_BUNDLE_ID = "com.localscreenvision.bot"
APP_NAME = "Mac Screen Vision Bot.app"

class UpdateError(RuntimeError):
    pass

def _request(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": "MacScreenVisionBot-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as response:
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
        raise UpdateError("Release tag must be a semantic version, such as v1.0.5.") from exc
    assets = [a for a in release.get("assets", []) if str(a.get("name", "")).endswith(".zip")]
    if len(assets) != 1:
        raise UpdateError("Release must contain exactly one .zip update asset.")
    asset = assets[0]
    return {
        "version": tag,
        "download_url": asset.get("browser_download_url"),
        "asset_name": asset.get("name"),
        "release_url": release.get("html_url", ""),
        "notes": release.get("body", ""),
    }

def _safe_source_path(name):
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts):
        raise UpdateError(f"Unsafe update path: {name}")
    if not (name.startswith("vision_bot/") or name in ALLOWED_SOURCE_NAMES):
        raise UpdateError(f"Update contains disallowed source path: {name}")
    return Path(*p.parts)

def _sha256(data):
    return hashlib.sha256(data).hexdigest()

def _download_to_temp(url, temp_root):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpdateError("Release download URL is missing or not HTTPS.")
    archive_path = temp_root / "update.zip"
    total = 0
    with urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "MacScreenVisionBot-Updater"}
    ), timeout=90) as response, archive_path.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise UpdateError("Update archive exceeds the 1.5 GB download limit.")
            out.write(chunk)
    if not total:
        raise UpdateError("GitHub returned an empty update archive.")
    return archive_path

def _find_app_bundle(zip_file):
    entries = [i for i in zip_file.infolist() if not i.is_dir()]
    names = [i.filename.replace("\\", "/") for i in entries]
    expanded_size = sum(i.file_size for i in entries)
    if expanded_size > 2_500 * 1024 * 1024:
        raise UpdateError("Expanded app bundle exceeds the 2.5 GB safety limit.")
    for item in zip_file.infolist():
        name = item.filename.replace("\\", "/").rstrip("/")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in ("..", "") for part in path.parts):
            raise UpdateError(f"Unsafe path in app update archive: {name}")
        if name and not (name.startswith(f"{APP_NAME}/") or name == APP_NAME or name.startswith("__MACOSX/")):
            raise UpdateError(f"Unexpected file outside app bundle in update archive: {name}")
    app_prefixes = set()
    for name in names:
        marker = f"/{APP_NAME}/Contents/Info.plist"
        if name.endswith(marker):
            app_prefixes.add(name[:-len("Contents/Info.plist")].rstrip("/"))
        if name == f"{APP_NAME}/Contents/Info.plist":
            app_prefixes.add(APP_NAME)
    if len(app_prefixes) != 1:
        return None
    prefix = next(iter(app_prefixes))
    if prefix != APP_NAME:
        raise UpdateError(f"App bundle must be at the ZIP root as {APP_NAME}.")
    info_name = f"{APP_NAME}/Contents/Info.plist"
    try:
        info = plistlib.loads(zip_file.read(info_name))
    except Exception as exc:
        raise UpdateError(f"Could not read app bundle Info.plist: {exc}") from exc
    if info.get("CFBundleIdentifier") != APP_BUNDLE_ID:
        raise UpdateError("Downloaded app bundle has the wrong bundle identifier.")
    return info

def _current_app_bundle():
    executable = Path(sys.executable).absolute()
    for parent in (executable, *executable.parents):
        if parent.name.endswith(".app") and parent.is_dir():
            return parent
    for parent in executable.parents:
        if parent.name == "MacOS" and parent.parent.name == "Contents" and parent.parent.parent.name.endswith(".app"):
            return parent.parent.parent
    return None

def _start_bundle_helper(archive_path, info, app_pid):
    app_path = _current_app_bundle()
    if app_path is None:
        raise UpdateError("Cannot identify installed app location. Open the app from Finder and try again.")
    version = str(info.get("CFBundleShortVersionString", "")).strip()
    if not version or Version(version) <= Version(CURRENT_VERSION):
        raise UpdateError("The update version must be newer than the installed version.")
    helper_root = Path.home() / "Library" / "Application Support" / "MacScreenVisionBot" / "updater"
    backup_root = Path.home() / "Library" / "Application Support" / "MacScreenVisionBot" / "backups"
    helper_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    archive_dest = helper_root / f"update-{version}.zip"
    shutil.copy2(archive_path, archive_dest)
    helper = helper_root / f"install-{version}.sh"
    log_path = helper_root / f"install-{version}.log"
    helper_script = '#!/bin/bash\nset -u\nARCHIVE="$1"\nAPP_PATH="$2"\nPARENT_PID="$3"\nBACKUP_ROOT="$4"\nVERSION="$5"\nSUPPORT_ROOT="$6"\nLOG="$SUPPORT_ROOT/install-$VERSION.log"\nSTAGE="$SUPPORT_ROOT/stage-$VERSION-$$"\nNEW_APP="$SUPPORT_ROOT/new-$VERSION-$$.app"\nBACKUP="$BACKUP_ROOT/Mac Screen Vision Bot-$VERSION-$(date +%Y%m%d-%H%M%S).app"\nOLD_HOLD="$SUPPORT_ROOT/old-$VERSION-$$.app"\nlog() { printf \'[%s] %s\\n\' "$(date \'+%Y-%m-%d %H:%M:%S\')" "$*" >> "$LOG"; }\nfail() { log "ERROR: $*"; exit 1; }\ncleanup() { rm -rf "$STAGE" "$NEW_APP"; }\ntrap cleanup EXIT\n: > "$LOG"\nlog "Starting v$VERSION update"\nmkdir -p "$STAGE" "$BACKUP_ROOT" || fail "Could not create staging/backup directories"\n/usr/bin/ditto -x -k "$ARCHIVE" "$STAGE" >>"$LOG" 2>&1 || fail "Could not extract update archive"\nSTAGED_APP="$STAGE/Mac Screen Vision Bot.app"\n[ -f "$STAGED_APP/Contents/Info.plist" ] || fail "ZIP does not contain the expected app bundle"\nBUNDLE_ID=$(/usr/libexec/PlistBuddy -c \'Print :CFBundleIdentifier\' "$STAGED_APP/Contents/Info.plist" 2>>"$LOG") || fail "Could not read bundle identifier"\nBUNDLE_VERSION=$(/usr/libexec/PlistBuddy -c \'Print :CFBundleShortVersionString\' "$STAGED_APP/Contents/Info.plist" 2>>"$LOG") || fail "Could not read app version"\n[ "$BUNDLE_ID" = "com.localscreenvision.bot" ] || fail "Unexpected bundle identifier: $BUNDLE_ID"\n[ "$BUNDLE_VERSION" = "$VERSION" ] || fail "Bundle version $BUNDLE_VERSION does not match $VERSION"\nlog "Staged app verified; waiting for app process $PARENT_PID to exit"\nwhile kill -0 "$PARENT_PID" 2>/dev/null; do sleep 1; done\nlog "App process exited; preparing replacement"\n/usr/bin/ditto "$STAGED_APP" "$NEW_APP" >>"$LOG" 2>&1 || fail "Could not stage replacement beside installed app"\n[ -f "$NEW_APP/Contents/Info.plist" ] || fail "Staged replacement is incomplete"\nNEW_VERSION=$(/usr/libexec/PlistBuddy -c \'Print :CFBundleShortVersionString\' "$NEW_APP/Contents/Info.plist" 2>>"$LOG") || fail "Could not verify staged replacement"\n[ "$NEW_VERSION" = "$VERSION" ] || fail "Staged replacement version check failed"\n/usr/bin/ditto "$APP_PATH" "$BACKUP" >>"$LOG" 2>&1 || fail "Could not back up current app"\nmv "$APP_PATH" "$OLD_HOLD" >>"$LOG" 2>&1 || fail "Could not move current app aside"\nif mv "$NEW_APP" "$APP_PATH" >>"$LOG" 2>&1; then\n  INSTALLED_VERSION=$(/usr/libexec/PlistBuddy -c \'Print :CFBundleShortVersionString\' "$APP_PATH/Contents/Info.plist" 2>>"$LOG") || INSTALLED_VERSION=""\n  if [ "$INSTALLED_VERSION" = "$VERSION" ]; then\n    rm -rf "$OLD_HOLD"\n    log "Install succeeded; launching updated app"\n    /usr/bin/open "$APP_PATH" >>"$LOG" 2>&1\n    OPEN_STATUS=$?\n    log "open command exit status: $OPEN_STATUS"\n    exit "$OPEN_STATUS"\n  fi\n  log "Installed version verification failed; rolling back"\nfi\nrm -rf "$APP_PATH"\nif mv "$OLD_HOLD" "$APP_PATH" >>"$LOG" 2>&1; then\n  log "Rollback restored previous app; reopening previous version"\n  /usr/bin/open "$APP_PATH" >>"$LOG" 2>&1 || log "WARNING: rollback app could not be opened"\nelse\n  /usr/bin/ditto "$BACKUP" "$APP_PATH" >>"$LOG" 2>&1 || log "CRITICAL: backup restore failed"\n  /usr/bin/open "$APP_PATH" >>"$LOG" 2>&1 || log "WARNING: restored app could not be opened"\nfi\nfail "Update failed; previous app restored where possible"\n'
    helper.write_text(helper_script, encoding="utf-8")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"Updater launched for version {version}; target={app_path}\\n")
    subprocess.Popen(
        ["/bin/bash", str(helper), str(archive_dest), str(app_path), str(int(app_pid)),
         str(backup_root), version, str(helper_root)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=True,
    )
    return {
        "mode": "app_bundle", "version": version, "app_path": str(app_path),
        "backup_root": str(backup_root), "log_path": str(log_path),
        "restart_required": True,
    }


def _install_source_patch(archive_path, info):
    with zipfile.ZipFile(archive_path) as zf:
        staged = {}
        total = 0
        for item in zf.infolist():
            if item.is_dir():
                continue
            if item.filename == "update_manifest.json":
                rel_name = "update_manifest.json"
            else:
                rel_name = _safe_source_path(item.filename).as_posix()
            mode = (item.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                raise UpdateError("Symlinks are not allowed in update archives.")
            total += item.file_size
            if total > MAX_SOURCE_EXPANDED_BYTES:
                raise UpdateError("Expanded source update exceeds 30 MB.")
            if rel_name in staged:
                raise UpdateError(f"Duplicate path in update archive: {rel_name}")
            staged[rel_name] = zf.read(item)
    manifest_bytes = staged.pop("update_manifest.json", None)
    if not manifest_bytes:
        raise UpdateError("Update ZIP is neither a full .app bundle nor a source patch with update_manifest.json.")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if str(manifest.get("version", "")).lstrip("v") != str(info["version"]).lstrip("v"):
        raise UpdateError("Manifest version does not match the GitHub release.")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected or set(staged) != set(expected):
        raise UpdateError("Update manifest file list does not exactly match the ZIP contents.")
    for name, digest in expected.items():
        _safe_source_path(name)
        if not isinstance(digest, str) or _sha256(staged[name]) != digest.lower():
            raise UpdateError(f"SHA-256 validation failed for {name}.")
    backup_root = Path.home() / "Library" / "Application Support" / "MacScreenVisionBot" / "source-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"backup-{CURRENT_VERSION}"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    changed = []
    try:
        for name, content in staged.items():
            dest = APP_ROOT / _safe_source_path(name)
            if dest.exists() and dest.is_file():
                saved = backup / _safe_source_path(name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)
        for name, content in staged.items():
            dest = APP_ROOT / _safe_source_path(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".update-tmp")
            tmp.write_bytes(content)
            tmp.replace(dest)
            changed.append(name)
        (backup / "backup_manifest.json").write_text(json.dumps({"version": CURRENT_VERSION, "changed": changed}, indent=2))
    except Exception:
        for name in reversed(changed):
            dest = APP_ROOT / _safe_source_path(name)
            saved = backup / _safe_source_path(name)
            if saved.exists():
                shutil.copy2(saved, dest)
            else:
                try: dest.unlink()
                except OSError: pass
        raise
    return {"mode": "source_patch", "backup": str(backup)}

def download_and_install_update(info, app_pid=None):
    temp_root = Path(tempfile.mkdtemp(prefix="macsvb-update-"))
    try:
        archive_path = _download_to_temp(info.get("download_url"), temp_root)
        try:
            with zipfile.ZipFile(archive_path) as zf:
                app_info = _find_app_bundle(zf)
                if app_info is not None:
                    if str(app_info.get("CFBundleShortVersionString", "")).strip().lstrip("v") != str(info["version"]).lstrip("v"):
                        raise UpdateError("App bundle version does not match the GitHub release tag.")
                    return _start_bundle_helper(archive_path, app_info, app_pid or os.getpid())
        except zipfile.BadZipFile as exc:
            raise UpdateError("GitHub update asset is not a valid ZIP archive.") from exc
        return _install_source_patch(archive_path, info)
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError, InvalidVersion) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update failed validation or installation: {exc}") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
