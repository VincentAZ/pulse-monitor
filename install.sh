#!/usr/bin/env bash
# Installs Pulse for the current user: launcher in ~/.local/bin, desktop entry + icon in ~/.local/share,
# pins it to the GNOME dock and adds a desktop shortcut. Options: --no-dock, --no-desktop.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
META_DIR="${HOME}/.local/share/metainfo"
mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR" "$META_DIR"
cat > "$BIN_DIR/pulse" <<LAUNCH
#!/usr/bin/env bash
exec python3 "$ROOT/bin/pulse" "\$@"
LAUNCH
chmod +x "$BIN_DIR/pulse"
sed "s|^Exec=pulse|Exec=$BIN_DIR/pulse|" "$ROOT/data/org.pulse.Monitor.desktop" > "$APP_DIR/org.pulse.Monitor.desktop"
cp "$ROOT/data/icons/org.pulse.Monitor.svg" "$ICON_DIR/org.pulse.Monitor.svg"
cp "$ROOT/data/org.pulse.Monitor.metainfo.xml" "$META_DIR/org.pulse.Monitor.metainfo.xml"
command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true

# Pin to the GNOME dock / dash (favourites) unless --no-dock is given.
if [[ " $* " != *" --no-dock "* ]] && command -v gsettings >/dev/null && gsettings writable org.gnome.shell favorite-apps >/dev/null 2>&1; then
  python3 - <<'PY'
import ast, subprocess
cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "favorite-apps"], capture_output=True, text=True).stdout.strip()
favs = ast.literal_eval(cur) if cur.startswith("[") else []
if "org.pulse.Monitor.desktop" not in favs:
    favs.append("org.pulse.Monitor.desktop")
    subprocess.run(["gsettings", "set", "org.gnome.shell", "favorite-apps", repr(favs)], check=True)
    print("Pinned Pulse to the dock.")
PY
fi

# Desktop shortcut unless --no-desktop is given.
if [[ " $* " != *" --no-desktop "* ]]; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "${HOME}/Desktop")"
  if [[ -d "$DESKTOP_DIR" ]]; then
    cp "$APP_DIR/org.pulse.Monitor.desktop" "$DESKTOP_DIR/org.pulse.Monitor.desktop"
    chmod +x "$DESKTOP_DIR/org.pulse.Monitor.desktop"
    # Mark the launcher as trusted so GNOME desktop icons run it without a prompt.
    command -v gio >/dev/null && gio set "$DESKTOP_DIR/org.pulse.Monitor.desktop" metadata::trusted true 2>/dev/null || true
    echo "Desktop shortcut created at $DESKTOP_DIR/org.pulse.Monitor.desktop"
  fi
fi
echo "Installed. Launch with: $BIN_DIR/pulse, from the dock, the desktop icon, or the app grid as 'Pulse'"
