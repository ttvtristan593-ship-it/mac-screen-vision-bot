            staged[rel_name] = zf.read(item)

    manifest_bytes = staged.pop("update_manifest.json", None)
    if not manifest_bytes:
        raise UpdateError(
            "Update ZIP is neither a full .app bundle nor a source patch "
            "with update_manifest.json."
        )

    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if str(manifest.get("version", "")).lstrip("v") != str(
        info["version"]
    ).lstrip("v"):
        raise UpdateError("Manifest version does not match the GitHub release.")

    expected = manifest.get("files")
    if (
        not isinstance(expected, dict)
        or not expected
        or set(staged) != set(expected)
    ):
        raise UpdateError(
            "Update manifest file list does not exactly match the ZIP contents."
        )

    for name, digest in expected.items():
        _safe_source_path(name)
        if not isinstance(digest, str) or _sha256(staged[name]) != digest.lower():
            raise UpdateError(f"SHA-256 validation failed for {name}.")

    backup_root = (
        Path.home() / "Library" / "Application Support"
        / "MacScreenVisionBot" / "source-backups"
    )
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

        (backup / "backup_manifest.json").write_text(
            json.dumps(
                {"version": CURRENT_VERSION, "changed": changed},
                indent=2,
            )
        )
    except Exception:
        for name in reversed(changed):
            dest = APP_ROOT / _safe_source_path(name)
            saved = backup / _safe_source_path(name)
            if saved.exists():
                shutil.copy2(saved, dest)
            else:
                try:
                    dest.unlink()
                except OSError:
                    pass
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
                    if str(
                        app_info.get("CFBundleShortVersionString", "")
                    ).strip().lstrip("v") != str(
                        info["version"]
                    ).lstrip("v"):
                        raise UpdateError(
                            "App bundle version does not match the GitHub release tag."
                        )
                    return _start_bundle_helper(
                        archive_path, app_info, app_pid or os.getpid()
                    )
        except zipfile.BadZipFile as exc:
            raise UpdateError(
                "GitHub update asset is not a valid ZIP archive."
            ) from exc

        return _install_source_patch(archive_path, info)
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError, InvalidVersion) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(
            f"Update failed validation or installation: {exc}"
        ) from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
