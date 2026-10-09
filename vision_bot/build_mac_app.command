#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Building Mac Screen Vision Bot.app (fixed entry point) ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/macos/"
  read -r -p "Press Enter to exit..."
  exit 1
fi

python3 -m venv .build-venv
source .build-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# Build from the package so relative/package imports resolve correctly.
pyinstaller --noconfirm --clean --windowed \
  --name "Mac Screen Vision Bot" \
  --paths . \
  --collect-all cv2 \
  --collect-all mss \
  --collect-all numpy \
  --hidden-import vision_bot.settings \
  --hidden-import vision_bot.updater \
  --hidden-import packaging.version \
  --osx-bundle-identifier com.localscreenvision.bot \
  launch_app.py

echo
echo "Build finished. Your app should be at:"
echo "$(pwd)/dist/Mac Screen Vision Bot.app"
echo
echo "Drag it to Applications if you want. On first launch, macOS may ask for Screen Recording permission."
read -r -p "Press Enter to close..."
