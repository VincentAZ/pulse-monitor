"""Mount and unmount volumes through udisks2, the same D-Bus service Files and GNOME Disks use.

udisks applies the system's polkit policy, so a removable drive mounts without a password while
system disks ask for one. Pulse never touches block devices itself. The one exception is the
NTFS "dirty flag" repair, which runs ntfsfix through pkexec (a GUI password prompt) because the
kernel ntfs3 driver refuses to mount a volume Windows did not eject cleanly.
"""
from __future__ import annotations

import shutil
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

UDISKS_NAME = "org.freedesktop.UDisks2"
UDISKS_PATH = "/org/freedesktop/UDisks2/block_devices/"
FS_IFACE = "org.freedesktop.UDisks2.Filesystem"

# udisks error strings that mean "the kernel ntfs3 driver refused a dirty NTFS volume".
_NTFS_DIRTY_HINTS = ("bad superblock", "wrong fs type", "dirty", "force", "chkdsk")

Callback = Callable[[str | None, str], None]  # (error message or None, mount point or "")


def block_path(device: str) -> str:
    """D-Bus object path for a block device name such as sda1 (udisks escapes non-alnum chars)."""
    name = device[5:] if device.startswith("/dev/") else device
    safe = "".join(c if c.isalnum() else f"_{ord(c):02x}" for c in name)
    return UDISKS_PATH + safe


def is_ntfs_dirty_error(fstype: str, message: str) -> bool:
    """True when a mount failure looks like the classic unclean-Windows-eject NTFS refusal."""
    if fstype.lower() != "ntfs":
        return False
    low = message.lower()
    return any(h in low for h in _NTFS_DIRTY_HINTS)


def ntfsfix_available() -> bool:
    return shutil.which("ntfsfix") is not None and shutil.which("pkexec") is not None


def ntfsfix_command(device: str) -> list[str]:
    path = device if device.startswith("/dev/") else f"/dev/{device}"
    return ["pkexec", "ntfsfix", "-d", path]


def _clean_error(exc: GLib.Error) -> str:
    msg = exc.message or str(exc)
    # "GDBus.Error:org.freedesktop.UDisks2.Error.Failed: Error mounting ..." -> "Error mounting ..."
    if msg.startswith("GDBus.Error:"):
        msg = msg.split(":", 2)[-1].strip()
    return msg


class UDisksClient:
    """Thin async wrapper; every method reports back on the GTK main loop."""

    def __init__(self, bus: Gio.DBusConnection | None = None):
        self._bus = bus

    def _connection(self) -> Gio.DBusConnection:
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._bus

    def _call(self, device: str, method: str, callback: Callback) -> None:
        try:
            bus = self._connection()
        except GLib.Error as exc:
            callback(f"Could not reach udisks: {_clean_error(exc)}", "")
            return
        options = GLib.Variant("(a{sv})", ({},))

        def done(conn, result):
            try:
                reply = conn.call_finish(result)
            except GLib.Error as exc:
                callback(_clean_error(exc), "")
                return
            callback(None, reply.unpack()[0] if reply and reply.n_children() else "")

        bus.call(UDISKS_NAME, block_path(device), FS_IFACE, method, options, None,
                 Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 60000, None, done)

    def mount(self, device: str, callback: Callback) -> None:
        self._call(device, "Mount", callback)

    def unmount(self, device: str, callback: Callback) -> None:
        self._call(device, "Unmount", callback)

    def clear_ntfs_dirty(self, device: str, callback: Callback) -> None:
        """Run `pkexec ntfsfix -d /dev/X`; the desktop's polkit agent asks for the password."""
        if not ntfsfix_available():
            callback("ntfsfix or pkexec is not installed", "")
            return
        try:
            proc = Gio.Subprocess.new(ntfsfix_command(device),
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            callback(f"Could not start ntfsfix: {_clean_error(exc)}", "")
            return

        def done(p, result):
            try:
                _ok, out, err = p.communicate_utf8_finish(result)
            except GLib.Error as exc:
                callback(f"ntfsfix failed: {_clean_error(exc)}", "")
                return
            if p.get_successful():
                callback(None, "")
            else:
                status = p.get_exit_status() if p.get_if_exited() else -1
                text = (err or out or "").strip().splitlines()
                detail = text[-1] if text else f"exit status {status}"
                if status in (126, 127):
                    detail = "authentication was cancelled"
                callback(f"ntfsfix failed: {detail}", "")

        proc.communicate_utf8_async(None, None, done)
