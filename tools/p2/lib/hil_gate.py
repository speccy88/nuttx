import argparse
import os
import sys

HIL_REQUIRED = 'HIL REQUIRED: no physical P2 target is available in this environment'


def require_hil(args, destructive=False, sd_destructive=False):
    if os.getenv('P2_HIL', '0') != '1':
        return False, HIL_REQUIRED
    if not getattr(args, 'execute', False):
        return False, HIL_REQUIRED
    if not (getattr(args, 'port', '') or os.getenv('P2_PORT', '')):
        return False, HIL_REQUIRED
    if destructive and os.getenv('P2_ALLOW_FLASH_WRITE', '0') != '1':
        return False, 'Refusing flash write: set P2_ALLOW_FLASH_WRITE=1 and pass --execute with --port'
    if sd_destructive and os.getenv('P2_ALLOW_SD_DESTRUCTIVE', '0') != '1':
        return False, 'Refusing destructive SD action: set P2_ALLOW_SD_DESTRUCTIVE=1'
    return True, 'DRAFTED: HIL gate passed; hardware implementation is local-only'


def main(kind='generic'):
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--port', default=os.getenv('P2_PORT', ''))
    args = parser.parse_args()
    ok, msg = require_hil(args, destructive=(kind == 'flash'), sd_destructive=(kind == 'sd'))
    print(msg)
    return 0 if ok else 2
