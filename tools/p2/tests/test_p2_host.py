import pathlib, sys, unittest, subprocess, os, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'lib'))
from flash_layout import validate
from hil_parse import classify_log
sys.path.insert(0, str(pathlib.Path(__file__).parents[3] / 'arch/p2/src/common'))
from p2_hostlogic import baud_ticks, tick_cycles, counter_delta
class P2HostTests(unittest.TestCase):
    def test_flash_layout(self): self.assertTrue(validate(image_size=1024)); self.assertRaises(ValueError, validate, [('a',0,0x2000,0),('b',0x1000,0x1000,0)])
    def test_hub_overflow(self): self.assertRaises(ValueError, validate, image_size=1024*1024)
    def test_clock(self): self.assertEqual(baud_ticks(180000000,230400),781); self.assertEqual(baud_ticks(180000000,2000000),90); self.assertEqual(tick_cycles(180000000,100),1800000); self.assertEqual(counter_delta(1,0xffffffff),2)
    def test_hil_parse(self): self.assertEqual(classify_log('boot OK','OK'),'success'); self.assertEqual(classify_log('PANIC','OK'),'panic'); self.assertEqual(classify_log('','OK'),'timeout')
    def test_flash_refuses_default(self):
        r=subprocess.run(['bash','tools/p2/flash.sh'], cwd=pathlib.Path(__file__).parents[3], text=True, capture_output=True)
        self.assertEqual(r.returncode,2); self.assertIn('HIL REQUIRED', r.stdout)
if __name__=='__main__': unittest.main()
