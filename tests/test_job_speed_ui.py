"""A move or delete shows its speed and the time left, worked out from the bytes done in the last minute and a half."""
import os, re, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class JobSpeedTest(unittest.TestCase):
    def test_shown_on_move_delete_and_purge(self):
        self.assertEqual(JS.count("(${q}%)${jobRate(j)}`"), 2)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_rate_and_time_left(self):
        m = re.search(r"  const JBR=.*?(?=\n  const jobPaint)", JS, re.S)
        code = "const dur=m=>Math.round(m)+' min';let T=0;Date.now=()=>T*1000;" + m.group(0) + """
const j={action:'move',role:'J',to:'L',bytes:56e9,bytes_done:10e9};
const out=[];out.push(jobRate(j));T=20;j.bytes_done=14e9;out.push(jobRate(j));console.log(JSON.stringify(out));"""
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        first, second = __import__("json").loads(r.stdout)
        self.assertEqual(first, "")                                  # nothing to measure from yet
        self.assertIn("200 MB/s", second)                            # 4 GB in 20 s
        self.assertIn("about 4 min left", second)                    # 42 GB at 200 MB/s = 210 s, 3.5 min


if __name__ == "__main__":
    unittest.main()
