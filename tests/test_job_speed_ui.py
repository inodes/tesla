"""A move or delete shows its speed and the time left, worked out from the bytes done in the last minute and a half."""
import os, re, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class JobSpeedTest(unittest.TestCase):
    def test_shown_on_move_delete_and_purge(self):
        self.assertEqual(JS.count("(${q}%)${jobRate(j)}"), 2)                 # the purge line and the move / delete line

    def test_the_header_counts_only_what_is_copied(self):
        self.assertIn('${n0(j.confirmed||0)} of ${n0(fl)} files ${j.action==="move"?"moved and checked":"deleted"} · ${fmtGB(bd)} of ${fmtGB(bt)} (${q}%)', JS)
        self.assertIn("j.files-(j.skip_files||0)", JS)
        self.assertIn("j.bytes_done-(j.skipped_bytes||0)", JS)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_rate_and_time_left(self):
        m = re.search(r"  const JBR=.*?(?=\n  const jobPaint)", JS, re.S)
        code = "const dur=m=>Math.round(m)+' min';let T=0;Date.now=()=>T*1000;" + m.group(0) + """
const j={action:'move',role:'J',to:'L',bytes:56e9,bytes_done:10e9};
const out=[];out.push(jobRate(j));T=20;j.bytes_done=14e9;out.push(jobRate(j));
const k={action:'move',role:'J',to:'L',bytes:390e9,bytes_done:0,skipped_bytes:0};T=100;out.push(jobRate(k));      // 101 GB skipped in an instant, then 500 MB/s of copying
k.bytes_done=101e9;k.skipped_bytes=101e9;out.push(jobRate(k));T=120;k.bytes_done=111e9;out.push(jobRate(k));
console.log(JSON.stringify(out));"""
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        first, second, _s0, _s1, third = __import__("json").loads(r.stdout)
        self.assertEqual(first, "")                                  # nothing to measure from yet
        self.assertIn("200 MB/s", second)                            # 4 GB in 20 s
        self.assertIn("about 4 min left", second)                    # 42 GB at 200 MB/s = 210 s, 3.5 min
        self.assertIn("500 MB/s", third)                             # the skipped 101 GB do not count as copied: 10 GB in 20 s
        self.assertIn("about 9 min left", third)                     # 279 GB at 500 MB/s


if __name__ == "__main__":
    unittest.main()
