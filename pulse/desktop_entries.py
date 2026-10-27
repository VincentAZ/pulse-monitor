"""Index of .desktop files: maps app ids / executables to display names and icons."""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass


@dataclass
class DesktopEntry:
    id: str
    name: str
    icon: str | None
    exec_name: str | None
    wm_class: str | None
    no_display: bool = False


def _data_dirs() -> list[str]:
    home = os.path.expanduser("~")
    dirs = [os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += ["/var/lib/flatpak/exports/share", os.path.join(home, ".local", "share", "flatpak", "exports", "share"),
             "/var/lib/snapd/desktop"]
    out = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


_SKIP_EXEC = {"env", "exec", "sh", "bash", "flatpak", "snap", "gio", "xdg-open", "python", "python3", "wine", "gjs",
              "electron", "nohup", "sudo", "pkexec", "dbus-launch", "gnome-session"}


def exec_basename(exec_line: str) -> str | None:
    """Best effort: executable basename from a Desktop Exec= line."""
    try:
        parts = shlex.split(exec_line.replace("%", "%%"))
    except ValueError:
        parts = exec_line.split()
    parts = [p.replace("%%", "%") for p in parts]
    if not parts:
        return None
    first = os.path.basename(parts[0])
    if first == "flatpak" and "run" in parts:
        for part in parts[parts.index("run") + 1:]:
            if not part.startswith("-"):
                return part
        return None
    if first in ("sh", "bash", "zsh", "dash") and len(parts) >= 3 and parts[1] == "-c":
        return exec_basename(parts[2])
    for part in parts:
        if part.startswith("%") or ("=" in part and not part.startswith("/")):
            continue
        base = os.path.basename(part)
        if base in _SKIP_EXEC or base.startswith("-"):
            continue
        return base
    return None


def parse_desktop_file(path: str, entry_id: str) -> DesktopEntry | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    in_main = False
    data: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_main = line == "[Desktop Entry]"
            continue
        if not in_main or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in data:
            data[key] = value.strip()
    if data.get("Type", "Application") != "Application":
        return None
    name = data.get("Name") or entry_id
    return DesktopEntry(id=entry_id, name=name, icon=data.get("Icon") or None,
                        exec_name=exec_basename(data.get("Exec", "")) if data.get("Exec") else None,
                        wm_class=data.get("StartupWMClass") or None,
                        no_display=data.get("NoDisplay", "false").lower() == "true")


class DesktopIndex:
    def __init__(self, dirs: list[str] | None = None):
        self.by_id: dict[str, DesktopEntry] = {}
        self.by_exec: dict[str, DesktopEntry] = {}
        self.by_wmclass: dict[str, DesktopEntry] = {}
        self.load(dirs)

    def load(self, dirs: list[str] | None = None) -> None:
        for d in dirs if dirs is not None else _data_dirs():
            app_dir = os.path.join(d, "applications")
            try:
                for root, _, files in os.walk(app_dir):
                    for f in files:
                        if not f.endswith(".desktop"):
                            continue
                        entry_id = f[:-8]
                        entry = parse_desktop_file(os.path.join(root, f), entry_id)
                        if entry is None:
                            continue
                        key = entry_id.lower()
                        if key not in self.by_id:
                            self.by_id[key] = entry
                        if entry.exec_name and not entry.no_display:
                            self.by_exec.setdefault(entry.exec_name.lower(), entry)
                        if entry.wm_class:
                            self.by_wmclass.setdefault(entry.wm_class.lower(), entry)
            except OSError:
                continue

    def lookup_id(self, app_id: str) -> DesktopEntry | None:
        app_id = app_id.lower()
        e = self.by_id.get(app_id)
        if e:
            return e
        # snap style "firefox_firefox", flatpak reverse-dns, or id with suffix
        for cand in (app_id.replace("_", "."), app_id.split("_")[-1]):
            e = self.by_id.get(cand)
            if e:
                return e
        for key, e in self.by_id.items():
            if key.endswith("." + app_id) or key.startswith(app_id + "."):
                return e
        return None

    def lookup_exec(self, exe: str) -> DesktopEntry | None:
        exe = exe.lower()
        return self.by_exec.get(exe) or self.by_wmclass.get(exe)


_SCOPE_RE = re.compile(r"^app-(?:[A-Za-z0-9]+-)?(?P<id>.+?)(?:-[0-9a-f]+)?\.scope$")
_SCOPE_PID_RE = re.compile(r"^app-(?:(?P<launcher>gnome|flatpak|snap|launched|dbus|KDE|org\.kde\.plasma)-)?(?P<id>.+?)(?:-\d+)?\.scope$")
_SNAP_RE = re.compile(r"^snap\.(?P<snap>[^.]+)\.(?P<app>[^-]+)(?:-[0-9a-f-]+)?\.scope$")


def app_id_from_cgroup(cgroup_line: str) -> str | None:
    """'0::/user.slice/.../app-gnome-firefox-1234.scope' -> 'firefox'.

    Returns None for processes that are not inside an application scope.
    """
    path = cgroup_line.strip()
    if "::" in path:
        path = path.split("::", 1)[1]
    leaf = path.rsplit("/", 1)[-1]
    if not leaf.endswith(".scope"):
        return None
    m = _SNAP_RE.match(leaf)
    if m:
        return f"{m.group('snap')}_{m.group('app')}"
    m = _SCOPE_PID_RE.match(leaf)
    if m:
        app = m.group("id")
        # gnome-shell escapes '-' in ids as '\x2d'
        app = app.replace("\\x2d", "-")
        return app
    return None
