"""Process collector: /proc/<pid>/{stat,statm,io,cmdline,cgroup} with per-pid caches."""
from __future__ import annotations

import os
import pwd
from dataclasses import dataclass, field

from ..desktop_entries import DesktopIndex, app_id_from_cgroup
from ..util import read_text

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

STATE_NAMES = {"R": "Running", "S": "Sleeping", "D": "Disk sleep", "Z": "Zombie", "T": "Stopped",
               "t": "Tracing stop", "X": "Dead", "I": "Idle", "P": "Parked"}


@dataclass
class ProcStatic:
    """Cached per (pid, starttime)."""
    pid: int
    starttime: int
    name: str = ""
    cmdline: str = ""
    exe: str = ""
    user: str = ""
    uid: int = -1
    app_id: str | None = None
    kernel: bool = False
    io_allowed: bool = True


@dataclass
class ProcessSample:
    pid: int
    ppid: int = 0
    name: str = ""
    cmdline: str = ""
    exe: str = ""
    user: str = ""
    uid: int = -1
    state: str = ""
    cpu: float = 0.0              # fraction of total machine capacity, 0..1
    cpu_ticks: int = 0
    rss: int = 0
    shared: int = 0
    threads: int = 0
    nice: int = 0
    read_bps: float = 0.0
    write_bps: float = 0.0
    read_total: int = 0
    write_total: int = 0
    gpu_util: float | None = None
    gpu_mem: int | None = None
    gpu_key: str | None = None
    gpu_kind: str | None = None
    enc_util: float | None = None
    dec_util: float | None = None
    app_id: str | None = None
    kernel: bool = False
    starttime: int = 0

    @property
    def memory(self) -> int:
        return self.rss


@dataclass
class AppSample:
    app_id: str
    name: str
    icon: str | None
    pids: list[int] = field(default_factory=list)
    cpu: float = 0.0
    memory: int = 0
    read_bps: float = 0.0
    write_bps: float = 0.0
    gpu_util: float = 0.0
    gpu_mem: int = 0


def parse_stat_line(text: str) -> tuple[str, list[str]] | None:
    """Split '/proc/pid/stat' into (comm, fields_after_comm). Handles ')' in comm."""
    lp = text.find("(")
    rp = text.rfind(")")
    if lp < 0 or rp < 0 or rp < lp:
        return None
    comm = text[lp + 1:rp]
    rest = text[rp + 2:].split()
    if len(rest) < 22:
        return None
    return comm, rest


