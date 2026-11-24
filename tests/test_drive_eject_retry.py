"""drive_eject: a try that fails is made again before the person is told the drive would not eject."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import drive_eject as DE  # noqa: E402
import drive_registry as REG  # noqa: E402


def run_factory(codes, calls):
    it = iter(codes)

    def run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "diskutil":
            return types.SimpleNamespace(returncode=next(it), stdout="", stderr="Volume failed to eject: dissented by PID 123")
        return types.SimpleNamespace(returncode=0, stdout="COMMAND\n", stderr="")
    return run


class EjectRetryTest(unittest.TestCase):
    def setUp(self):
        self.mp, self.dr = REG._mount_point, REG.drive_root
        REG._mount_point = lambda p: "/Volumes/TESLADRIVE"
        REG.drive_root = lambda p: p
        self.gone = DE._gone
        DE._gone = lambda mp: False

    def tearDown(self):
        REG._mount_point, REG.drive_root = self.mp, self.dr
        DE._gone = self.gone

    def test_second_try_works(self):
        calls, naps = [], []
        r = DE.eject("/Volumes/TESLADRIVE/TeslaCam", platform="darwin", run=run_factory([1, 0], calls), which=lambda x: "/usr/bin/" + x, sleep=naps.append)
        self.assertTrue(r["ok"])
        self.assertEqual(calls.count("diskutil"), 2)
        self.assertEqual(len(naps), 1)

    def test_all_tries_fail_is_said_once(self):
        calls = []
        r = DE.eject("/Volumes/TESLADRIVE/TeslaCam", platform="darwin", run=run_factory([1] * 9, calls), which=lambda x: "/usr/bin/" + x, sleep=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertEqual(calls.count("diskutil"), DE.TRIES)
        self.assertIn("after %d tries" % DE.TRIES, r["text"])


if __name__ == "__main__":
    unittest.main()
