#!/usr/bin/env bash
set -euo pipefail
rm -f "${HOME}/.local/bin/pulse" "${HOME}/.local/share/applications/org.pulse.Monitor.desktop" \
      "${HOME}/.local/share/icons/hicolor/scalable/apps/org.pulse.Monitor.svg" "${HOME}/.local/share/metainfo/org.pulse.Monitor.metainfo.xml"
rm -f "$(xdg-user-dir DESKTOP 2>/dev/null || echo "${HOME}/Desktop")/org.pulse.Monitor.desktop"
if command -v gsettings >/dev/null && gsettings writable org.gnome.shell favorite-apps >/dev/null 2>&1; then
  python3 - <<'PY'
import ast, subprocess
cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "favorite-apps"], capture_output=True, text=True).stdout.strip()
favs = ast.literal_eval(cur) if cur.startswith("[") else []
if "org.pulse.Monitor.desktop" in favs:
    favs.remove("org.pulse.Monitor.desktop")
    subprocess.run(["gsettings", "set", "org.gnome.shell", "favorite-apps", repr(favs)], check=True)
PY
fi
echo "Pulse removed (settings in ~/.config/pulse were kept)."
