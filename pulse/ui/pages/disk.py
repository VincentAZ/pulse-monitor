from __future__ import annotations

from ...util import fmt_bytes, fmt_percent, fmt_rate  # noqa: E402
from ..graph import COLORS, HistoryGraph, Series  # noqa: E402
from ..launch import disks_available, open_folder, open_in_disks  # noqa: E402
from ..udisks import UDisksClient, is_ntfs_dirty_error, ntfsfix_available  # noqa: E402
from ..widgets import GraphBlock, InfoList, PageHeader, StatsGrid  # noqa: E402
from . import Page  # noqa: E402

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class DiskPage(Page):
    def __init__(self, history, settings, points: int, name: str, index: int):
        super().__init__()
        self.key = f"disk.{name}"
        self.name = name
        self.sidebar_title = f"Disk {index} ({name})"
        self.history = history
        self.settings = settings
        self.header = PageHeader(f"Disk {index} ({name})", "")
        self.append(self.header)
        self.udisks = UDisksClient()
        self.volumes: list = []
        self.volume_buttons: list[Gtk.Button] = []
        self.last_dialog = None
        self._busy: set[str] = set()
        self.actions = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.disks_button = Gtk.Button(label="Open in Disks")
        self.disks_button.set_tooltip_text("Format, eject or inspect this drive with GNOME Disks")
        self.disks_button.connect("clicked", self._on_open_disks)
        self.disks_button.set_visible(disks_available())
        self.actions.append(self.disks_button)
        self.append(self.actions)
        self.busy_graph = self.register_graph(HistoryGraph(history, [Series(f"disk.{name}.busy", COLORS["disk"])], 1.0, points))
        self.busy_block = GraphBlock(self.busy_graph, "Active time", "100%", "", "0", height=170)
        self.append(self.busy_block)
        self.rate_graph = self.register_graph(HistoryGraph(
            history, [Series(f"disk.{name}.read", COLORS["disk"]), Series(f"disk.{name}.write", COLORS["disk"], fill=False, dashed=True)],
            None, points, min_auto_max=1024.0 * 64))
        self.rate_block = GraphBlock(self.rate_graph, "Disk transfer rate", "", "Read (solid)  ·  Write (dashed)", "0", height=170)
        self.append(self.rate_block)

        bottom = Adw.WrapBox(child_spacing=24, line_spacing=18)
        self.stats = StatsGrid(columns=3)
        self.stats.add("busy", "Active time")
        self.stats.add("resp", "Average response time")
        self.stats.add("inflight", "Queue depth")
        self.stats.add("read", "Read speed")
        self.stats.add("write", "Write speed")
        self.stats.add("iops", "IOPS")
        self.stats.add("tread", "Total read")
        self.stats.add("twrite", "Total written")
        self.stats.set_valign(Gtk.Align.START)
        bottom.append(self.stats)
        self.info = InfoList()
        self.info.add("capacity", "Capacity")
        self.info.add("formatted", "Formatted")
        self.info.add("used", "Used")
        self.info.add("system", "System disk")
        self.info.add("type", "Type")
        self.info.add("mounts", "Mount points")
        self.info.set_valign(Gtk.Align.START)
        bottom.append(self.info)
        self.append(bottom)

    def update(self, snap) -> None:
        info = snap.disk_infos.get(self.name)
        d = snap.disks.get(self.name)
        secs = self.settings.get("history_seconds")
        self.busy_block.left.set_label(f"Active time over {secs} seconds")
        self.rate_block.left.set_label(f"Disk transfer rate over {secs} seconds")
        if info:
            self.header.subtitle.set_label(info.model)
            self.info.set("capacity", fmt_bytes(info.capacity))
            self.info.set("formatted", fmt_bytes(info.formatted) if info.mounts else "Not mounted")
            self.info.set("used", f"{fmt_bytes(info.used)} ({fmt_percent(info.used / info.formatted)})" if info.formatted else "—")
            self.info.set("system", "Yes" if info.is_system else "No")
            self.info.set("type", info.kind)
            self.info.set("mounts", "\n".join(f"{m.mountpoint} ({m.fstype})" for m in info.mounts) or "—")
            self._set_volumes(info.volumes, info.is_system)
        if d:
            self.stats.set("busy", fmt_percent(d.busy))
            self.stats.set("resp", f"{d.response_ms:.2f} ms" if d.response_ms is not None else "—")
            self.stats.set("inflight", str(d.in_flight))
            self.stats.set("read", fmt_rate(d.read_bps))
            self.stats.set("write", fmt_rate(d.write_bps))
            self.stats.set("iops", f"{d.reads_ps + d.writes_ps:.0f}")
            self.stats.set("tread", fmt_bytes(d.total_read))
            self.stats.set("twrite", fmt_bytes(d.total_written))
        self.refresh_graphs()
        self.rate_block.right.set_label(fmt_rate(self.rate_graph.current_max))

    # -- hand-offs to other apps ---------------------------------------------------------------

    def _set_volumes(self, volumes: list, is_system: bool) -> None:
        """One Mount / Open in Files (+ Unmount) button per volume, rebuilt only when they change."""
        key = [(v.device, v.fstype, v.label, v.mountpoint) for v in volumes]
        if key == self.volumes:
            return
        self.volumes = key
        for b in self.volume_buttons:
            self.actions.remove(b)
        self.volume_buttons = []
        many = len(volumes) > 1
        for vol in volumes:
            name = vol.display_name
            if vol.mounted:
                btn = Gtk.Button(label=f"Open {name}" if many else "Open in Files")
                btn.set_tooltip_text(f"Browse {vol.mountpoint} in the file manager")
                btn.connect("clicked", lambda _b, path=vol.mountpoint: self._open_mount(path))
                btn.volume, btn.action = vol, "open"
                self._add_volume_button(btn)
                if not is_system and vol.mountpoint != "/":
                    btn = Gtk.Button(label=f"Unmount {name}" if many else "Unmount")
                    btn.set_tooltip_text(f"Unmount {vol.mountpoint} so the drive can be removed safely")
                    btn.connect("clicked", lambda _b, v=vol: self._unmount(v))
                    btn.volume, btn.action = vol, "unmount"
                    self._add_volume_button(btn)
            else:
                btn = Gtk.Button(label=f"Mount {name}" if many else f"Mount ({vol.fstype})")
                btn.add_css_class("suggested-action")
                btn.set_tooltip_text(f"Mount /dev/{vol.device} ({vol.fstype}) so its files can be browsed")
                btn.connect("clicked", lambda _b, v=vol: self._mount(v))
                btn.volume, btn.action = vol, "mount"
                self._add_volume_button(btn)

    def _add_volume_button(self, btn: Gtk.Button) -> None:
        btn.set_sensitive(btn.volume.device not in self._busy)
        self.actions.append(btn)
        self.volume_buttons.append(btn)

    def _set_busy(self, device: str, busy: bool) -> None:
        if busy:
            self._busy.add(device)
        else:
            self._busy.discard(device)
        for b in self.volume_buttons:
            if b.volume.device == device:
                b.set_sensitive(not busy)

    def _rediscover(self) -> None:
        """Ask the sampler to re-read mounts right away instead of waiting for the next scan."""
        root = self.get_root()
        sampler = getattr(root, "sampler", None)
        if sampler is not None:
            disk = getattr(sampler, "disk", None)
            if disk is not None and hasattr(disk, "request_discovery"):
                disk.request_discovery()
            if hasattr(sampler, "request_sample"):
                sampler.request_sample()

    # -- mounting -------------------------------------------------------------------------------

    def _mount(self, vol) -> None:
        self._set_busy(vol.device, True)

        def done(err, mountpoint):
            self._set_busy(vol.device, False)
            if err:
                self._show_mount_error(vol, err)
                return
            self._toast(f"Mounted {vol.display_name} at {mountpoint}")
            self._rediscover()

        self.udisks.mount(vol.device, done)

    def _unmount(self, vol) -> None:
        self._set_busy(vol.device, True)

        def done(err, _mp):
            self._set_busy(vol.device, False)
            if err:
                self._toast(f"Could not unmount {vol.display_name}: {err}")
                return
            self._toast(f"Unmounted {vol.display_name}")
            self._rediscover()

        self.udisks.unmount(vol.device, done)

    def _show_mount_error(self, vol, err: str) -> None:
        dirty = is_ntfs_dirty_error(vol.fstype, err)
        dialog = Adw.AlertDialog(heading=f"Could not mount {vol.display_name}")
        if dirty:
            body = (f"/dev/{vol.device} is an NTFS volume that Windows did not shut down or eject cleanly, "
                    "so Linux refuses to mount it until its \u201cdirty\u201d flag is cleared.\n\n"
                    "The safe fix is to plug it into Windows, run chkdsk, and eject it properly. "
                    "Pulse can instead clear the flag now with ntfsfix (you will be asked for your password). "
                    "That makes the files readable but does not repair any damage.\n\n"
                    f"Error: {err}")
        else:
            body = f"/dev/{vol.device} ({vol.fstype})\n\n{err}"
        dialog.set_body(body)
        dialog.add_response("cancel", "Cancel")
        if dirty and ntfsfix_available():
            dialog.add_response("fix", "Clear Flag and Mount")
            dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("fix")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_mount_error_response, vol)
        self.last_dialog = dialog
        root = self.get_root()
        dialog.present(root if isinstance(root, Gtk.Widget) else None)

    def _on_mount_error_response(self, _dialog, response: str, vol) -> None:
        if response != "fix":
            return
        self._set_busy(vol.device, True)

        def fixed(err, _):
            self._set_busy(vol.device, False)
            if err:
                self._toast(err)
                return
            self._toast(f"Cleared the dirty flag on /dev/{vol.device}; mounting\u2026")
            self._mount(vol)

        self.udisks.clear_ntfs_dirty(vol.device, fixed)

    def _toast(self, text: str) -> None:
        root = self.get_root()
        if root is not None and hasattr(root, "toast"):
            root.toast(text)
        else:
            print(f"pulse: {text}")

    def _on_open_disks(self, *_args) -> None:
        err = open_in_disks(self.name)
        if err:
            self._toast(err)

    def _open_mount(self, path: str) -> None:
        err = open_folder(path, self.get_root() if isinstance(self.get_root(), Gtk.Window) else None)
        if err:
            self._toast(err)
