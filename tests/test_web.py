import shutil
import subprocess
import unittest
from pathlib import Path


class WebRegressionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser-script regression tests')
    def test_live_updates_and_navigation_races(self):
        result=subprocess.run(['node','tests/web_live.cjs'],cwd=Path(__file__).resolve().parent.parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
