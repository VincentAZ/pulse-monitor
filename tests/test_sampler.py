"""Sampler thread + history buffers."""
import threading
import time
import unittest

from . import helpers  # noqa: F401
from pulse.sampler import History, Sampler


class HistoryTests(unittest.TestCase):
    def test_ring_and_capacity_change(self):
        h = History(3)
        for i in range(5):
            h.push("a", i)
        self.assertEqual(h.get("a"), [2.0, 3.0, 4.0])
        self.assertEqual(h.last("a"), 4.0)
        self.assertEqual(h.last("missing", -1), -1)
        h.set_capacity(2)
        self.assertEqual(h.get("a"), [3.0, 4.0])
        h.set_capacity(10)
        h.push("a", None)
        self.assertEqual(h.get("a"), [3.0, 4.0, 0.0])
        h.drop("a")
        self.assertEqual(h.get("a"), [])

    def test_thread_safety(self):
        h = History(100)
        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                h.push("x", i)
                i += 1

        t = threading.Thread(target=writer)
        t.start()
        for _ in range(2000):
            data = h.get("x")
            self.assertLessEqual(len(data), 100)
        stop.set()
        t.join()


class SamplerTests(unittest.TestCase):
    def test_sample_once_structure(self):
        s = Sampler(interval_s=0.2, history_points=5, enable_gpu=True)
        snap = s.sample_once()
        self.assertEqual(snap.seq, 1)
        self.assertEqual(snap.errors, [])
        self.assertGreater(snap.memory.total, 0)
        self.assertTrue(snap.processes)
        self.assertEqual(set(snap.disks), set(snap.disk_infos))
        self.assertEqual(set(snap.nets), set(snap.net_infos))
        self.assertEqual(set(snap.gpus), set(snap.gpu_infos))
        self.assertIn("cpu.util", s.history.keys())
        self.assertLess(snap.sample_ms, 2000)
        s.stop()

    def test_thread_delivers_snapshots(self):
        got = []
        done = threading.Event()

        def cb(snap):
            got.append(snap)
            if len(got) >= 3:
                done.set()

        s = Sampler(interval_s=0.2, history_points=4, callback=cb, enable_gpu=False)
        s.start()
        self.assertTrue(done.wait(5.0), "sampler did not deliver 3 snapshots in time")
        s.stop()
        self.assertGreaterEqual(len(got), 3)
        self.assertEqual([x.seq for x in got[:3]], [1, 2, 3])
        self.assertLessEqual(len(s.history.get("cpu.util")), 4)
        # cadence roughly matches the interval
        dt = got[2].ts - got[0].ts
        self.assertGreater(dt, 0.25)
        self.assertLess(dt, 2.0)

    def test_interval_change_wakes_thread(self):
        s = Sampler(interval_s=5.0, history_points=4, enable_gpu=False, enable_processes=False)
        s.start()
        time.sleep(0.3)
        first = s.latest.seq if s.latest else 0
        s.set_interval(0.1)
        time.sleep(0.8)
        s.stop()
        self.assertGreater(s.latest.seq, first + 2)

    def test_collector_failure_is_isolated(self):
        import logging
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        s = Sampler(interval_s=0.2, history_points=4, enable_gpu=False)

        class Broken:
            def sample(self, ts):
                raise RuntimeError("boom")
        s.disk = Broken()
        s.disk.disks = {}
        snap = s.sample_once()
        self.assertTrue(any("disk" in e for e in snap.errors))
        self.assertGreater(snap.memory.total, 0)   # other collectors still ran


if __name__ == "__main__":
    unittest.main()
