from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ...util import fmt_bytes, fmt_freq, fmt_percent, fmt_rate, fmt_temp, fmt_watts  # noqa: E402
from ..graph import COLORS, HistoryGraph, Series  # noqa: E402
from ..widgets import GraphBlock, InfoList, PageHeader, StatsGrid  # noqa: E402
from . import Page  # noqa: E402


class GpuPage(Page):
    def __init__(self, history, settings, points: int, gpu_key: str, index: int, info):
        super().__init__()
        self.key = f"gpu.{gpu_key}"
        self.gpu_key = gpu_key
        self.sidebar_title = f"GPU {index}"
        self.history = history
        self.settings = settings
        self.header = PageHeader(f"GPU {index}", info.name)
        self.append(self.header)
        k = gpu_key
        self.util_graph = self.register_graph(HistoryGraph(history, [Series(f"gpu.{k}.util", COLORS["gpu"])], 1.0, points))
        self.util_block = GraphBlock(self.util_graph, "Overall utilization", "100%", "", "0", height=170)
        self.append(self.util_block)
        row = Adw.WrapBox(child_spacing=18, line_spacing=12, justify=Adw.JustifyMode.FILL)
        self.codec_graph = self.register_graph(HistoryGraph(
            history, [Series(f"gpu.{k}.enc", COLORS["enc"]), Series(f"gpu.{k}.dec", COLORS["dec"], fill=False, dashed=True)], 1.0, points))
        self.codec_block = GraphBlock(self.codec_graph, "Video encode / decode", "100%", "Encode (solid) · Decode (dashed)", "0", height=130)
        self.codec_block.set_size_request(340, -1)
        row.append(self.codec_block)
        self.mem_graph = self.register_graph(HistoryGraph(history, [Series(f"gpu.{k}.vram", COLORS["gpu2"])], 1.0, points))
        self.mem_block = GraphBlock(self.mem_graph, "Memory usage", fmt_bytes(info.vram_total) if info.vram_total else "", "", "0", height=130)
        self.mem_block.set_size_request(340, -1)
        row.append(self.mem_block)
        self.append(row)

        bottom = Adw.WrapBox(child_spacing=24, line_spacing=18)
        self.stats = StatsGrid(columns=3, tile_width=152)
        self.stats.add("util", "Utilization")
        self.stats.add("clock", "Clock speed")
        self.stats.add("mclock", "Memory speed")
        self.stats.add("power", "Power draw")
        self.stats.add("temp", "Temperature")
        self.stats.add("fan", "Fan")
        self.stats.add("vram", "Memory usage")
        self.stats.add("shared", "Shared memory")
        self.stats.add("memutil", "Memory controller")
        self.stats.add("enc", "Encode / Decode")
        self.stats.add("pcie", "PCIe link")
        self.stats.add("pcie_rate", "PCIe throughput")
        self.stats.add("procs", "Processes")
        self.stats.set_valign(Gtk.Align.START)
        bottom.append(self.stats)
        self.info = InfoList()
        self.info.add("vendor", "Vendor")
        self.info.add("driver", "Driver")
        self.info.add("bus", "Bus")
        self.info.add("vram_total", "Dedicated memory")
        self.info.add("maxclock", "Max clock")
        self.info.add("maxmclock", "Max memory clock")
        self.info.add("plimit", "Power limit")
        self.info.add("pcie_max", "PCIe capability")
        self.info.add("extra1", "VBIOS")
        self.info.add("extra2", "CUDA")
        self.info.add("extra3", "Cores")
        self.info.add("pstate", "Performance state")
        self.info.set_valign(Gtk.Align.START)
        bottom.append(self.info)
        self.append(bottom)
        self._fill_static(info)

    def _fill_static(self, info) -> None:
        self.info.set("vendor", info.vendor)
        drv = info.driver + (f" {info.driver_version}" if info.driver_version else "")
        self.info.set("driver", drv)
        self.info.set("bus", info.key + (f" ({info.drm_card})" if info.drm_card else ""))
        self.info.set("vram_total", fmt_bytes(info.vram_total) if info.vram_total else "—")
        self.info.set("maxclock", fmt_freq(info.clock_max_mhz) if info.clock_max_mhz else "—")
        self.info.set("maxmclock", fmt_freq(info.mem_clock_max_mhz) if info.mem_clock_max_mhz else "—")
        self.info.set("plimit", fmt_watts(info.power_limit_w, 0) if info.power_limit_w else "—")
        pm = f"Gen {info.pcie_gen_max} x{info.pcie_width_max}" if info.pcie_gen_max and info.pcie_width_max else "—"
        self.info.set("pcie_max", pm)
        ex = info.extra or {}
        self.info.set("extra1", ex.get("vbios") or "—")
        self.info.set("extra2", ex.get("cuda") or "—")
        cores = ex.get("cores")
        cc = ex.get("compute_capability")
        self.info.set("extra3", (f"{cores:,}" if cores else "—") + (f"  (CC {cc})" if cc else ""))

    def update(self, snap) -> None:
        g = snap.gpus.get(self.gpu_key)
        secs = self.settings.get("history_seconds")
        self.util_block.left.set_label(f"Overall utilization over {secs} seconds")
        if g is None:
            return
        self.stats.set("util", fmt_percent(g.util))
        self.stats.set("clock", fmt_freq(g.clock_mhz) if g.clock_mhz else "—")
        self.stats.set("mclock", fmt_freq(g.mem_clock_mhz) if g.mem_clock_mhz else "—")
        if g.power_w is None:
            p = "—"
        elif g.power_limit_w:
            p = f"{g.power_w:.0f} / {g.power_limit_w:.0f} W"
        else:
            p = fmt_watts(g.power_w)
        self.stats.set("power", p)
        t = fmt_temp(g.temp_c)
        if g.hotspot_c:
            t += f"  (hot spot {fmt_temp(g.hotspot_c)})"
        self.stats.set("temp", t)
        if g.fan_pct is not None:
            fan = f"{g.fan_pct:.0f}%" + (f" ({g.fan_rpm:.0f} rpm)" if g.fan_rpm else "")
        elif g.fan_rpm is not None:
            fan = f"{g.fan_rpm:.0f} rpm"
        else:
            fan = "—"
        self.stats.set("fan", fan)
        if g.vram_used is not None and g.vram_total:
            self.stats.set("vram", f"{fmt_bytes(g.vram_used)} / {fmt_bytes(g.vram_total)}")
            self.mem_block.footer_left.set_label(fmt_percent(g.vram_fraction))
        else:
            self.stats.set("vram", "—")
        if g.shared_used is not None:
            self.stats.set("shared", f"{fmt_bytes(g.shared_used)}" + (f" / {fmt_bytes(g.shared_total)}" if g.shared_total else ""))
        else:
            self.stats.set("shared", "—")
        self.stats.set("memutil", fmt_percent(g.mem_util) if g.mem_util is not None else "—")
        self.stats.set("enc", f"{fmt_percent(g.enc_util)} / {fmt_percent(g.dec_util)}")
        self.stats.set("pcie", f"Gen {g.pcie_gen} x{g.pcie_width}" if g.pcie_gen and g.pcie_width else "—")
        if g.pcie_tx_bps is not None:
            self.stats.set("pcie_rate", f"↑ {fmt_rate(g.pcie_tx_bps, 0)}  ↓ {fmt_rate(g.pcie_rx_bps, 0)}")
        else:
            self.stats.set("pcie_rate", "—")
        self.stats.set("procs", str(len(g.processes)))
        self.info.set("pstate", g.pstate or "—")
        self.refresh_graphs()
