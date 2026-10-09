# Mac Screen Vision Controller Bot — Prototype

A macOS prototype for **user-selected visual target tracking**. It captures a screen region, lets you select a target from a screenshot, tracks that visual template, and produces normalized left-stick suggestions. It starts in **SIMULATION mode**; no controller output is sent unless you explicitly enable the local UDP bridge.

## What works in this prototype
- Screen capture via `mss`
- Select a target region from a paused screenshot
- OpenCV template matching to follow that target
- Adjustable tracking dead zone and gain
- Target-loss timeout and a large emergency-stop button
- Local UDP output protocol for a separately installed, compatible virtual-gamepad bridge
- Logs target position and simulated stick values

## Important compatibility note
macOS does not provide a universal built-in API for creating an arbitrary virtual Xbox controller. A compatible virtual-gamepad driver/bridge is required for actual controller emulation, and compatibility varies by macOS version and game. This project deliberately does **not** install drivers or bypass game protections. Check the target game's rules before using automated input; use only where permitted.

## Install
Use Python 3.11 or newer. In Terminal:

```bash
cd mac_screen_vision_bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m vision_bot.app
```

If macOS asks for Screen Recording permission, allow it in **System Settings → Privacy & Security → Screen & System Audio Recording** (wording varies by macOS version), then restart the app.

## Use
1. Launch the app and click **Capture / Select Target**.
2. Drag a rectangle around a visible target in the screenshot window and press Enter/Space.
3. Keep the target visually distinct. Template matching follows appearance; it does not understand game semantics and may lock onto a similar-looking object.
4. Test in **Simulation only** first.
5. For actual controller output, enable the UDP bridge only after configuring a trusted bridge that listens on `127.0.0.1:26760` and understands the protocol below.

## UDP bridge protocol
When enabled, the app sends one UTF-8 JSON datagram per update to `127.0.0.1:26760`:

```json
{"type":"stick","x":-0.25,"y":0.12,"active":true}
```

- `x` and `y` are clamped to `[-1, 1]`
- `active:false` means immediately neutralize the stick
- The app sends neutral output on target loss, pause, and shutdown

This is a protocol boundary, not a bundled virtual-controller driver. Do not point it at an unknown network host; the destination is hard-coded to loopback.

## Limitations
- Target tracking is based on a selected image patch, not a trained enemy detector.
- Occlusion, animation, camera movement, lighting changes, and similar-looking objects can cause loss or false matches.
- The prototype maps target screen offset to a configurable stick vector. It is not game-specific camera calibration.
- It intentionally does not press buttons, fire weapons, inject into a game process, or bypass anti-cheat systems.

## GitHub updater (new)
The app has **Check for Updates** and **Install Available Update** controls. Updates are checked against GitHub Releases, downloaded over HTTPS, and validated against SHA-256 hashes in `update_manifest.json` before installation. The app stores settings in `~/Library/Application Support/MacScreenVisionBot/settings.json`, outside the update tree. Existing source files are backed up before replacement and restoration is attempted if installation fails.

### Configure the repository
Set `MAC_SCREEN_VISION_BOT_REPOSITORY=owner/repo` in the environment before launching, or edit `GITHUB_REPOSITORY` in `vision_bot/updater.py`. The default is intentionally a placeholder; no public repository has been created for you.

### Publish an update release
Each release must have exactly one `.zip` asset containing `update_manifest.json` and only the files listed in its manifest. The manifest hashes must be generated from the exact files in the archive. Tag releases using semantic versions, e.g. `v1.1.0`. Because this prototype is not code-signed/notarized as a native app, the updater does not replace a packaged `.app` bundle or update its Python runtime. It updates the source files in the project folder.

### Security notes
- Hash validation detects corrupted/mismatched files, but does **not** authenticate the publisher by itself; HTTPS and the trusted GitHub repository are still important. For production, sign the manifest with a private signing key and embed the public verification key in the updater.
- Updates require explicit user confirmation and tracking/controller output must be stopped.
- The updater refuses unsafe paths, symlinks, unlisted files, and oversized archives.
- This is a prototype updater, not a notarized enterprise auto-updater. Review release contents and repository ownership before installing.


## Build a double-clickable macOS app
This source ZIP does not itself contain a compiled `.app`. To build one on your Mac:
1. Extract the ZIP.
2. In Finder, right-click `build_mac_app.command` and choose Open (or run it from Terminal).
3. Wait for PyInstaller to package the app.
4. Find `dist/Mac Screen Vision Bot.app` and drag it to Applications.

This build script requires Python 3 and an internet connection to install dependencies. It has not been tested on your specific Mac. For a distributable app, configure signing and notarization with an Apple Developer account. The app bundle updater in this prototype updates source files and is not a production-grade signed app-bundle updater.


## Fix for standalone app import error
If an earlier build crashes with `attempted relative import with no known parent package`, rebuild using the updated `build_mac_app.command`. It uses a package-aware launcher and includes the package modules.
