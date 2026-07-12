#!/usr/bin/env python3
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).with_name('lib')))
from flash_layout import validate
validate(); print('HOST-TESTED: flash layout validates')