class ProcessCollector:
    def __init__(self, ncpu: int, desktop_index: DesktopIndex | None = None):
        self.ncpu = max(1, ncpu)
        self.desktop = desktop_index if desktop_index is not None else DesktopIndex()
        self._static: dict[int, ProcStatic] = {}
        self._prev_cpu: dict[int, tuple[float, int]] = {}
        self._prev_io: dict[int, tuple[float, int, int]] = {}
        self._users: dict[int, str] = {}
        self._last_ts: float | None = None

    def _username(self, uid: int) -> str:
        name = self._users.get(uid)
        if name is None:
            try:
                name = pwd.getpwuid(uid).pw_name
            except KeyError:
                name = str(uid)
            self._users[uid] = name
        return name

    def _static_for(self, pid: int, comm: str, starttime: int, ppid: int) -> ProcStatic:
        st = self._static.get(pid)
        if st is not None and st.starttime == starttime:
            return st
        st = ProcStatic(pid=pid, starttime=starttime)
        raw = read_text(f"/proc/{pid}/cmdline", "") or ""
        args = [a for a in raw.split("\0") if a]
        st.cmdline = " ".join(args)
        try:
            st.exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            st.exe = ""
        st.kernel = not args and (ppid == 2 or pid == 2)
        name = comm
        if args:
            base = os.path.basename(args[0].split(" ")[0]) if args[0] else ""
            # comm is truncated to 15 chars; prefer the real name when it matches
            if base and len(comm) >= 15 and base.startswith(comm):
                name = base
            elif base and st.exe and base == st.exe and base != comm and comm in base:
                name = base
        if st.exe and st.exe.startswith(("python", "node", "java", "perl", "ruby", "wine")) and len(args) > 1 and name == st.exe:
            script = os.path.basename(args[1])
            if script and not script.startswith("-"):
                name = f"{st.exe} {script}"
        st.name = name or f"pid {pid}"
        try:
            st.uid = os.stat(f"/proc/{pid}").st_uid
        except OSError:
            st.uid = -1
        st.user = self._username(st.uid) if st.uid >= 0 else "?"
        cg = read_text(f"/proc/{pid}/cgroup", "") or ""
        st.app_id = app_id_from_cgroup(cg.splitlines()[0]) if cg else None
        self._static[pid] = st
        return st

    def sample(self, ts: float, gpu_samples=None) -> dict[int, ProcessSample]:
        """gpu_samples: {gpu_key: GpuSample} used to attach per-process GPU usage."""
        out: dict[int, ProcessSample] = {}
        try:
            pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            pids = []
        prev_ts = self._last_ts
        alive: set[int] = set()
        for pid in pids:
            stat = read_text(f"/proc/{pid}/stat")
            if not stat:
                continue
            parsed = parse_stat_line(stat)
            if parsed is None:
                continue
            comm, f = parsed
            try:
                state = f[0]
                ppid = int(f[1])
                utime = int(f[11])
                stime = int(f[12])
                nice = int(f[16])
                threads = int(f[17])
                starttime = int(f[19])
                rss = int(f[21]) * PAGE_SIZE
            except (ValueError, IndexError):
                continue
            alive.add(pid)
            st = self._static_for(pid, comm, starttime, ppid)
            ps = ProcessSample(pid=pid, ppid=ppid, name=st.name, cmdline=st.cmdline, exe=st.exe, user=st.user,
                               uid=st.uid, state=STATE_NAMES.get(state, state), rss=rss, threads=threads, nice=nice,
                               app_id=st.app_id, kernel=st.kernel, starttime=starttime)
            ticks = utime + stime
            ps.cpu_ticks = ticks
            prev = self._prev_cpu.get(pid)
            if prev is not None and prev_ts is not None and ts > prev_ts:
                dticks = ticks - prev[1]
                if dticks > 0:
                    ps.cpu = min(1.0, dticks / CLK_TCK / (ts - prev_ts) / self.ncpu)
            self._prev_cpu[pid] = (ts, ticks)

            statm = read_text(f"/proc/{pid}/statm")
            if statm:
                parts = statm.split()
                if len(parts) >= 3:
                    try:
                        ps.shared = int(parts[2]) * PAGE_SIZE
                    except ValueError:
                        pass
            if st.io_allowed:
                io = read_text(f"/proc/{pid}/io")
                if io is None:
                    st.io_allowed = False
                else:
                    rb = wb = 0
                    for line in io.splitlines():
                        if line.startswith("read_bytes:"):
                            rb = int(line[11:])
                        elif line.startswith("write_bytes:"):
                            wb = int(line[12:])
                    ps.read_total, ps.write_total = rb, wb
                    pio = self._prev_io.get(pid)
                    if pio is not None and ts > pio[0]:
                        dt = ts - pio[0]
                        ps.read_bps = max(0.0, (rb - pio[1]) / dt)
                        ps.write_bps = max(0.0, (wb - pio[2]) / dt)
                    self._prev_io[pid] = (ts, rb, wb)
            out[pid] = ps

        # drop caches for dead pids
        for pid in list(self._static):
            if pid not in alive:
                self._static.pop(pid, None)
                self._prev_cpu.pop(pid, None)
                self._prev_io.pop(pid, None)
        self._last_ts = ts

        if gpu_samples:
            for key, gs in gpu_samples.items():
                for pid, gp in gs.processes.items():
                    ps = out.get(pid)
                    if ps is None:
                        continue
                    util = gp.gpu_util or 0.0
                    if ps.gpu_util is None or util > ps.gpu_util:
                        ps.gpu_util = util
                        ps.gpu_key = key
                        ps.gpu_kind = gp.kind
                    ps.gpu_mem = (ps.gpu_mem or 0) + (gp.vram or 0)
                    ps.enc_util = max(ps.enc_util or 0.0, gp.enc_util or 0.0)
                    ps.dec_util = max(ps.dec_util or 0.0, gp.dec_util or 0.0)
        return out

    def group_apps(self, procs: dict[int, ProcessSample]) -> dict[str, AppSample]:
        apps: dict[str, AppSample] = {}
        for ps in procs.values():
            if not ps.app_id:
                continue
            app = apps.get(ps.app_id)
            if app is None:
                entry = self.desktop.lookup_id(ps.app_id)
                if entry is None and ps.exe:
                    entry = self.desktop.lookup_exec(ps.exe)
                name = entry.name if entry else ps.app_id
                icon = entry.icon if entry else None
                app = AppSample(app_id=ps.app_id, name=name, icon=icon)
                apps[ps.app_id] = app
            app.pids.append(ps.pid)
            app.cpu += ps.cpu
            app.memory += ps.rss
            app.read_bps += ps.read_bps
            app.write_bps += ps.write_bps
            app.gpu_util += ps.gpu_util or 0.0
            app.gpu_mem += ps.gpu_mem or 0
        for app in apps.values():
            app.cpu = min(1.0, app.cpu)
            app.gpu_util = min(1.0, app.gpu_util)
        return apps
