#!/usr/bin/env bash
set -euo pipefail

RULES_TMP="$(mktemp)"
cat > "$RULES_TMP" <<'EOF'
# links for pco usb cameras
#
SUBSYSTEM=="usb", ATTR{idVendor}=="1cb2", GROUP="video", MODE="0666", SYMLINK+="pco_usb_camera%n"
EOF

RULES_DIR="/etc/udev/rules.d"
RULES_FILE="$RULES_DIR/pco_usb.rules"

if [ ! -f "$RULES_FILE" ]; then
  sudo mkdir -p "$RULES_DIR"
  sudo cp "$RULES_TMP" "$RULES_FILE"
  sudo udevadm trigger || true
  echo "Installed $RULES_FILE"
else
  echo "$RULES_FILE already exists"
fi

rm -f "$RULES_TMP"
