#!/usr/bin/env python3
"""RAM-load NuttX and require its ordered early-startup markers."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hil


raise SystemExit(hil.main([*sys.argv[1:], "--protocol", "boot"]))
