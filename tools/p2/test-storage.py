#!/usr/bin/env python3
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).with_name('lib')))
from hil_gate import main
raise SystemExit(main('sd'))
