"""Hand-offs to other desktop apps (GNOME Disks, the file manager).

Pulse stays read-only: it never mounts, formats or ejects anything itself. These helpers just open
the right external tool, and report failures back to the caller so the UI can show a toast.
"""
from __future__ import annotations

import shutil
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

DISKS_APP_IDS = ("org.gnome.DiskUtility.desktop", "gnome-disks.desktop")
DISKS_BINARY = "gnome-disks"


def find_disks_app() -> Gio.DesktopAppInfo | None:
    """The GNOME Disks desktop entry, if installed."""
    for app_id in DISKS_APP_IDS:
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except Exception:
            info = None
        if info is not None:
            return info
    return None


def disks_available() -> bool:
    return find_disks_app() is not None or shutil.which(DISKS_BINARY) is not None


def disks_command(device: str) -> list[str]:
    """Command line that opens GNOME Disks focused on a block device such as nvme0n1."""
    path = device if device.startswith("/dev/") else f"/dev/{device}"
    return [DISKS_BINARY, f"--block-device={path}"]


def open_in_disks(device: str, spawn: Callable[[list[str]], None] | None = None) -> str | None:
    """Launch GNOME Disks on `device`. Returns an error message, or None on success."""
    if not disks_available():
        return "GNOME Disks is not installed"
    argv = disks_command(device)
    try:
        if spawn is not None:
            spawn(argv)
        else:
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
    except GLib.Error as exc:
        return f"Could not start GNOME Disks: {exc.message}"
    except OSError as exc:
        return f"Could not start GNOME Disks: {exc}"
    return None


def open_folder(path: str, parent: Gtk.Window | None = None,
                launcher: Callable[[str], None] | None = None) -> str | None:
    """Open a directory in the default file manager. Returns an error message, or None."""
    if launcher is not None:
        try:
            launcher(path)
        except Exception as exc:  # the stub decides what counts as failure
            return f"Could not open {path}: {exc}"
        return None
    gfile = Gio.File.new_for_path(path)
    try:
        Gtk.FileLauncher.new(gfile).launch(parent, None, _on_folder_launched, path)
    except GLib.Error as exc:
        return f"Could not open {path}: {exc.message}"
    return None


def _on_folder_launched(launcher: Gtk.FileLauncher, result: Gio.AsyncResult, path: str) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error as exc:
        # Dismissed portal dialogs raise here; nothing useful to surface.
        if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            print(f"pulse: could not open {path}: {exc.message}")
