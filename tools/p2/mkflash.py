#!/usr/bin/env python3
import argparse, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).with_name('lib')))
from flash_layout import validate, FLASH_SIZE
p=argparse.ArgumentParser(); p.add_argument('elf_or_bin'); p.add_argument('-o','--output',default='p2-flash.img'); a=p.parse_args()
data=pathlib.Path(a.elf_or_bin).read_bytes(); validate(image_size=len(data)); out=bytearray([0xff])*FLASH_SIZE; out[:len(data)]=data; pathlib.Path(a.output).write_bytes(out); print(f'DRAFTED flash image {a.output}: boot bytes={len(data)}; boot format HIL-REQUIRED')
