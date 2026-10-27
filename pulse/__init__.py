"""Pulse - a system and GPU monitor for Linux.

Combines a Mission Center style performance overview (CPU, memory, disks,
network, GPUs, processes, apps) with an nvtop style per-process GPU view.
Everything is read directly from /proc, /sys, DRM fdinfo and NVML.
"""

__version__ = "0.1.0"
APP_ID = "org.pulse.Monitor"
APP_NAME = "Pulse"
