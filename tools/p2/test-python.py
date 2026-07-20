#!/usr/bin/env python3
"""RAM-load NuttX, upload its P2 Python container, and run HIL checks.

The default is a read-only dry run.  Serial access, reset, RAM loading, and
reserved-PSRAM writes require ``--execute`` plus the standard P2 HIL safety
environment.  A successful run preserves machine-readable evidence for the
exact resident image, container, loader command, upload, and Python checks.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import binascii
import dataclasses
import datetime
import fcntl
import hashlib
import json
import math
import os
import pathlib
import re
import select
import shlex
import stat
import struct
import subprocess
import sys
import time
from typing import BinaryIO, Callable, Iterable, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import p2_python_container  # noqa: E402
import p2_python_package  # noqa: E402

UPLOAD_MAGIC = b"P2PYUPL\x00"
UPLOAD_PROTOCOL = 3
UPLOAD_HEADER = struct.Struct("<8sHHIII")
UPLOAD_FRAME = struct.Struct("<III")
UPLOAD_ACK = struct.Struct("<4sI")
UPLOAD_ACK_MAGIC = b"P2AK"
UPLOAD_NACK_MAGIC = b"P2NK"
UPLOAD_FRAME_SIZE = 65536
UPLOAD_FRAME_RETRIES = 3
UPLOAD_FAULT_BAD_CRC = "bad_crc"
UPLOAD_FAULT_BAD_OFFSET = "bad_offset"
UPLOAD_FAULT_BAD_FINAL_SIZE = "bad_final_size"
UPLOAD_FAULT_SEQUENCE = (
    UPLOAD_FAULT_BAD_CRC,
    UPLOAD_FAULT_BAD_OFFSET,
    UPLOAD_FAULT_BAD_FINAL_SIZE,
)
# Protocol v3 keeps one logical block in flight, but expands its payload to
# 64 KiB so a full CPython container no longer pays one host/target handshake
# per kilobyte.  Before it emits ACCEPT, the target pauses serial upper-half
# promotion and makes scheduler cog 0 the direct consumer of the existing
# 1024-byte Smart Pin SPSC ring.  The payload is streamed into the unpublished
# 90112-byte overlay execution slot, validated, committed to PSRAM, and only
# then ACKed.  Host writes remain bounded to the lower-ring size and have no
# artificial quiet gaps.

UPLOAD_WINDOW_FRAMES = 1
UPLOAD_PROGRESS_INTERVAL = 1024 * 1024
MAX_UART_WRITE = 1024
UPLOAD_WIRE_CHUNK_SIZE = 1024
UPLOAD_CHUNK_GAP_SECONDS = 0.0
RUNTIME_BAUD = 2000000
UART_BITS_PER_BYTE = 10
UPLOAD_CHUNK_WIRE_SECONDS = (
    UPLOAD_WIRE_CHUNK_SIZE * UART_BITS_PER_BYTE / RUNTIME_BAUD
)
UPLOAD_CHUNK_PAUSE_SECONDS = 0.0
CONTAINER_BASE = 0x10300000
CONTAINER_CAPACITY = 13 * 1024 * 1024
LINE_MAX = 256
DEFAULT_LOCK = pathlib.Path("/tmp/nuttx-p2-python-hil.lock")
LIVE_EVIDENCE_FORMAT = "p2-python-serial-progress-v1"
LIVE_EVIDENCE_SYNC_SECONDS = 5.0
FAILURE_DRAIN_QUIET_SECONDS = 1.0
FAILURE_DRAIN_PROGRESS_SECONDS = 1.0
FAILURE_DRAIN_SCAN_BYTES = 256
FAILURE_DRAIN_TERMINAL_MARKERS = (b"nsh> ",)


class PythonHilError(RuntimeError):
    """A fail-closed configuration, transport, or target failure."""


@dataclasses.dataclass(frozen=True)
class PythonTest:
    name: str
    marker: str
    command: str
    setup_commands: Tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class PythonTestWorker:
    """One CPython lifecycle that may prove one or more named tests."""

    name: str
    tests: Tuple[PythonTest, ...]
    command: str
    setup_commands: Tuple[str, ...] = ()


LOCK_ONLY_TEST_PATH = "/tmp/p2-lock-only.py"
LOCK_ONLY_TEST_SCRIPT = (
    "import _thread as t,_imp,importlib._bootstrap as b",
    "import functools,_pyio,_strptime,reprlib,tempfile",
    'CE="thread creation is unavailable: this NuttX P2 profile supports one Python task and only lock-only _thread compatibility"',
    'TE="threading is unavailable: this NuttX P2 profile supports one Python task and only lock-only _thread compatibility"',
    "def expect(exc,fn):",
    " try: fn()",
    " except exc as caught: return str(caught)",
    ' raise AssertionError("expected exception")',
    'assert _imp.is_builtin("_thread")==0 and t.__spec__.origin=="/usr/local/lib/python313.zip/_thread.pyc"',
    'assert t._NUTTX_LOCK_ONLY is True and b._thread is None;m=b._get_module_lock("p2-lock-only")',
    'assert type(m).__name__=="_DummyModuleLock" and m.acquire() and m.acquire();m.release();m.release()',
    "lock=t.allocate();assert type(lock) is t.LockType and t.allocate is t.allocate_lock",
    "assert lock.acquire() and lock.locked() and not lock.acquire(False)",
    'assert expect(RuntimeError,lock.acquire)=="lock-only _thread lock would block the only Python task";lock.release();assert expect(RuntimeError,lock.release)=="release unlocked lock"',
    "with lock: assert lock.locked()",
    "assert not lock.locked()",
    "rlock=t.RLock();assert rlock.acquire() and rlock.acquire(False)",
    'assert rlock.locked() and rlock._recursion_count()==2;rlock.release();rlock.release();assert not rlock.locked();assert expect(RuntimeError,rlock.release)=="cannot release un-acquired lock"',
    "ident=t.get_ident();assert ident!=0 and ident==t.get_ident();called=[];callback=lambda:called.append(1)",
    "for starter,args in ((t.start_new_thread,(callback,())),(t.start_new,(callback,())),(t.start_joinable_thread,(callback,))):",
    " assert expect(NotImplementedError,lambda:starter(*args))==CE",
    "assert not called",
    "cached=functools.lru_cache(maxsize=2)(lambda value:value+1);assert cached(2)==cached(2)==3 and cached.cache_info().hits==1",
    'raw=_pyio.BytesIO(b"abc");assert _pyio.BufferedReader(raw).read()==b"abc";assert _strptime._strptime_time("2026","%Y").tm_year==2026',
    "class Recursive: pass",
    "@reprlib.recursive_repr()",
    "def recursive(self): return recursive(self)",
    'Recursive.__repr__=recursive;assert repr(Recursive())=="...";assert tempfile.gettempdir()=="/tmp"',
    'assert expect(ImportError,lambda:__import__("threading"))==TE;print("P2PY"+"TEST:LOCK_ONLY:PASS")',
)
LOCK_ONLY_TEST_SETUP_COMMANDS = tuple(
    "echo '{}' {}{}".format(
        line,
        ">" if index == 0 else ">>",
        LOCK_ONLY_TEST_PATH,
    )
    for index, line in enumerate(LOCK_ONLY_TEST_SCRIPT)
)


SOFTFLOAT_PROBE_TEST_NAME = "arithmetic"
SOFTFLOAT_PROBE_BEGIN_MARKER = "P2PYTEST:SOFTFLOAT:BEGIN"
SOFTFLOAT_PROBE_PASS_MARKER = "P2PYTEST:SOFTFLOAT:PASS"
SOFTFLOAT_PROBE_PREFIX = "P2PYTEST:SOFTFLOAT:"
# NuttX/P2 has no stat.st_birthtime, so posixmodule builds exactly the atime,
# mtime, and ctime floating-point fields for one successful os.stat() call.
SOFTFLOAT_PROBE_FILL_TIME_CALLS = 3


PYTHON_TESTS: Tuple[PythonTest, ...] = (
    PythonTest(
        "arithmetic",
        "P2PYTEST:ARITH:PASS",
        "python -c 'import os,sys;assert sys.flags.no_site==1;"
        'print("P2PYTEST:"+"SOFTFLOAT:BEGIN",flush=True);'
        's=os.stat("/tmp");assert isinstance(s.st_mtime,float);'
        'print("P2PYTEST:"+"SOFTFLOAT:PASS");'
        "assert (6*7,2**10)==(42,1024);"
        'print("P2PYTEST:"+"ARITH:PASS")\'',
    ),
    PythonTest(
        "float_libm",
        "P2PYTEST:FLOAT:PASS",
        "python -c 'import math;"
        "assert math.isclose(math.sin(math.pi/2),1.0,abs_tol=1e-12);"
        "assert math.sqrt(81.0)==9.0;"
        'print("P2PYTEST:"+"FLOAT:PASS")\'',
    ),
    PythonTest(
        "unicode",
        "P2PYTEST:UNICODE:PASS",
        "python -c 's=chr(0x3c0)+chr(0x1f680);b=s.encode(\"utf-8\");"
        "assert b.decode(\"utf-8\")==s and len(s)==2;"
        'print("P2PYTEST:"+"UNICODE:PASS")\'',
    ),
    # Table membership alone does not prove which loader populated sys.modules.
    # Require the three startup modules themselves to report FrozenImporter
    # origin, then use their alias table to load one non-frozen codec.
    PythonTest(
        "codecs",
        "P2PYTEST:CODECS:PASS",
        "python -c 'import codecs as c,encodings as e,"
        "encodings.aliases as a,encodings.utf_8 as u;"
        "assert all(x.__spec__.origin==\"frozen\" for x in(e,a,u)) and "
        "c.lookup(\"latin1\").name==\"iso8859-1\";"
        'print("P2PYTEST:"+"CODECS:PASS")\'',
    ),
    PythonTest(
        "stdlib",
        "P2PYTEST:STDLIB:PASS",
        "python -c 'import json,collections;"
        'assert json.loads("[1,2,3]")[2]==3;'
        "assert collections.deque([1,2]).pop()==2;"
        'print("P2PYTEST:"+"STDLIB:PASS")\'',
    ),
    PythonTest(
        "zlib_sizes",
        "P2PYTEST:ZLIB_SIZES:PASS",
        "python -c 'import zlib;xs=(b\"\",b\"x\",b\"abc\"*12000);"
        "assert all(zlib.decompress(zlib.compress(x))==x for x in xs);"
        'print("P2PYTEST:"+"ZLIB_SIZES:PASS")\'',
    ),
    PythonTest(
        "zlib_incompressible",
        "P2PYTEST:ZLIB_RANDOM:PASS",
        "python -c 'import random,zlib;x=random.Random(7).randbytes(40000);"
        "c=zlib.compress(x);assert len(c)>len(x) and zlib.decompress(c)==x;"
        'print("P2PYTEST:"+"ZLIB_RANDOM:PASS")\'',
    ),
    PythonTest(
        "zlib_streaming",
        "P2PYTEST:ZLIB_STREAM:PASS",
        "python -c 'import zlib as z;s=b\"a\"*40000;o=z.compressobj();"
        "c=o.compress(s[:1])+o.compress(s[1:])+o.flush();"
        "d=z.decompressobj();r=d.decompress(c[:2])+d.decompress(c[2:])+d.flush();"
        "assert r==s and d.eof;"
        'print("P2PY""TEST:ZLIB_STREAM:PASS")\'',
    ),
    PythonTest(
        "zlib_checksums",
        "P2PYTEST:ZLIB_CHECKSUM:PASS",
        "python -c 'import zlib;s=b\"123456789\";"
        "assert zlib.adler32(s)==0x091e01de and zlib.crc32(s)==0xcbf43926;"
        'print("P2PYTEST:"+"ZLIB_CHECKSUM:PASS")\'',
    ),
    PythonTest(
        "hardware_entropy",
        "P2PYTEST:ENTROPY:PASS",
        "python -c 'import os,secrets;a=os.urandom(256);b=secrets.token_bytes(256);"
        "assert len(a)==len(b)==256 and a!=b and any(a) and any(b);"
        'print("P2PYTEST:ENTROPY:"+"FINGERPRINT:"+a[:16].hex());'
        'print("P2PYTEST:"+"ENTROPY:PASS")\'',
    ),
    PythonTest(
        "runtime_paths",
        "P2PYTEST:PATHS:PASS",
        "python -c 'import sys;assert sys.prefix==sys.exec_prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"PATHS:PASS")\'',
    ),
    PythonTest(
        "user_site_contract",
        "P2PYTEST:USER_SITE:PASS",
        "python -c 'import os,sys;"
        "assert sys.flags.no_site==1 and \"site\" not in sys.modules;"
        "import site;assert site.ENABLE_USER_SITE is None;site.main();"
        "assert site.ENABLE_USER_SITE is False and "
        "os.path.expanduser(\"~\")==\"/tmp\";"
        'print("P2PYTEST:"+"USER_SITE:PASS")\'',
    ),
    PythonTest(
        "ignore_environment",
        "P2PYTEST:IGNORE_ENV:PASS",
        "python -E -c 'import encodings,sys;assert sys.prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"IGNORE_ENV:PASS")\'',
    ),
    PythonTest(
        "isolated_mode",
        "P2PYTEST:ISOLATED:PASS",
        "python -I -c 'import encodings,sys;assert sys.prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"ISOLATED:PASS")\'',
    ),
    PythonTest(
        "allocation_gc",
        "P2PYTEST:ALLOC_GC:PASS",
        "python -c 'import gc;x=[bytearray([i&255])*8192 for i in range(1024)];"
        "assert len(x)==1024 and all(b[0]==b[4096]==b[-1]==(i&255) "
        "for i,b in enumerate(x));del x;"
        'assert gc.collect()>=0;print("P2PYTEST:"+"ALLOC_GC:PASS")\'',
    ),
    # Hold at least 8 MiB in 256 KiB chunks, but cap attempts below the
    # 16 MiB unified user-heap region.  A PASS therefore proves a caught
    # allocator MemoryError followed by release, collection, and a fresh
    # 1 MiB allocation; it cannot turn into an unbounded target OOM loop.

    PythonTest(
        "memory_error_recovery",
        "P2PYTEST:MEMORY:PASS",
        "python -c 'exec(\"import gc\\nx=[]\\ntry:\\n while len(x)<63:"
        "x.append(bytearray(1<<18))\\nexcept MemoryError:pass\\nn=len(x);"
        "assert 32<=n<63\\nx=0;gc.collect();y=bytearray(1<<20);"
        "y[0]=7;assert y[0]==7\\nprint(\\\"P2PYTEST:\\\"+"
        "\\\"MEMORY:PASS\\\")\")'",
    ),
    PythonTest(
        "filesystem",
        "P2PYTEST:FILESYSTEM:PASS",
        'python -c \'import os;p="/tmp/p2py.txt";'
        'open(p,"w").write("p2-python");'
        'assert open(p).read()=="p2-python";os.unlink(p);'
        'print("P2PYTEST:"+"FILESYSTEM:PASS")\'',
    ),
    PythonTest(
        "filesystem_large",
        "P2PYTEST:FILESYSTEM_LARGE:PASS",
        'python -c \'p="/tmp/f";d=bytes(range(256))*3072;'
        'assert open(p,"wb").write(d)==786432;'
        'assert open(p,"rb").read()==d;import os;os.unlink(p);'
        'assert open(p,"wb").write(b"x")==1;os.unlink(p);'
        'print("P2PYTEST:"+"FILESYSTEM_LARGE:PASS")\'',
    ),
    PythonTest(
        "exceptions",
        "P2PYTEST:EXCEPTION:PASS",
        "python -c 'exec(\"try:\\n  1/0\\nexcept ZeroDivisionError:\\n  "
        'print(\\"P2PYTEST:\\"+\\"EXCEPTION:PASS\\")")\'',
    ),
    PythonTest(
        "tracemalloc_tls",
        "P2PYTEST:TRACEMALLOC:PASS",
        "python -c 'import tracemalloc;tracemalloc.start();x=bytearray(4096);"
        "assert tracemalloc.is_tracing() and tracemalloc.get_traced_memory()[0]>0;"
        'tracemalloc.stop();print("P2PYTEST:"+"TRACEMALLOC:PASS")\'',
    ),
    PythonTest(
        "restart_state_seed",
        "P2PYTEST:STATE_SEED:PASS",
        "python -c 'import builtins,sys;builtins._p2_leak=1;"
        "sys.modules[\"_p2_leak\"]=builtins;"
        "open(\"/tmp/p2hash\",\"w\").write(str(hash(\"p2-fixed\")));"
        'print("P2PYTEST:"+"STATE_SEED:PASS")\'',
    ),
    PythonTest(
        "restart_state_isolation",
        "P2PYTEST:STATE_ISOLATION:PASS",
        "python -c 'import builtins,sys,os;"
        "assert not hasattr(builtins,\"_p2_leak\");"
        "assert \"_p2_leak\" not in sys.modules;"
        "assert int(open(\"/tmp/p2hash\").read())!=hash(\"p2-fixed\");"
        "os.unlink(\"/tmp/p2hash\");"
        'print("P2PYTEST:"+"STATE_ISOLATION:PASS")\'',
    ),
    PythonTest(
        "deep_recursion",
        "P2PYTEST:DEEP:PASS",
        'python -c \'x=eval("["*100+"0"+"]"*100);'
        'assert x==eval("["*100+"0"+"]"*100);'
        "assert len(repr(x))==201;"
        'print("P2PYTEST:"+"DEEP:PASS")\'',
    ),
    PythonTest(
        "lock_only_thread",
        "P2PYTEST:LOCK_ONLY:PASS",
        "python " + LOCK_ONLY_TEST_PATH,
        LOCK_ONLY_TEST_SETUP_COMMANDS,
    ),
    PythonTest(
        "subinterpreters_unsupported",
        "P2PYTEST:NO_SUBINTERPRETERS:PASS",
        "python -c 'import importlib.util;"
        "assert importlib.util.find_spec(\"_interpreters\") is None;"
        'print("P2PYTEST:"+"NO_SUBINTERPRETERS:PASS")\'',
    ),
    PythonTest(
        "final",
        "P2PYTEST:ALL:PASS",
        'python -c \'print("P2PYTEST:"+"ALL:PASS")\'',
    ),
)


# The normal qualification deliberately uses only three successful CPython
# starts.  The first is a genuine, persistent plain ``python`` REPL.  It owns
# the one-time container upload, writes the comprehensive script to /tmp, runs
# that script in the same REPL, evaluates 6*7 at a prompt, and then finalizes.
# A -E background lifecycle combines environment/state-isolation checks with
# the EBUSY concurrency proof.  A final -I lifecycle proves isolated-mode and
# a clean restart after contention.  Twenty extra finalize/restart cycles are
# available only through the explicit overnight qualification level.

SPECIAL_RESTART_TEST_NAMES = (
    "ignore_environment",
    "restart_state_isolation",
    "isolated_mode",
)
QUALIFICATION_BATCH_TEST_NAME = "qualification_batch"
QUALIFICATION_BATCH_PATH = "/tmp/p2b.py"
IGNORE_ENV_STATE_PATH = "/tmp/p2e.py"
ISOLATED_RESTART_PATH = "/tmp/p2i.py"


def _test_by_name(name: str) -> PythonTest:
    matches = tuple(test for test in PYTHON_TESTS if test.name == name)
    if len(matches) != 1:
        raise RuntimeError("Python test lookup is not unique: {}".format(name))
    return matches[0]


def _python_c_source(test: PythonTest) -> str:
    argv = shlex.split(test.command)
    if len(argv) != 3 or argv[:2] != ["python", "-c"]:
        raise RuntimeError(
            "batched Python test is not a plain python -c command: {}".format(
                test.name
            )
        )
    return argv[2]


# Avoid transporting backslash-escaped compound statements through NSH echo.
# The target script uses ordinary indentation for these two cases instead.

QUALIFICATION_BATCH_SOURCE_OVERRIDES = {
    "user_site_contract": (
        "import os,sys",
        "assert sys.flags.no_site==1",
        'assert "site" not in sys.modules',
        "import site",
        "assert site.ENABLE_USER_SITE is None",
        "site.main()",
        "assert site.ENABLE_USER_SITE is False",
        'assert os.path.expanduser("~")=="/tmp"',
        'print("P2PY"+"TEST:USER_SITE:PASS")',
    ),
    "memory_error_recovery": (
        "import gc",
        "x=[]",
        "try:",
        " while len(x)<63:",
        "  x.append(bytearray(1<<18))",
        "except MemoryError:",
        " pass",
        "n=len(x)",
        "assert 32<=n<63",
        "x=0",
        "gc.collect()",
        "y=bytearray(1<<20)",
        "y[0]=7",
        "assert y[0]==7",
        'print("P2PY"+"TEST:MEMORY:PASS")',
    ),
    "exceptions": (
        "try:",
        " 1/0",
        "except ZeroDivisionError:",
        ' print("P2PY"+"TEST:EXCEPTION:PASS")',
    ),
    "lock_only_thread": LOCK_ONLY_TEST_SCRIPT,
}


QUALIFICATION_BATCH_TESTS = tuple(
    test
    for test in PYTHON_TESTS
    if test.name not in SPECIAL_RESTART_TEST_NAMES
)


def _qualification_batch_script() -> Tuple[str, ...]:
    lines = ['print("P2PYREPL:"+"SCRIPT:BEGIN")']
    for test in QUALIFICATION_BATCH_TESTS:
        function = "_p2_{}".format(test.name)
        source_lines = QUALIFICATION_BATCH_SOURCE_OVERRIDES.get(test.name)
        if source_lines is None:
            source_lines = (_python_c_source(test),)
        lines.append("def {}():".format(function))
        lines.extend(" " + line for line in source_lines)
        lines.append("{}()".format(function))
        lines.append("del {}".format(function))
    lines.append('print("P2PYREPL:"+"SCRIPT:PASS")')
    return tuple(lines)


QUALIFICATION_BATCH_SCRIPT = _qualification_batch_script()
QUALIFICATION_BATCH_COMMAND = "python"


IGNORE_ENV_STATE_SCRIPT = (
    "import builtins,encodings,os,sys,time",
    "time.sleep(1)",
    "assert sys.flags.ignore_environment==1",
    'assert sys.prefix=="/usr/local"',
    'assert "/usr/local/lib/python313.zip" in sys.path',
    'print("P2PY"+"TEST:IGNORE_ENV:PASS")',
    'assert not hasattr(builtins,"_p2_leak")',
    'assert "_p2_leak" not in sys.modules',
    'assert int(open("/tmp/p2hash").read())!=hash("p2-fixed")',
    'os.unlink("/tmp/p2hash")',
    'print("P2PY"+"TEST:STATE_ISOLATION:PASS")',
    'print("P2PY"+"TEST:CONCURRENCY:HOLDER",flush=True)',
    "time.sleep(10)",
    'print("P2PY"+"TEST:CONCURRENCY:DONE")',
)
ISOLATED_RESTART_SCRIPT = (
    "import encodings,sys",
    "assert sys.flags.isolated==1",
    'assert sys.prefix=="/usr/local"',
    'assert "/usr/local/lib/python313.zip" in sys.path',
    'print("P2PY"+"TEST:ISOLATED:PASS")',
    'print("P2PY"+"TEST:CONCURRENCY:POST_PASS")',
)


def _script_text(lines: Sequence[str]) -> str:
    return "\n".join(lines) + "\n"


def _qualification_script_for_tests(
    tests: Sequence[PythonTest],
) -> Tuple[str, ...]:
    lines = ['print("P2PYREPL:"+"SCRIPT:BEGIN")']
    for test in tests:
        function = "_p2_{}".format(test.name)
        source_lines = QUALIFICATION_BATCH_SOURCE_OVERRIDES.get(test.name)
        if source_lines is None:
            source_lines = (_python_c_source(test),)
        lines.append("def {}():".format(function))
        lines.extend(" " + line for line in source_lines)
        lines.append("{}()".format(function))
        lines.append("del {}".format(function))
    lines.append('print("P2PYREPL:"+"SCRIPT:PASS")')
    return tuple(lines)


def persistent_repl_scripts(
    full_qualification: bool,
) -> Tuple[Tuple[str, str], ...]:
    batch = (
        QUALIFICATION_BATCH_SCRIPT
        if full_qualification
        else _qualification_script_for_tests((_test_by_name("arithmetic"),))
    )
    scripts = [(QUALIFICATION_BATCH_PATH, _script_text(batch))]
    if full_qualification:
        scripts.extend(
            (
                (IGNORE_ENV_STATE_PATH, _script_text(IGNORE_ENV_STATE_SCRIPT)),
                (ISOLATED_RESTART_PATH, _script_text(ISOLATED_RESTART_SCRIPT)),
            )
        )
    return tuple(scripts)


def _bounded_repl_script_commands(path: str, source: str) -> Tuple[str, ...]:
    """Create one script using prompt-acknowledged lines <= LINE_MAX."""

    source.encode("ascii")
    commands = ['_p2f=open({!r},"w")'.format(path)]
    offset = 0
    while offset < len(source):
        low = 1
        high = len(source) - offset
        selected = None
        while low <= high:
            count = (low + high) // 2
            chunk = source[offset : offset + count]
            candidate = (
                "_p2n=_p2f.write({!r});assert _p2n=={}".format(
                    chunk, len(chunk)
                )
            )
            if len((candidate + "\r").encode("ascii")) <= LINE_MAX:
                selected = (chunk, candidate)
                low = count + 1
            else:
                high = count - 1
        if selected is None:
            raise RuntimeError("cannot encode a bounded persistent REPL chunk")
        chunk, command = selected
        commands.append(command)
        offset += len(chunk)
    commands.append("_p2f.close();del _p2f,_p2n")
    return tuple(commands)


def persistent_repl_setup_commands(
    full_qualification: bool,
) -> Tuple[str, ...]:
    commands = []
    for path, source in persistent_repl_scripts(full_qualification):
        commands.extend(_bounded_repl_script_commands(path, source))
    return tuple(commands)


def persistent_repl_exec_command() -> str:
    return "exec(compile(open({0!r}).read(),{0!r},\"exec\"))".format(
        QUALIFICATION_BATCH_PATH
    )


def persistent_repl_prompt_count(full_qualification: bool) -> int:
    # Initial prompt, one ACK per setup command, then post-script and
    # post-expression prompts.  The initial prompt is also the send point for
    # the first setup command, so the exact total is setup + 3.
    return len(persistent_repl_setup_commands(full_qualification)) + 3


def _single_test_worker(name: str) -> PythonTestWorker:
    test = _test_by_name(name)
    return PythonTestWorker(
        name=test.name,
        tests=(test,),
        command=test.command,
        setup_commands=test.setup_commands,
    )


FULL_PYTHON_TEST_WORKERS = (
    PythonTestWorker(
        name="interactive_repl",
        tests=QUALIFICATION_BATCH_TESTS,
        command=QUALIFICATION_BATCH_COMMAND,
    ),
    PythonTestWorker(
        name="concurrency_holder",
        tests=(
            _test_by_name("ignore_environment"),
            _test_by_name("restart_state_isolation"),
        ),
        command="python -E " + IGNORE_ENV_STATE_PATH + " &",
    ),
    PythonTestWorker(
        name="concurrency_post",
        tests=(_test_by_name("isolated_mode"),),
        command="python -I " + ISOLATED_RESTART_PATH,
    ),
)
SMOKE_PYTHON_TEST_WORKERS = (
    PythonTestWorker(
        name="interactive_repl",
        tests=(_test_by_name("arithmetic"),),
        command="python",
    ),
)

CONCURRENCY_HOLDER_MARKER = "P2PYTEST:CONCURRENCY:HOLDER"
CONCURRENCY_DONE_MARKER = "P2PYTEST:CONCURRENCY:DONE"
CONCURRENCY_SECOND_MARKER = "P2PYTEST:CONCURRENCY:SECOND_RAN"
CONCURRENCY_POST_MARKER = "P2PYTEST:CONCURRENCY:POST_PASS"
CONCURRENCY_BUSY_PREFIX = "P2PY:RUNTIME:BUSY:CODE="
WORKER_EXIT_PREFIX = "P2PY:WORKER:EXIT:CODE="
ENTROPY_FINGERPRINT_PREFIX = "P2PYTEST:ENTROPY:FINGERPRINT:"
CONCURRENCY_HOLDER_COMMAND = FULL_PYTHON_TEST_WORKERS[1].command
CONCURRENCY_SECOND_COMMAND = 'python -c \'print("P2PYTEST:"+"CONCURRENCY:SECOND_RAN")\''
CONCURRENCY_POST_COMMAND = FULL_PYTHON_TEST_WORKERS[2].command
INTERACTIVE_REPL_TEST_NAME = "interactive_repl"
INTERACTIVE_REPL_START_COMMAND = "python"
INTERACTIVE_REPL_EXPRESSION_COMMAND = (
    'print("P2PYREPL:"+"EXPR="+str(6*7))'
)
INTERACTIVE_REPL_EXIT_COMMAND = "raise SystemExit"
INTERACTIVE_REPL_BANNER_PREFIX = b"Python 3."
INTERACTIVE_REPL_PROMPT = b">>> "
INTERACTIVE_REPL_EXPRESSION_MARKER = "P2PYREPL:EXPR=42"
INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER = "P2PYREPL:SCRIPT:BEGIN"
INTERACTIVE_REPL_SCRIPT_PASS_MARKER = "P2PYREPL:SCRIPT:PASS"
RESTART_STRESS_COUNT = 20
STACK_MINIMUM_FREE = 2048
CONCURRENCY_HOLDER_TEST_NAME = "concurrency_holder"
CONCURRENCY_POST_TEST_NAME = "concurrency_post"
CONCURRENCY_SUCCESSFUL_WORKERS = 2
INTERACTIVE_REPL_SUCCESSFUL_WORKERS = 1
# The smoke hold discards every SAMPLE already buffered at the prompt, so it
# must span one complete target telemetry period.  Keep a small scheduling
# margin while staying below the default 120-second per-step test timeout.
OVERLAY_TELEMETRY_INTERVAL_SECONDS = 60.0
SMOKE_REPL_LIVE_HOLD_MARGIN_SECONDS = 5.0
SMOKE_REPL_LIVE_HOLD_SECONDS = (
    OVERLAY_TELEMETRY_INTERVAL_SECONDS + SMOKE_REPL_LIVE_HOLD_MARGIN_SECONDS
)
SMOKE_SKIP_REASON = "omitted by smoke qualification level"
FULL_RESTART_SKIP_REASON = "optional; use overnight qualification level"


@dataclasses.dataclass(frozen=True)
class PythonQualificationPlan:
    level: str
    python_tests: Tuple[PythonTest, ...]
    python_workers: Tuple[PythonTestWorker, ...]
    include_restart_stress: bool
    include_concurrency: bool
    full_qualification: bool
    repl_live_hold_seconds: float = 0.0

    @property
    def expected_worker_names(self) -> Tuple[str, ...]:
        names = tuple(worker.name for worker in self.python_workers)
        if self.include_restart_stress:
            names += tuple(
                "restart_stress_{}".format(index)
                for index in range(RESTART_STRESS_COUNT)
            )
        return names

    @property
    def completed_test_names(self) -> Tuple[str, ...]:
        names = ()
        for worker in self.python_workers:
            names += tuple(test.name for test in worker.tests)
            if worker.name == INTERACTIVE_REPL_TEST_NAME:
                names += (INTERACTIVE_REPL_TEST_NAME,)
        if self.include_concurrency:
            names += ("concurrency_guard",)
        if self.include_restart_stress:
            names += ("restart_stress_20",)
        return names

    @property
    def omitted_test_names(self) -> Tuple[str, ...]:
        selected = {test.name for test in self.python_tests}
        return tuple(test.name for test in PYTHON_TESTS if test.name not in selected)

    @property
    def artifact_format(self) -> str:
        if self.level == "overnight":
            return "p2-python-hil-overnight-v1"
        if self.full_qualification:
            return "p2-python-hil-v1"
        return "p2-python-hil-smoke-v1"

    @property
    def success_status(self) -> str:
        return "PASS" if self.full_qualification else "SMOKE_PASS"


FULL_QUALIFICATION_PLAN = PythonQualificationPlan(
    level="full",
    python_tests=PYTHON_TESTS,
    python_workers=FULL_PYTHON_TEST_WORKERS,
    include_restart_stress=False,
    include_concurrency=True,
    full_qualification=True,
)
OVERNIGHT_QUALIFICATION_PLAN = dataclasses.replace(
    FULL_QUALIFICATION_PLAN,
    level="overnight",
    include_restart_stress=True,
)
SMOKE_QUALIFICATION_PLAN = PythonQualificationPlan(
    level="smoke",
    python_tests=tuple(test for test in PYTHON_TESTS if test.name == "arithmetic"),
    python_workers=SMOKE_PYTHON_TEST_WORKERS,
    include_restart_stress=False,
    include_concurrency=False,
    full_qualification=False,
    repl_live_hold_seconds=SMOKE_REPL_LIVE_HOLD_SECONDS,
)
if len(SMOKE_QUALIFICATION_PLAN.python_tests) != 1:
    raise RuntimeError("the smoke qualification must select exactly arithmetic")

EXPECTED_SUCCESSFUL_WORKER_NAMES = FULL_QUALIFICATION_PLAN.expected_worker_names
EXPECTED_SUCCESSFUL_WORKERS = len(EXPECTED_SUCCESSFUL_WORKER_NAMES)
OVERLAY_MAXIMUM_DEPTH = 64

READY_RE = re.compile(
    rb"^P2PY:UPLOAD:READY:PROTO=(\d+):BASE=([0-9A-F]{8}):"
    rb"MAX=(\d+):FRAME=(\d+):BAUD=(\d+)$"
)
STACK_RE = re.compile(rb"^P2PY:WORKER:STACK:FREE=(\d+):SIZE=(\d+)$")
WORKER_EXIT_RE = re.compile(rb"^P2PY:WORKER:EXIT:CODE=(-?\d+)$")
WORKER_STACK_PREFIX = b"P2PY:WORKER:STACK:"
ENTROPY_FINGERPRINT_RE = re.compile(
    rb"^P2PYTEST:ENTROPY:FINGERPRINT:([0-9a-f]{32})$"
)
RESTART_MARKER_PREFIX = b"P2PYTEST:RESTART:"
RESTART_MARKER_RE = re.compile(rb"^P2PYTEST:RESTART:(0|[1-9]\d*):PASS$")
CONCURRENCY_BUSY_RE = re.compile(rb"^P2PY:RUNTIME:BUSY:CODE=([1-9]\d*)$")
OVERLAY_TELEMETRY_PREFIX = b"P2PY:OVL:"
OVERLAY_TELEMETRY_STAGES = ("LAUNCH", "BEGIN", "SAMPLE", "END", "FINAL")
OVERLAY_TELEMETRY_STATS_RE = re.compile(
    rb"^P2PY:OVL:(LAUNCH|BEGIN|SAMPLE|END|FINAL):"
    rb"E=([0-9A-F]{16}):X=([0-9A-F]{16}):D=([0-9A-F]{16}):"
    rb"A=([0-9A-F]{16}):L=([0-9A-F]{16}):B=([0-9A-F]{16}):"
    rb"DEP=([0-9A-F]{8}):MAX=([0-9A-F]{8}):G=([0-9A-F]{8}):"
    rb"LG=([0-9A-F]{8}):LB=([0-9A-F]{8}):REQ=([0-9A-F]{8}):"
    rb"STUB=([0-9A-F]{8}):F=([0-9A-F]{2}):ERR=(-?\d+)$"
)
OVERLAY_TELEMETRY_ERROR_RE = re.compile(
    rb"^P2PY:OVL:(LAUNCH|BEGIN|SAMPLE|END|FINAL):ERROR=(-?\d+)$"
)
XMEM_TELEMETRY_PREFIX = b"P2PY:XMEM:"
XMEM_TELEMETRY_STATS_RE = re.compile(
    rb"^P2PY:XMEM:(LAUNCH|BEGIN|SAMPLE|END|FINAL):"
    rb"H=([0-9A-F]{16}):M=([0-9A-F]{16}):F=([0-9A-F]{16}):"
    rb"W=([0-9A-F]{16}):B=([0-9A-F]{16})$"
)
XMEM_TELEMETRY_ERROR_RE = re.compile(
    rb"^P2PY:XMEM:(LAUNCH|BEGIN|SAMPLE|END|FINAL):ERROR=(-?\d+)$"
)
OVERLAY_CUMULATIVE_FIELDS = (
    "entry_count",
    "exit_count",
    "direct_count",
    "load_attempt_count",
    "load_count",
    "load_bytes",
    "maximum_depth",
)
RUNTIME_STAGES = (
    b"P2PY:TMPFS:READY:PATH=/tmp:HEAP=1048576",
    b"P2PY:ROMDISK:READY:MODE=BUFFERED:SECTOR=512",
    b"P2PY:ROMFS:MOUNTED",
    b"P2PY:CPYTHON:EARLY:START",
    b"P2PY:CPYTHON:EARLY:PASS",
    b"P2PY:CPYTHON:RUN",
)
FATAL_SERIAL_PREFIXES = (b"P2XMEM:FAULT", b"P2XMEM:TIMEOUT")
PYTHON_STATIC_TYPE_COUNT = 113
INIT_DIAGNOSTIC_PREFIX = b"P2PY:INIT:"
INIT_TYPES_RE = re.compile(rb"^P2PY:INIT:TYPES:(BEGIN|PASS):N=(\d+)$")
INIT_TYPE_BEFORE_RE = re.compile(rb"^P2PY:INIT:TYPE:I=(\d+):BEFORE$")
INIT_TYPE_AFTER_RE = re.compile(
    rb"^P2PY:INIT:TYPE:I=(\d+):AFTER:R=(-?\d+)$"
)
INIT_FIXED_MARKERS = {
    b"P2PY:INIT:GIL:TSTATE:PASS": "GIL:TSTATE:PASS",
    b"P2PY:INIT:GIL:READY:PASS": "GIL:READY:PASS",
    b"P2PY:INIT:GLOBAL_OBJECTS:BEGIN": "GLOBAL_OBJECTS:BEGIN",
    b"P2PY:INIT:UNICODE_STATIC:BEGIN": "UNICODE_STATIC:BEGIN",
    b"P2PY:INIT:UNICODE_STATIC:PASS": "UNICODE_STATIC:PASS",
    b"P2PY:INIT:LATIN1:BEGIN": "LATIN1:BEGIN",
    b"P2PY:INIT:LATIN1:PASS": "LATIN1:PASS",
    b"P2PY:INIT:GLOBAL_OBJECTS:PASS": "GLOBAL_OBJECTS:PASS",
    b"P2PY:INIT:CODE:BEGIN": "CODE:BEGIN",
    b"P2PY:INIT:CODE:PASS": "CODE:PASS",
    b"P2PY:INIT:DTOA:BEGIN": "DTOA:BEGIN",
    b"P2PY:INIT:DTOA:PASS": "DTOA:PASS",
    b"P2PY:INIT:GC:BEGIN": "GC:BEGIN",
    b"P2PY:INIT:GC:PASS": "GC:PASS",
    b"P2PY:INIT:PYCORE_TYPES:BEGIN": "PYCORE_TYPES:BEGIN",
    b"P2PY:INIT:PYCORE_TYPES:PASS": "PYCORE_TYPES:PASS",
}
IMPORTLIB_DIAGNOSTIC_PREFIX = b"P2PY:IMPORTLIB:"
IMPORTLIB_PASS_MARKER = b"P2PY:IMPORTLIB:PASS"
PATHCONFIG_DIAGNOSTIC_PREFIX = b"P2PY:PATHCONFIG:"
PATHCONFIG_BEGIN_MARKER = b"P2PY:PATHCONFIG:BEGIN"
PATHCONFIG_PASS_MARKER = b"P2PY:PATHCONFIG:PASS"
PATHCONFIG_FAIL_MARKER = b"P2PY:PATHCONFIG:FAIL"
PATHCONFIG_FIXED_EVENTS = {
    PATHCONFIG_BEGIN_MARKER: "BEGIN",
    PATHCONFIG_PASS_MARKER: "PASS",
    PATHCONFIG_FAIL_MARKER: "FAIL",
}
MAIN_DIAGNOSTIC_PREFIX = b"P2PY:MAIN:"
MAIN_PASS_MARKER = b"P2PY:MAIN:PASS"
FILL_TIME_DIAGNOSTIC_PREFIX = b"P2PY:FILLTIME:"
FILL_TIME_RAW_RE = re.compile(
    rb"^P2PY:FILLTIME:RAW:SECLO=([0-9A-F]{8}):"
    rb"SECHI=([0-9A-F]{8}):NSEC=([0-9A-F]{8})$"
)
FILL_TIME_SUCCESS_MARKERS = (
    b"P2PY:FILLTIME:FLOATDIDF:BEGIN",
    b"P2PY:FILLTIME:FLOATDIDF:PASS",
    b"P2PY:FILLTIME:FLOATUNSIDF:BEGIN",
    b"P2PY:FILLTIME:FLOATUNSIDF:PASS",
    b"P2PY:FILLTIME:MULDF3:BEGIN",
    b"P2PY:FILLTIME:MULDF3:PASS",
    b"P2PY:FILLTIME:ADDDF3:BEGIN",
    b"P2PY:FILLTIME:ADDDF3:PASS",
    b"P2PY:FILLTIME:PYFLOAT:BEGIN",
    b"P2PY:FILLTIME:PYFLOAT:PASS",
)
FILL_TIME_FAILURE_MARKER = b"P2PY:FILLTIME:PYFLOAT:FAIL"
FILL_TIME_FIXED_EVENTS = {
    marker: marker.removeprefix(FILL_TIME_DIAGNOSTIC_PREFIX).decode("ascii")
    for marker in FILL_TIME_SUCCESS_MARKERS + (FILL_TIME_FAILURE_MARKER,)
}
FILL_TIME_SUCCESS_EVENTS = ("RAW",) + tuple(
    FILL_TIME_FIXED_EVENTS[marker] for marker in FILL_TIME_SUCCESS_MARKERS
)
FILL_TIME_RECORDS_PER_CALL = len(FILL_TIME_SUCCESS_EVENTS)
# This profile deliberately sets PyConfig.site_import=0, so a correct startup
# may emit no os.stat()/fill_time diagnostics before MAIN:PASS.  Validate every
# observed startup call exactly, but obtain positive soft-float proof from the
# explicit marker-bounded os.stat("/tmp") probe in the arithmetic lifecycle.
MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE = 0
CONSOLE_DIAGNOSTIC_PREFIX = b"nsh> "

ProgressCallback = Callable[[str, Mapping[str, object]], None]


def report_progress(
    callback: Optional[ProgressCallback], phase: str, **details: object
) -> None:
    if callback is not None:
        callback(phase, details)


def qualification_plan(level: str) -> PythonQualificationPlan:
    if level == FULL_QUALIFICATION_PLAN.level:
        return FULL_QUALIFICATION_PLAN
    if level == OVERNIGHT_QUALIFICATION_PLAN.level:
        return OVERNIGHT_QUALIFICATION_PLAN
    if level == SMOKE_QUALIFICATION_PLAN.level:
        return SMOKE_QUALIFICATION_PLAN
    raise PythonHilError("unknown Python HIL qualification level: {}".format(level))


def qualification_scope(plan: PythonQualificationPlan) -> Mapping[str, object]:
    repl_setup = persistent_repl_setup_commands(plan.full_qualification)
    return {
        "level": plan.level,
        "full_qualification": plan.full_qualification,
        "selected_tests": [test.name for test in plan.python_tests],
        "omitted_tests": list(plan.omitted_test_names),
        "python_workers": [
            {
                "name": worker.name,
                "tests": [test.name for test in worker.tests],
            }
            for worker in plan.python_workers
        ],
        "interactive_repl": {
            "enabled": True,
            "setup_command_count": len(repl_setup),
            "setup_command_bytes": sum(
                len((command + "\r").encode("ascii"))
                for command in repl_setup
            ),
            "expected_prompt_count": len(repl_setup) + 3,
        },
        "restart_stress": {
            "enabled": plan.include_restart_stress,
            "iterations": RESTART_STRESS_COUNT if plan.include_restart_stress else 0,
            "mode": "overnight" if plan.include_restart_stress else "omitted",
        },
        "concurrency_guard": plan.include_concurrency,
        "repl_live_hold_seconds": plan.repl_live_hold_seconds,
        "expected_successful_workers": len(plan.expected_worker_names),
        "expected_successful_worker_names": list(plan.expected_worker_names),
    }


def fsync_directory(path: pathlib.Path) -> None:
    """Make a preceding create or rename durable in one directory."""

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_status_atomic(path: pathlib.Path, status: Mapping[str, object]) -> None:
    """Durably replace JSON without exposing or claiming a partial document."""

    temporary = path.with_name(".{}.{}.tmp".format(path.name, os.getpid()))
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(status, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_python_hil_result(
    result: Mapping[str, object],
    worker_stacks: Sequence[Mapping[str, object]],
    interactive_evidence: Mapping[str, object],
    plan: PythonQualificationPlan = FULL_QUALIFICATION_PLAN,
    entropy_fingerprint_evidence: Optional[str] = None,
    concurrency_evidence: Optional[Mapping[str, object]] = None,
) -> Sequence[str]:
    """Cross-check the structured HIL result against raw worker evidence."""

    errors = []
    expected_completed = list(plan.completed_test_names)
    completed = result.get("completed_tests")
    if completed != expected_completed:
        errors.append(
            "completed_tests does not match the exact {}-test HIL plan".format(
                len(expected_completed)
            )
        )

    entropy_selected = any(
        test.name == "hardware_entropy" for test in plan.python_tests
    )
    structured_entropy = result.get("entropy_fingerprint")
    if entropy_selected:
        if not isinstance(structured_entropy, str) or re.fullmatch(
            r"[0-9a-f]{32}", structured_entropy
        ) is None:
            errors.append(
                "entropy_fingerprint is missing or is not 32 lowercase hex digits"
            )
        if entropy_fingerprint_evidence is None:
            errors.append("raw entropy fingerprint evidence is missing")
        elif structured_entropy != entropy_fingerprint_evidence:
            errors.append(
                "entropy_fingerprint does not match raw serial evidence"
            )
    elif structured_entropy is not None:
        errors.append("entropy_fingerprint is present outside this HIL plan")

    expected_stack_names = list(plan.expected_worker_names)
    expected_workers = len(expected_stack_names)
    stack_samples = result.get("stack_samples")
    if not isinstance(stack_samples, list):
        errors.append("stack_samples is missing or is not a list")
        stack_samples = []
    elif len(stack_samples) != expected_workers:
        errors.append(
            "stack_samples count {} != {}".format(
                len(stack_samples), expected_workers
            )
        )

    observed_names = [
        sample.get("test") if isinstance(sample, Mapping) else None
        for sample in stack_samples
    ]
    if observed_names != expected_stack_names:
        errors.append("stack_samples worker names/order do not match the HIL plan")

    for index, (sample, raw) in enumerate(zip(stack_samples, worker_stacks), 1):
        if not isinstance(sample, Mapping):
            errors.append("stack sample {} is not a mapping".format(index))
            continue
        if sample.get("free") != raw["free"] or sample.get("size") != raw["size"]:
            errors.append(
                "stack sample {} does not match raw serial telemetry".format(index)
            )
        if sample.get("used") != raw["size"] - raw["free"]:
            errors.append("stack sample {} has an invalid used count".format(index))

    interactive = result.get("interactive_repl")
    interactive_index = expected_stack_names.index(INTERACTIVE_REPL_TEST_NAME)
    expected_setup_commands = persistent_repl_setup_commands(
        plan.full_qualification
    )
    expected_setup_bytes = sum(
        len((command + "\r").encode("ascii"))
        for command in expected_setup_commands
    )
    if not isinstance(interactive, Mapping):
        errors.append("interactive_repl result is missing")
    else:
        expected_interactive_stack = (
            stack_samples[interactive_index]
            if len(stack_samples) > interactive_index
            else None
        )
        if interactive.get("stack_sample") != expected_interactive_stack:
            errors.append(
                "interactive_repl stack does not match the top-level worker order"
            )
        if not str(interactive.get("banner", "")).startswith(
            INTERACTIVE_REPL_BANNER_PREFIX.decode("ascii")
        ):
            errors.append("interactive_repl banner is missing or invalid")
        if interactive.get("prompt") != INTERACTIVE_REPL_PROMPT.decode("ascii"):
            errors.append("interactive_repl prompt is missing or invalid")
        if (
            interactive.get("expression_marker")
            != INTERACTIVE_REPL_EXPRESSION_MARKER
        ):
            errors.append("interactive_repl expression marker is invalid")
        if interactive.get("exit_command") != INTERACTIVE_REPL_EXIT_COMMAND:
            errors.append("interactive_repl exit command is invalid")
        if interactive.get("exit_code") != 0:
            errors.append("interactive_repl exit code is not zero")
        setup = interactive.get("setup")
        if not isinstance(setup, Mapping):
            errors.append("interactive_repl setup evidence is missing")
        else:
            expected_setup = {
                "command_count": len(expected_setup_commands),
                "command_bytes": expected_setup_bytes,
                "prompt_ack_count": len(expected_setup_commands),
                "maximum_command_bytes": max(
                    len((command + "\r").encode("ascii"))
                    for command in expected_setup_commands
                ),
            }
            for field, expected in expected_setup.items():
                if setup.get(field) != expected:
                    errors.append(
                        "interactive_repl setup {} {} != {}".format(
                            field, setup.get(field), expected
                        )
                    )
        if interactive.get("execution_command") != persistent_repl_exec_command():
            errors.append("interactive_repl execution command is invalid")
        expected_repl_tests = [
            test.name
            for test in plan.python_workers[interactive_index].tests
        ]
        if interactive.get("script_path") != QUALIFICATION_BATCH_PATH:
            errors.append("interactive_repl prepared script path is invalid")
        if interactive.get("script_tests") != expected_repl_tests:
            errors.append("interactive_repl script tests do not match its lifecycle")
        if interactive.get("script_begin_marker") != INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER:
            errors.append("interactive_repl script BEGIN marker is invalid")
        if interactive.get("script_pass_marker") != INTERACTIVE_REPL_SCRIPT_PASS_MARKER:
            errors.append("interactive_repl script PASS marker is invalid")
        if interactive.get("script_begin_marker") != interactive_evidence.get(
            "script_begin_marker"
        ):
            errors.append(
                "interactive_repl script BEGIN does not match raw serial evidence"
            )
        if interactive.get("script_pass_marker") != interactive_evidence.get(
            "script_pass_marker"
        ):
            errors.append(
                "interactive_repl script PASS does not match raw serial evidence"
            )
        if interactive_evidence.get("setup_prompt_count") != len(
            expected_setup_commands
        ):
            errors.append(
                "interactive_repl setup prompt ACK count does not match raw serial evidence"
            )
        if interactive_evidence.get("setup_command_count") != len(
            expected_setup_commands
        ):
            errors.append(
                "interactive_repl setup command count does not match raw serial evidence"
            )
        if interactive_evidence.get("setup_command_bytes") != expected_setup_bytes:
            errors.append(
                "interactive_repl setup byte count does not match raw serial evidence"
            )
        if interactive_evidence.get("setup_echoes_exact") is not True:
            errors.append("interactive_repl setup command echoes are not exact")
        if interactive.get("banner") != interactive_evidence.get("banner"):
            errors.append(
                "interactive_repl banner does not match raw serial evidence"
            )
        if (
            interactive.get("expression_marker")
            != interactive_evidence.get("expression_marker")
        ):
            errors.append(
                "interactive_repl expression does not match raw serial evidence"
            )
        if plan.repl_live_hold_seconds > 0:
            live_hold = interactive.get("live_hold")
            if not isinstance(live_hold, Mapping):
                errors.append("interactive_repl smoke live hold is missing")
            else:
                if live_hold.get("requested_seconds") != plan.repl_live_hold_seconds:
                    errors.append("interactive_repl smoke hold duration is invalid")
                if live_hold.get("elapsed_seconds", 0) < plan.repl_live_hold_seconds:
                    errors.append("interactive_repl smoke hold ended too early")
                if not str(live_hold.get("sample_marker", "")).startswith(
                    "P2PY:OVL:SAMPLE:"
                ):
                    errors.append("interactive_repl smoke live sample is missing")

    restart = result.get("restart_stress")
    if not plan.include_restart_stress:
        if not isinstance(restart, Mapping) or restart.get("skipped") is not True:
            errors.append("restart_stress omission is not explicit")
        elif restart.get("reason") != (
            SMOKE_SKIP_REASON if plan.level == "smoke" else FULL_RESTART_SKIP_REASON
        ):
            errors.append("restart_stress omission reason is invalid")
    elif not isinstance(restart, Mapping):
        errors.append("restart_stress result is missing")
    else:
        if restart.get("count") != RESTART_STRESS_COUNT:
            errors.append(
                "restart_stress count {} != {}".format(
                    restart.get("count"), RESTART_STRESS_COUNT
                )
            )
        restart_stacks = restart.get("stack_samples")
        if (
            not isinstance(restart_stacks, list)
            or len(restart_stacks) != RESTART_STRESS_COUNT
        ):
            errors.append(
                "restart_stress must contain exactly {} stack samples".format(
                    RESTART_STRESS_COUNT
                )
            )
        restart_start = len(plan.python_workers)
        if isinstance(restart_stacks, list) and restart_stacks != stack_samples[
            restart_start : restart_start + RESTART_STRESS_COUNT
        ]:
            errors.append(
                "restart_stress stack samples do not match the top-level slice"
            )

    concurrency = result.get("concurrency")
    if not plan.include_concurrency:
        if (
            not isinstance(concurrency, Mapping)
            or concurrency.get("skipped") is not True
        ):
            errors.append("concurrency omission is not explicit")
        elif concurrency.get("reason") != SMOKE_SKIP_REASON:
            errors.append("concurrency omission reason is invalid")
    elif not isinstance(concurrency, Mapping):
        errors.append("concurrency result is missing")
    else:
        concurrency_stacks = concurrency.get("stack_samples")
        if not isinstance(concurrency_stacks, list) or len(concurrency_stacks) != 2:
            errors.append("concurrency result must contain exactly 2 stack samples")
        else:
            holder_index = expected_stack_names.index(
                CONCURRENCY_HOLDER_TEST_NAME
            )
            post_index = expected_stack_names.index(CONCURRENCY_POST_TEST_NAME)
            if max(holder_index, post_index) >= len(stack_samples):
                expected_concurrency_stacks = []
            else:
                expected_concurrency_stacks = [
                    stack_samples[holder_index], stack_samples[post_index]
                ]
        if (
            isinstance(concurrency_stacks, list)
            and len(concurrency_stacks) == 2
            and concurrency_stacks != expected_concurrency_stacks
        ):
            errors.append(
                "concurrency stack samples do not match their top-level workers"
            )
        if concurrency_evidence is None:
            errors.append("raw concurrency marker evidence is missing")
        else:
            for field in (
                "holder_marker",
                "busy_marker",
                "done_marker",
                "post_marker",
            ):
                if concurrency.get(field) != concurrency_evidence.get(field):
                    errors.append(
                        "concurrency {} does not match raw serial evidence".format(
                            field
                        )
                    )

    raw_minimum = min(
        (stack["free"] for stack in worker_stacks), default=None
    )
    if result.get("minimum_stack_free") != raw_minimum:
        errors.append(
            "minimum_stack_free {} does not match raw minimum {}".format(
                result.get("minimum_stack_free"), raw_minimum
            )
        )

    return errors


def parse_overlay_telemetry(
    serial_rx: bytes,
    result: Optional[Mapping[str, object]] = None,
    plan: PythonQualificationPlan = FULL_QUALIFICATION_PLAN,
) -> Mapping[str, object]:
    """Parse and qualify every complete P2 overlay/worker record.

    Local record validity is kept separate from full-run qualification so a
    failed or truncated run still preserves useful diagnostics.  Only a
    newline-terminated record is claimed as complete; the raw artifact keeps
    any final fragment.  Console prompt prefixes are tolerated.
    """

    expected_worker_names = plan.expected_worker_names
    expected_workers = len(expected_worker_names)
    expected_repl_setup_commands = persistent_repl_setup_commands(
        plan.full_qualification
    )
    expected_repl_setup_count = len(expected_repl_setup_commands)
    expected_interactive_prompt_count = expected_repl_setup_count + 3
    records = []
    malformed = []
    validation_errors = []
    qualification_errors = []
    result_validation_errors = []
    stats_records = []
    worker_exits = []
    worker_stacks = []
    worker_malformed = []
    test_marker_records = []
    entropy_fingerprint_records = []
    entropy_fingerprint_malformed = []
    restart_marker_records = []
    restart_marker_malformed = []
    concurrency_marker_records = []
    concurrency_marker_malformed = []
    interactive_banners = []
    interactive_expressions = []
    interactive_script_begins = []
    interactive_script_passes = []
    init_records = []
    init_malformed = []
    importlib_pass_records = []
    importlib_malformed = []
    pathconfig_records = []
    pathconfig_malformed = []
    main_records = []
    main_malformed = []
    fill_time_records = []
    fill_time_malformed = []
    softfloat_probe_records = []
    softfloat_probe_malformed = []
    xmem_records = []
    xmem_errors = []
    xmem_malformed = []
    ordered_events = []
    byte_offset = 0
    chunks = serial_rx.split(b"\n")
    trailing_fragment = chunks[-1]
    test_marker_names = {
        test.marker.encode("ascii"): test.name for test in PYTHON_TESTS
    }
    concurrency_markers = {
        CONCURRENCY_HOLDER_MARKER.encode("ascii"): "holder",
        CONCURRENCY_DONE_MARKER.encode("ascii"): "done",
        CONCURRENCY_SECOND_MARKER.encode("ascii"): "second_ran",
        CONCURRENCY_POST_MARKER.encode("ascii"): "post",
    }
    softfloat_probe_events = {
        SOFTFLOAT_PROBE_BEGIN_MARKER.encode("ascii"): "BEGIN",
        SOFTFLOAT_PROBE_PASS_MARKER.encode("ascii"): "PASS",
    }

    for line_number, chunk in enumerate(chunks[:-1], 1):
        line = chunk[:-1] if chunk.endswith(b"\r") else chunk

        marker_offset = (
            len(CONSOLE_DIAGNOSTIC_PREFIX)
            if line.startswith(CONSOLE_DIAGNOSTIC_PREFIX)
            else 0
        )
        marker_name = test_marker_names.get(line[marker_offset:])
        if marker_name is not None:
            test_marker_records.append(
                {
                    "line": line_number,
                    "byte_offset": byte_offset + marker_offset,
                    "test": marker_name,
                }
            )

        diagnostic = line[marker_offset:]
        location = {
            "line": line_number,
            "byte_offset": byte_offset + marker_offset,
        }
        if diagnostic.startswith(ENTROPY_FINGERPRINT_PREFIX.encode("ascii")):
            match = ENTROPY_FINGERPRINT_RE.fullmatch(diagnostic)
            if match is None:
                entropy_fingerprint_malformed.append(
                    {**location, "raw": diagnostic.decode("ascii", "replace")}
                )
            else:
                entropy_fingerprint_records.append(
                    {
                        **location,
                        "fingerprint": match.group(1).decode("ascii"),
                    }
                )

        if diagnostic.startswith(RESTART_MARKER_PREFIX):
            match = RESTART_MARKER_RE.fullmatch(diagnostic)
            if match is None:
                restart_marker_malformed.append(
                    {**location, "raw": diagnostic.decode("ascii", "replace")}
                )
            else:
                restart_marker_records.append(
                    {**location, "iteration": int(match.group(1))}
                )

        concurrency_kind = concurrency_markers.get(diagnostic)
        if concurrency_kind is not None:
            concurrency_marker_records.append(
                {**location, "event": concurrency_kind}
            )
        elif diagnostic.startswith(b"P2PYTEST:CONCURRENCY:"):
            concurrency_marker_malformed.append(
                {**location, "raw": diagnostic.decode("ascii", "replace")}
            )

        if diagnostic.startswith(CONCURRENCY_BUSY_PREFIX.encode("ascii")):
            match = CONCURRENCY_BUSY_RE.fullmatch(diagnostic)
            if match is None:
                concurrency_marker_malformed.append(
                    {**location, "raw": diagnostic.decode("ascii", "replace")}
                )
            else:
                concurrency_marker_records.append(
                    {
                        **location,
                        "event": "busy",
                        "code": int(match.group(1)),
                    }
                )

        softfloat_probe_event = softfloat_probe_events.get(diagnostic)
        if softfloat_probe_event is not None:
            softfloat_probe_records.append(
                {**location, "event": softfloat_probe_event}
            )
        elif diagnostic.startswith(SOFTFLOAT_PROBE_PREFIX.encode("ascii")):
            softfloat_probe_malformed.append(
                {**location, "raw": diagnostic.decode("ascii", "replace")}
            )

        if line.startswith(INTERACTIVE_REPL_BANNER_PREFIX):
            interactive_banners.append(
                {
                    "line": line_number,
                    "byte_offset": byte_offset,
                    "text": line.decode("ascii", "replace"),
                }
            )
        if line == INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii"):
            interactive_expressions.append(
                {
                    "line": line_number,
                    "byte_offset": byte_offset,
                    "text": line.decode("ascii"),
                }
            )
        if diagnostic == INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii"):
            interactive_script_begins.append({**location})
        if diagnostic == INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii"):
            interactive_script_passes.append({**location})

        init_offset = line.find(INIT_DIAGNOSTIC_PREFIX)
        if init_offset >= 0:
            candidate = line[init_offset:]
            location = {
                "line": line_number,
                "byte_offset": byte_offset + init_offset,
            }
            fixed = INIT_FIXED_MARKERS.get(candidate)
            types_match = INIT_TYPES_RE.fullmatch(candidate)
            before_match = INIT_TYPE_BEFORE_RE.fullmatch(candidate)
            after_match = INIT_TYPE_AFTER_RE.fullmatch(candidate)
            if fixed is not None:
                init_records.append({**location, "event": fixed})
            elif types_match is not None:
                phase = types_match.group(1).decode("ascii")
                count = int(types_match.group(2))
                init_records.append(
                    {
                        **location,
                        "event": "TYPES:{}".format(phase),
                        "count": count,
                    }
                )
            elif before_match is not None:
                index = int(before_match.group(1))
                init_records.append(
                    {
                        **location,
                        "event": "TYPE:BEFORE",
                        "index": index,
                    }
                )
            elif after_match is not None:
                index = int(after_match.group(1))
                result_code = int(after_match.group(2))
                init_records.append(
                    {
                        **location,
                        "event": "TYPE:AFTER",
                        "index": index,
                        "result": result_code,
                    }
                )
            else:
                init_malformed.append(
                    {
                        **location,
                        "raw": candidate.decode("ascii", "replace"),
                    }
                )

        importlib_offset = line.find(IMPORTLIB_PASS_MARKER)
        if importlib_offset >= 0:
            if line == IMPORTLIB_PASS_MARKER:
                candidate = line
                marker_offset = 0
            elif line == CONSOLE_DIAGNOSTIC_PREFIX + IMPORTLIB_PASS_MARKER:
                marker_offset = len(CONSOLE_DIAGNOSTIC_PREFIX)
                candidate = line[marker_offset:]
            else:
                marker_offset = importlib_offset
                candidate = None
            location = {
                "line": line_number,
                "byte_offset": byte_offset + marker_offset,
            }
            if candidate == IMPORTLIB_PASS_MARKER:
                importlib_pass_records.append({**location, "event": "PASS"})
            else:
                importlib_malformed.append(
                    {
                        **location,
                        "raw": line[marker_offset:].decode("ascii", "replace"),
                    }
                )

        pathconfig_offset = line.find(PATHCONFIG_DIAGNOSTIC_PREFIX)
        if pathconfig_offset >= 0:
            if line.startswith(PATHCONFIG_DIAGNOSTIC_PREFIX):
                candidate = line
                marker_offset = 0
            elif line.startswith(
                CONSOLE_DIAGNOSTIC_PREFIX + PATHCONFIG_DIAGNOSTIC_PREFIX
            ):
                marker_offset = len(CONSOLE_DIAGNOSTIC_PREFIX)
                candidate = line[marker_offset:]
            else:
                marker_offset = pathconfig_offset
                candidate = None
            location = {
                "line": line_number,
                "byte_offset": byte_offset + marker_offset,
            }
            event = (
                PATHCONFIG_FIXED_EVENTS.get(candidate)
                if candidate is not None
                else None
            )
            if event is not None:
                pathconfig_records.append({**location, "event": event})
            else:
                pathconfig_malformed.append(
                    {
                        **location,
                        "raw": line[marker_offset:].decode("ascii", "replace"),
                    }
                )

        main_offset = line.find(MAIN_DIAGNOSTIC_PREFIX)
        if main_offset >= 0:
            if line.startswith(MAIN_DIAGNOSTIC_PREFIX):
                candidate = line
                marker_offset = 0
            elif line.startswith(
                CONSOLE_DIAGNOSTIC_PREFIX + MAIN_DIAGNOSTIC_PREFIX
            ):
                marker_offset = len(CONSOLE_DIAGNOSTIC_PREFIX)
                candidate = line[marker_offset:]
            else:
                marker_offset = main_offset
                candidate = None
            location = {
                "line": line_number,
                "byte_offset": byte_offset + marker_offset,
            }
            if candidate == MAIN_PASS_MARKER:
                main_records.append({**location, "event": "PASS"})
            else:
                main_malformed.append(
                    {
                        **location,
                        "raw": line[marker_offset:].decode("ascii", "replace"),
                    }
                )

        fill_time_offset = line.find(FILL_TIME_DIAGNOSTIC_PREFIX)
        if fill_time_offset >= 0:
            if line.startswith(FILL_TIME_DIAGNOSTIC_PREFIX):
                candidate = line
                marker_offset = 0
            elif line.startswith(
                CONSOLE_DIAGNOSTIC_PREFIX + FILL_TIME_DIAGNOSTIC_PREFIX
            ):
                marker_offset = len(CONSOLE_DIAGNOSTIC_PREFIX)
                candidate = line[marker_offset:]
            else:
                marker_offset = fill_time_offset
                candidate = None
            location = {
                "line": line_number,
                "byte_offset": byte_offset + marker_offset,
            }
            raw_match = (
                FILL_TIME_RAW_RE.fullmatch(candidate)
                if candidate is not None
                else None
            )
            fixed_event = (
                FILL_TIME_FIXED_EVENTS.get(candidate)
                if candidate is not None
                else None
            )
            if raw_match is not None:
                fill_time_records.append(
                    {
                        **location,
                        "event": "RAW",
                        "sec_low": int(raw_match.group(1), 16),
                        "sec_high": int(raw_match.group(2), 16),
                        "nanoseconds": int(raw_match.group(3), 16),
                    }
                )
            elif fixed_event is not None:
                fill_time_records.append({**location, "event": fixed_event})
            else:
                fill_time_malformed.append(
                    {
                        **location,
                        "raw": line[marker_offset:].decode("ascii", "replace"),
                    }
                )

        xmem_offset = line.find(XMEM_TELEMETRY_PREFIX)
        if xmem_offset >= 0:
            candidate = line[xmem_offset:]
            location = {
                "line": line_number,
                "byte_offset": byte_offset + xmem_offset,
            }
            match = XMEM_TELEMETRY_STATS_RE.fullmatch(candidate)
            if match is not None:
                values = [int(group, 16) for group in match.groups()[1:]]
                xmem_records.append(
                    {
                        **location,
                        "stage": match.group(1).decode("ascii"),
                        "hits": values[0],
                        "misses": values[1],
                        "fills": values[2],
                        "writes": values[3],
                        "bypasses": values[4],
                    }
                )
            else:
                error_match = XMEM_TELEMETRY_ERROR_RE.fullmatch(candidate)
                if error_match is not None:
                    xmem_errors.append(
                        {
                            **location,
                            "stage": error_match.group(1).decode("ascii"),
                            "error": int(error_match.group(2)),
                        }
                    )
                else:
                    xmem_malformed.append(
                        {
                            **location,
                            "raw": candidate.decode("ascii", "replace"),
                        }
                    )

        marker_offset = line.find(OVERLAY_TELEMETRY_PREFIX)
        if marker_offset >= 0:
            candidate = line[marker_offset:]
            location = {
                "line": line_number,
                "byte_offset": byte_offset + marker_offset,
            }
            match = OVERLAY_TELEMETRY_STATS_RE.fullmatch(candidate)
            if match is not None:
                values = [int(group, 16) for group in match.groups()[1:-1]]
                flags = values[-1]
                record = {
                    **location,
                    "kind": "stats",
                    "stage": match.group(1).decode("ascii"),
                    "entry_count": values[0],
                    "exit_count": values[1],
                    "direct_count": values[2],
                    "load_attempt_count": values[3],
                    "load_count": values[4],
                    "load_bytes": values[5],
                    "current_depth": values[6],
                    "maximum_depth": values[7],
                    "loaded_group": values[8],
                    "loading_group": values[9],
                    "loading_bytes": values[10],
                    "last_requested_group": values[11],
                    "last_stub_index": values[12],
                    "flags": flags,
                    "ready": bool(flags & 1),
                    "transition": bool(flags & 2),
                    "last_error": int(match.group(16)),
                }
                records.append(record)
                stats_records.append(record)
                ordered_events.append({"event": "stats", "record": record})
            else:
                error_match = OVERLAY_TELEMETRY_ERROR_RE.fullmatch(candidate)
                if error_match is not None:
                    record = {
                        **location,
                        "kind": "error",
                        "stage": error_match.group(1).decode("ascii"),
                        "error": int(error_match.group(2)),
                    }
                    records.append(record)
                    validation_errors.append(
                        "line {} {} telemetry API error {}".format(
                            line_number, record["stage"], record["error"]
                        )
                    )
                else:
                    malformed.append(
                        {
                            **location,
                            "raw": candidate.decode("ascii", "replace"),
                            "reason": "record does not match the telemetry grammar",
                        }
                    )

        exit_marker = WORKER_EXIT_PREFIX.encode("ascii")
        exit_offset = line.find(exit_marker)
        if exit_offset >= 0:
            candidate = line[exit_offset:]
            location = {
                "line": line_number,
                "byte_offset": byte_offset + exit_offset,
            }
            match = WORKER_EXIT_RE.fullmatch(candidate)
            if match is None:
                worker_malformed.append(
                    {
                        **location,
                        "kind": "exit",
                        "raw": candidate.decode("ascii", "replace"),
                    }
                )
            else:
                event = {**location, "code": int(match.group(1))}
                worker_exits.append(event)
                ordered_events.append({"event": "exit", "record": event})

        stack_offset = line.find(WORKER_STACK_PREFIX)
        if stack_offset >= 0:
            candidate = line[stack_offset:]
            location = {
                "line": line_number,
                "byte_offset": byte_offset + stack_offset,
            }
            match = STACK_RE.fullmatch(candidate)
            if match is None:
                worker_malformed.append(
                    {
                        **location,
                        "kind": "stack",
                        "raw": candidate.decode("ascii", "replace"),
                    }
                )
            else:
                free = int(match.group(1))
                size = int(match.group(2))
                event = {
                    **location,
                    "free": free,
                    "size": size,
                    "used": size - free,
                }
                worker_stacks.append(event)
                ordered_events.append({"event": "stack", "record": event})

        byte_offset += len(chunk) + 1

    interactive_prompt_offsets = []
    prompt_search = 0
    while True:
        prompt_offset = serial_rx.find(INTERACTIVE_REPL_PROMPT, prompt_search)
        if prompt_offset < 0:
            break
        interactive_prompt_offsets.append(prompt_offset)
        prompt_search = prompt_offset + len(INTERACTIVE_REPL_PROMPT)

    interactive_echo_commands = expected_repl_setup_commands + (
        persistent_repl_exec_command(),
        INTERACTIVE_REPL_EXPRESSION_COMMAND,
        INTERACTIVE_REPL_EXIT_COMMAND,
    )
    interactive_echo_records = []
    interactive_echoes_exact = (
        len(interactive_prompt_offsets) == len(interactive_echo_commands)
    )
    if interactive_echoes_exact:
        for prompt_offset, command in zip(
            interactive_prompt_offsets, interactive_echo_commands
        ):
            encoded = (
                INTERACTIVE_REPL_PROMPT
                + command.encode("ascii")
                + b"\r\n"
            )
            exact = serial_rx.startswith(encoded, prompt_offset)
            interactive_echo_records.append(
                {
                    "byte_offset": prompt_offset,
                    "command_bytes": len((command + "\r").encode("ascii")),
                    "exact": exact,
                }
            )
            interactive_echoes_exact = interactive_echoes_exact and exact

    trailing_markers = [
        name
        for name, marker in (
            ("overlay", OVERLAY_TELEMETRY_PREFIX),
            ("worker-exit", WORKER_EXIT_PREFIX.encode("ascii")),
            ("worker-stack", WORKER_STACK_PREFIX),
            ("cpython-init", INIT_DIAGNOSTIC_PREFIX),
            ("cpython-importlib", IMPORTLIB_DIAGNOSTIC_PREFIX),
            ("cpython-pathconfig", PATHCONFIG_DIAGNOSTIC_PREFIX),
            ("cpython-main", MAIN_DIAGNOSTIC_PREFIX),
            ("fill-time", FILL_TIME_DIAGNOSTIC_PREFIX),
            ("xmem", XMEM_TELEMETRY_PREFIX),
            ("python-test", b"P2PYTEST:"),
        )
        if marker in trailing_fragment
    ]
    if trailing_markers:
        qualification_errors.append(
            "trailing incomplete known telemetry marker(s): {}".format(
                ", ".join(trailing_markers)
            )
        )

    previous = None
    for record in stats_records:
        location = "line {} {}".format(record["line"], record["stage"])
        if record["flags"] & ~3:
            validation_errors.append(
                "{} has unknown flags 0x{:02X}".format(location, record["flags"])
            )
        if not record["ready"]:
            validation_errors.append("{} does not report READY".format(location))
        if record["last_error"] != 0:
            validation_errors.append(
                "{} reports ERR={}".format(location, record["last_error"])
            )

        expected_depth = (
            record["entry_count"]
            - record["direct_count"]
            - record["exit_count"]
        )
        if expected_depth != record["current_depth"]:
            validation_errors.append(
                "{} violates entry-direct-exit=depth ({}-{}-{} != {})".format(
                    location,
                    record["entry_count"],
                    record["direct_count"],
                    record["exit_count"],
                    record["current_depth"],
                )
            )
        if record["maximum_depth"] < record["current_depth"]:
            validation_errors.append(
                "{} has maximum depth {} below current depth {}".format(
                    location,
                    record["maximum_depth"],
                    record["current_depth"],
                )
            )
        if record["maximum_depth"] > OVERLAY_MAXIMUM_DEPTH:
            validation_errors.append(
                "{} maximum depth {} exceeds configured limit {}".format(
                    location,
                    record["maximum_depth"],
                    OVERLAY_MAXIMUM_DEPTH,
                )
            )

        outstanding_load = record["load_attempt_count"] - record["load_count"]
        if record["transition"]:
            if outstanding_load != 1:
                validation_errors.append(
                    "{} has attempts-loads {} while loading".format(
                        location, outstanding_load
                    )
                )
            if (
                record["loaded_group"] != 0
                or record["loading_group"] == 0
                or record["loading_bytes"] == 0
            ):
                validation_errors.append(
                    "{} has inconsistent transition/loading state".format(location)
                )
        else:
            if outstanding_load != 0:
                validation_errors.append(
                    "{} has attempts-loads {} while not loading".format(
                        location, outstanding_load
                    )
                )
            if record["loading_group"] != 0 or record["loading_bytes"] != 0:
                validation_errors.append(
                    "{} has inconsistent idle loading state".format(location)
                )

        if record["entry_count"] > 0 and (
            record["last_requested_group"] == 0
            or record["last_stub_index"] == 0xFFFFFFFF
        ):
            validation_errors.append(
                "{} has no last request/stub after overlay entries".format(location)
            )

        if record["stage"] == "FINAL" and (
            record["current_depth"] != 0
            or record["transition"]
            or record["loading_group"] != 0
            or record["loading_bytes"] != 0
            or outstanding_load != 0
        ):
            validation_errors.append("{} is not quiescent".format(location))

        if previous is not None:
            for field in OVERLAY_CUMULATIVE_FIELDS:
                if record[field] < previous[field]:
                    validation_errors.append(
                        "{} counter {} regressed from {} to {}".format(
                            location, field, previous[field], record[field]
                        )
                    )
            load_delta = record["load_count"] - previous["load_count"]
            byte_delta = record["load_bytes"] - previous["load_bytes"]
            if (load_delta == 0) != (byte_delta == 0):
                validation_errors.append(
                    "{} has inconsistent load-count/load-byte progress".format(
                        location
                    )
                )
        previous = record

    for event in worker_exits:
        if event["code"] != 0:
            qualification_errors.append(
                "line {} worker exited with code {}".format(
                    event["line"], event["code"]
                )
            )
    for event in worker_stacks:
        if (
            event["size"] <= 0
            or event["free"] > event["size"]
            or event["free"] < STACK_MINIMUM_FREE
        ):
            qualification_errors.append(
                "line {} worker stack telemetry is invalid or below headroom".format(
                    event["line"]
                )
            )
    for event in worker_malformed:
        qualification_errors.append(
            "line {} malformed worker {} telemetry".format(
                event["line"], event["kind"]
            )
        )
    if entropy_fingerprint_malformed:
        qualification_errors.append(
            "{} malformed entropy fingerprint markers".format(
                len(entropy_fingerprint_malformed)
            )
        )
    if restart_marker_malformed:
        qualification_errors.append(
            "{} malformed restart markers".format(
                len(restart_marker_malformed)
            )
        )
    if concurrency_marker_malformed:
        qualification_errors.append(
            "{} malformed concurrency markers".format(
                len(concurrency_marker_malformed)
            )
        )
    if softfloat_probe_malformed:
        qualification_errors.append(
            "{} malformed soft-float probe markers".format(
                len(softfloat_probe_malformed)
            )
        )

    expected_init_cycle = [
        {"event": "GIL:TSTATE:PASS"},
        {"event": "GIL:READY:PASS"},
        {"event": "GLOBAL_OBJECTS:BEGIN"},
        {"event": "UNICODE_STATIC:BEGIN"},
        {"event": "UNICODE_STATIC:PASS"},
        {"event": "LATIN1:BEGIN"},
        {"event": "LATIN1:PASS"},
        {"event": "GLOBAL_OBJECTS:PASS"},
        {"event": "CODE:BEGIN"},
        {"event": "CODE:PASS"},
        {"event": "DTOA:BEGIN"},
        {"event": "DTOA:PASS"},
        {"event": "GC:BEGIN"},
        {"event": "GC:PASS"},
        {"event": "PYCORE_TYPES:BEGIN"},
        {"event": "TYPES:BEGIN", "count": PYTHON_STATIC_TYPE_COUNT},
    ]
    for index in range(PYTHON_STATIC_TYPE_COUNT):
        expected_init_cycle.extend(
            (
                {"event": "TYPE:BEFORE", "index": index},
                {"event": "TYPE:AFTER", "index": index, "result": 0},
            )
        )
    expected_init_cycle.extend(
        (
            {"event": "TYPES:PASS", "count": PYTHON_STATIC_TYPE_COUNT},
            {"event": "PYCORE_TYPES:PASS"},
        )
    )
    expected_init_records = len(expected_init_cycle) * expected_workers
    if init_malformed:
        qualification_errors.append(
            "{} malformed CPython initialization markers".format(
                len(init_malformed)
            )
        )
    if len(init_records) != expected_init_records:
        qualification_errors.append(
            "CPython initialization marker count {} != {} ({} lifecycles x {} "
            "records)".format(
                len(init_records),
                expected_init_records,
                expected_workers,
                len(expected_init_cycle),
            )
        )
    first_init_mismatch = None
    comparison_count = min(len(init_records), expected_init_records)
    for position in range(comparison_count):
        expected = expected_init_cycle[position % len(expected_init_cycle)]
        actual = init_records[position]
        if any(actual.get(key) != value for key, value in expected.items()):
            first_init_mismatch = {
                "position": position,
                "lifecycle": position // len(expected_init_cycle) + 1,
                "record_in_lifecycle": position % len(expected_init_cycle),
                "line": actual["line"],
                "expected": expected,
                "actual": {
                    key: actual.get(key)
                    for key in ("event", "count", "index", "result")
                    if key in actual
                },
            }
            qualification_errors.append(
                "CPython initialization sequence first differs at lifecycle "
                "{} record {} (line {})".format(
                    first_init_mismatch["lifecycle"],
                    first_init_mismatch["record_in_lifecycle"],
                    first_init_mismatch["line"],
                )
            )
            break
    complete_init_lifecycles = 0
    for start in range(0, len(init_records), len(expected_init_cycle)):
        cycle = init_records[start : start + len(expected_init_cycle)]
        if len(cycle) != len(expected_init_cycle):
            break
        if all(
            all(actual.get(key) == value for key, value in expected.items())
            for actual, expected in zip(cycle, expected_init_cycle)
        ):
            complete_init_lifecycles += 1
        else:
            break

    if importlib_malformed:
        qualification_errors.append(
            "{} malformed CPython IMPORTLIB:PASS markers".format(
                len(importlib_malformed)
            )
        )
    if len(importlib_pass_records) != expected_workers:
        qualification_errors.append(
            "CPython IMPORTLIB:PASS marker count {} != {}".format(
                len(importlib_pass_records), expected_workers
            )
        )

    if pathconfig_malformed:
        qualification_errors.append(
            "{} malformed CPython PATHCONFIG markers".format(
                len(pathconfig_malformed)
            )
        )
    pathconfig_event_records = {
        event: [
            record for record in pathconfig_records if record["event"] == event
        ]
        for event in ("BEGIN", "PASS", "FAIL")
    }
    for event, expected_count in (
        ("BEGIN", expected_workers),
        ("PASS", expected_workers),
        ("FAIL", 0),
    ):
        if len(pathconfig_event_records[event]) != expected_count:
            qualification_errors.append(
                "CPython PATHCONFIG:{} marker count {} != {}".format(
                    event,
                    len(pathconfig_event_records[event]),
                    expected_count,
                )
            )
    for record in pathconfig_event_records["FAIL"]:
        qualification_errors.append(
            "line {} CPython PATHCONFIG reported FAIL".format(record["line"])
        )

    if main_malformed:
        qualification_errors.append(
            "{} malformed CPython MAIN markers".format(len(main_malformed))
        )
    if len(main_records) != expected_workers:
        qualification_errors.append(
            "CPython MAIN:PASS marker count {} != {}".format(
                len(main_records), expected_workers
            )
        )
    if fill_time_malformed:
        qualification_errors.append(
            "{} malformed fill_time diagnostic markers".format(
                len(fill_time_malformed)
            )
        )
    for record in fill_time_records:
        if (
            record["event"] == "RAW"
            and record["nanoseconds"] >= 1_000_000_000
        ):
            qualification_errors.append(
                "line {} fill_time nanoseconds {} are outside [0, 1000000000)".format(
                    record["line"], record["nanoseconds"]
                )
            )

    previous_xmem = None
    for record in xmem_records:
        location = "line {} {}".format(record["line"], record["stage"])
        if record["fills"] > record["misses"]:
            validation_errors.append(
                "{} xmem fills {} exceed misses {}".format(
                    location, record["fills"], record["misses"]
                )
            )
        if previous_xmem is not None:
            for field in ("hits", "misses", "fills", "writes", "bypasses"):
                if record[field] < previous_xmem[field]:
                    validation_errors.append(
                        "{} xmem counter {} regressed from {} to {}".format(
                            location,
                            field,
                            previous_xmem[field],
                            record[field],
                        )
                    )
        previous_xmem = record

    for record in xmem_errors:
        qualification_errors.append(
            "line {} {} xmem telemetry API error {}".format(
                record["line"], record["stage"], record["error"]
            )
        )
    if xmem_malformed:
        qualification_errors.append(
            "{} malformed xmem telemetry records".format(len(xmem_malformed))
        )

    first = stats_records[0] if stats_records else None
    last = stats_records[-1] if stats_records else None
    if first is None:
        classification = "no-stats-records"
        deltas = {}
    else:
        deltas = {
            field: last[field] - first[field]
            for field in (
                "entry_count",
                "exit_count",
                "direct_count",
                "load_attempt_count",
                "load_count",
                "load_bytes",
            )
        }
        if last["transition"] and last["loading_group"] != 0:
            classification = "load-in-progress"
        elif deltas["load_count"] > 0:
            classification = "overlay-load-progress"
        elif deltas["entry_count"] > 0:
            classification = "same-group-progress"
        else:
            classification = "no-counter-progress"

    stage_counts = {
        stage: sum(record["stage"] == stage for record in stats_records)
        for stage in OVERLAY_TELEMETRY_STAGES
    }
    xmem_stage_counts = {
        stage: sum(record["stage"] == stage for record in xmem_records)
        for stage in OVERLAY_TELEMETRY_STAGES
    }
    if len(xmem_records) != len(stats_records):
        qualification_errors.append(
            "xmem telemetry record count {} != overlay stats count {}".format(
                len(xmem_records), len(stats_records)
            )
        )
    if [record["stage"] for record in xmem_records] != [
        record["stage"] for record in stats_records
    ]:
        qualification_errors.append(
            "xmem telemetry stages do not exactly match overlay stages"
        )
    for stage in OVERLAY_TELEMETRY_STAGES:
        if xmem_stage_counts[stage] != stage_counts[stage]:
            qualification_errors.append(
                "xmem {} stage count {} != overlay count {}".format(
                    stage, xmem_stage_counts[stage], stage_counts[stage]
                )
            )
    for stage in ("LAUNCH", "BEGIN", "END", "FINAL"):
        if stage_counts[stage] != expected_workers:
            qualification_errors.append(
                "{} stage count {} != {}".format(
                    stage, stage_counts[stage], expected_workers
                )
            )
    if stage_counts["SAMPLE"] < 1:
        qualification_errors.append("SAMPLE stage count must be at least 1")
    if len(worker_exits) != expected_workers:
        qualification_errors.append(
            "worker exit count {} != {}".format(
                len(worker_exits), expected_workers
            )
        )
    if len(worker_stacks) != expected_workers:
        qualification_errors.append(
            "worker stack count {} != {}".format(
                len(worker_stacks), expected_workers
            )
        )

    ordered_events.sort(key=lambda event: event["record"]["byte_offset"])
    pending = []
    lifecycles = []
    race_counts = {
        "launch_before_begin": 0,
        "launch_between_begin_end": 0,
        "launch_after_end": 0,
    }
    for event in ordered_events:
        pending.append(event)
        if event["event"] != "stats" or event["record"]["stage"] != "FINAL":
            continue

        lifecycle_number = len(lifecycles) + 1
        errors_before = len(qualification_errors)
        stage_events = {}
        for stage in OVERLAY_TELEMETRY_STAGES:
            stage_events[stage] = [
                item["record"]
                for item in pending
                if item["event"] == "stats" and item["record"]["stage"] == stage
            ]
        for stage in ("LAUNCH", "BEGIN", "END", "FINAL"):
            if len(stage_events[stage]) != 1:
                qualification_errors.append(
                    "lifecycle {} has {} {} records".format(
                        lifecycle_number, len(stage_events[stage]), stage
                    )
                )

        launch = stage_events["LAUNCH"][0] if len(stage_events["LAUNCH"]) == 1 else None
        begin = stage_events["BEGIN"][0] if len(stage_events["BEGIN"]) == 1 else None
        end = stage_events["END"][0] if len(stage_events["END"]) == 1 else None
        final = stage_events["FINAL"][0]
        race = "invalid"
        if begin is not None and end is not None:
            if begin["byte_offset"] >= end["byte_offset"]:
                qualification_errors.append(
                    "lifecycle {} END does not follow BEGIN".format(lifecycle_number)
                )
            elif end["entry_count"] <= begin["entry_count"]:
                qualification_errors.append(
                    "lifecycle {} made no overlay entry progress".format(
                        lifecycle_number
                    )
                )
        if launch is not None and begin is not None and end is not None:
            if launch["byte_offset"] < begin["byte_offset"]:
                race = "launch_before_begin"
            elif launch["byte_offset"] < end["byte_offset"]:
                race = "launch_between_begin_end"
            elif launch["byte_offset"] < final["byte_offset"]:
                race = "launch_after_end"
            else:
                qualification_errors.append(
                    "lifecycle {} LAUNCH does not precede FINAL".format(
                        lifecycle_number
                    )
                )
            if race != "invalid":
                race_counts[race] += 1

        for sample in stage_events["SAMPLE"]:
            if launch is None or not (
                launch["byte_offset"] < sample["byte_offset"] < final["byte_offset"]
            ):
                qualification_errors.append(
                    "lifecycle {} SAMPLE is outside LAUNCH..FINAL".format(
                        lifecycle_number
                    )
                )

        exits = [item["record"] for item in pending if item["event"] == "exit"]
        stacks = [item["record"] for item in pending if item["event"] == "stack"]
        if len(exits) != 1:
            qualification_errors.append(
                "lifecycle {} has {} worker exits".format(
                    lifecycle_number, len(exits)
                )
            )
        if len(stacks) != 1:
            qualification_errors.append(
                "lifecycle {} has {} worker stacks".format(
                    lifecycle_number, len(stacks)
                )
            )
        if end is not None and len(exits) == 1 and not (
            end["byte_offset"] < exits[0]["byte_offset"] < final["byte_offset"]
        ):
            qualification_errors.append(
                "lifecycle {} worker exit is outside END..FINAL".format(
                    lifecycle_number
                )
            )
        if len(exits) == 1 and len(stacks) == 1 and not (
            exits[0]["byte_offset"] < stacks[0]["byte_offset"] < final["byte_offset"]
        ):
            qualification_errors.append(
                "lifecycle {} worker stack is outside EXIT..FINAL".format(
                    lifecycle_number
                )
            )

        lifecycles.append(
            {
                "index": lifecycle_number,
                "worker": (
                    expected_worker_names[lifecycle_number - 1]
                    if lifecycle_number <= expected_workers
                    else "unexpected_worker_{}".format(lifecycle_number)
                ),
                "first_line": pending[0]["record"]["line"],
                "final_line": final["line"],
                "begin_byte_offset": (
                    begin["byte_offset"] if begin is not None else None
                ),
                "end_byte_offset": end["byte_offset"] if end is not None else None,
                "sample_count": len(stage_events["SAMPLE"]),
                "race": race,
                "structure_valid": len(qualification_errors) == errors_before,
            }
        )
        pending = []

    if pending:
        qualification_errors.append(
            "{} events remain after the last complete lifecycle".format(len(pending))
        )
    if len(lifecycles) != expected_workers:
        qualification_errors.append(
            "complete lifecycle count {} != {}".format(
                len(lifecycles), expected_workers
            )
        )

    # A named assertion is credited only when its exact marker appears once,
    # in order, inside the lifecycle of the worker assigned to that test.  A
    # marker from an earlier/later worker therefore cannot satisfy the batch,
    # even if a structured result is mocked or the serial stream is reordered.

    expected_selected_tests = {
        test.name for test in plan.python_tests
    }
    unexpected_test_markers = [
        record
        for record in test_marker_records
        if record["test"] not in expected_selected_tests
    ]
    if unexpected_test_markers:
        qualification_errors.append(
            "{} markers belong to tests outside this qualification plan".format(
                len(unexpected_test_markers)
            )
        )

    bound_test_marker_count = 0
    for worker_index, worker in enumerate(plan.python_workers):
        expected_tests = [test.name for test in worker.tests]
        if worker_index >= len(lifecycles):
            qualification_errors.append(
                "worker {} has no complete lifecycle for test markers".format(
                    worker.name
                )
            )
            continue
        lifecycle = lifecycles[worker_index]
        begin_offset = lifecycle["begin_byte_offset"]
        end_offset = lifecycle["end_byte_offset"]
        if begin_offset is None or end_offset is None:
            observed = []
            lifecycle_records = []
        else:
            lifecycle_records = [
                record
                for record in test_marker_records
                if begin_offset < record["byte_offset"] < end_offset
            ]
            observed = [record["test"] for record in lifecycle_records]
        bound_test_marker_count += len(lifecycle_records)
        lifecycle["test_markers"] = observed
        lifecycle["test_marker_offsets"] = [
            record["byte_offset"] for record in lifecycle_records
        ]
        if observed != expected_tests:
            qualification_errors.append(
                "worker {} test markers {} != {}".format(
                    worker.name, observed, expected_tests
                )
            )

    if bound_test_marker_count != len(test_marker_records):
        qualification_errors.append(
            "{} Python test markers are outside assigned regular workers".format(
                len(test_marker_records) - bound_test_marker_count
            )
        )
    if len(test_marker_records) != len(plan.python_tests):
        qualification_errors.append(
            "Python test marker count {} != {}".format(
                len(test_marker_records), len(plan.python_tests)
            )
        )

    # Bind all non-regular assertion evidence to the exact successful worker
    # lifecycle that is supposed to emit it.  Counts alone are insufficient:
    # without these bounds, an earlier worker or unrelated console noise could
    # satisfy a later restart, entropy, or contention claim.

    entropy_fingerprint_evidence = None
    entropy_selected = any(
        test.name == "hardware_entropy" for test in plan.python_tests
    )
    entropy_assignments = [
        (worker_index, test_index)
        for worker_index, worker in enumerate(plan.python_workers)
        for test_index, test in enumerate(worker.tests)
        if test.name == "hardware_entropy"
    ]
    expected_entropy_count = 1 if entropy_selected else 0
    if len(entropy_fingerprint_records) != expected_entropy_count:
        qualification_errors.append(
            "entropy fingerprint count {} != {}".format(
                len(entropy_fingerprint_records), expected_entropy_count
            )
        )
    if entropy_selected and len(entropy_assignments) != 1:
        qualification_errors.append(
            "hardware entropy must be assigned to exactly one regular worker"
        )
    elif not entropy_selected and entropy_assignments:
        qualification_errors.append(
            "hardware entropy is assigned outside this qualification plan"
        )

    if len(entropy_fingerprint_records) == 1:
        entropy_fingerprint_evidence = entropy_fingerprint_records[0][
            "fingerprint"
        ]
    if (
        entropy_selected
        and len(entropy_assignments) == 1
        and len(entropy_fingerprint_records) == 1
    ):
        worker_index, test_index = entropy_assignments[0]
        if worker_index >= len(lifecycles):
            qualification_errors.append(
                "hardware entropy worker has no complete lifecycle"
            )
        else:
            lifecycle = lifecycles[worker_index]
            begin_offset = lifecycle["begin_byte_offset"]
            end_offset = lifecycle["end_byte_offset"]
            marker_offsets = lifecycle.get("test_marker_offsets", [])
            fingerprint_offset = entropy_fingerprint_records[0]["byte_offset"]
            if (
                begin_offset is None
                or end_offset is None
                or test_index >= len(marker_offsets)
            ):
                qualification_errors.append(
                    "hardware entropy fingerprint cannot be bound to its PASS marker"
                )
            else:
                lower_offset = (
                    marker_offsets[test_index - 1]
                    if test_index > 0
                    else begin_offset
                )
                pass_offset = marker_offsets[test_index]
                if not lower_offset < fingerprint_offset < pass_offset < end_offset:
                    qualification_errors.append(
                        "hardware entropy fingerprint is outside its assigned "
                        "test interval"
                    )
                else:
                    lifecycle["entropy_fingerprint"] = (
                        entropy_fingerprint_evidence
                    )

    expected_restart_count = (
        RESTART_STRESS_COUNT if plan.include_restart_stress else 0
    )
    if len(restart_marker_records) != expected_restart_count:
        qualification_errors.append(
            "restart marker count {} != {}".format(
                len(restart_marker_records), expected_restart_count
            )
        )
    if plan.include_restart_stress:
        observed_iterations = [
            record["iteration"] for record in restart_marker_records
        ]
        if observed_iterations != list(range(RESTART_STRESS_COUNT)):
            qualification_errors.append(
                "restart markers are not the exact ordered 0..{} sequence".format(
                    RESTART_STRESS_COUNT - 1
                )
            )
        for iteration in range(RESTART_STRESS_COUNT):
            worker_name = "restart_stress_{}".format(iteration)
            worker_index = expected_worker_names.index(worker_name)
            matching = [
                record
                for record in restart_marker_records
                if record["iteration"] == iteration
            ]
            if worker_index >= len(lifecycles) or len(matching) != 1:
                qualification_errors.append(
                    "{} cannot be bound to one complete lifecycle".format(
                        worker_name
                    )
                )
                continue
            lifecycle = lifecycles[worker_index]
            begin_offset = lifecycle["begin_byte_offset"]
            end_offset = lifecycle["end_byte_offset"]
            marker_offset = matching[0]["byte_offset"]
            if (
                begin_offset is None
                or end_offset is None
                or not begin_offset < marker_offset < end_offset
            ):
                qualification_errors.append(
                    "{} marker is outside its assigned lifecycle".format(
                        worker_name
                    )
                )
            else:
                lifecycle["restart_marker_iteration"] = iteration

    concurrency_events = {
        event: [
            record
            for record in concurrency_marker_records
            if record["event"] == event
        ]
        for event in ("holder", "busy", "done", "second_ran", "post")
    }
    concurrency_evidence = None
    if not plan.include_concurrency:
        if concurrency_marker_records:
            qualification_errors.append(
                "concurrency markers are present outside this qualification plan"
            )
    else:
        expected_concurrency_counts = {
            "holder": 1,
            "busy": 1,
            "done": 1,
            "second_ran": 0,
            "post": 1,
        }
        for event, expected_count in expected_concurrency_counts.items():
            if len(concurrency_events[event]) != expected_count:
                qualification_errors.append(
                    "concurrency {} marker count {} != {}".format(
                        event,
                        len(concurrency_events[event]),
                        expected_count,
                    )
                )

        if all(
            len(concurrency_events[event]) == 1
            for event in ("holder", "busy", "done", "post")
        ):
            holder = concurrency_events["holder"][0]
            busy = concurrency_events["busy"][0]
            done = concurrency_events["done"][0]
            post = concurrency_events["post"][0]
            holder_index = expected_worker_names.index(
                CONCURRENCY_HOLDER_TEST_NAME
            )
            post_index = expected_worker_names.index(CONCURRENCY_POST_TEST_NAME)
            if holder_index >= len(lifecycles) or post_index >= len(lifecycles):
                qualification_errors.append(
                    "concurrency markers have no complete assigned lifecycles"
                )
            else:
                holder_lifecycle = lifecycles[holder_index]
                post_lifecycle = lifecycles[post_index]
                holder_begin = holder_lifecycle["begin_byte_offset"]
                holder_end = holder_lifecycle["end_byte_offset"]
                post_begin = post_lifecycle["begin_byte_offset"]
                post_end = post_lifecycle["end_byte_offset"]
                holder_order_valid = (
                    holder_begin is not None
                    and holder_end is not None
                    and holder_begin
                    < holder["byte_offset"]
                    < busy["byte_offset"]
                    < done["byte_offset"]
                    < holder_end
                    and busy["code"] > 0
                )
                post_order_valid = (
                    post_begin is not None
                    and post_end is not None
                    and post_begin < post["byte_offset"] < post_end
                )
                if not holder_order_valid:
                    qualification_errors.append(
                        "concurrency holder/busy/done markers are outside or "
                        "out of order in the holder lifecycle"
                    )
                if not post_order_valid:
                    qualification_errors.append(
                        "concurrency post marker is outside its assigned lifecycle"
                    )
                if holder_order_valid and post_order_valid:
                    holder_lifecycle["concurrency_markers"] = [
                        "holder",
                        "busy",
                        "done",
                    ]
                    post_lifecycle["concurrency_markers"] = ["post"]
                    concurrency_evidence = {
                        "holder_marker": CONCURRENCY_HOLDER_MARKER,
                        "busy_marker": "{}{}".format(
                            CONCURRENCY_BUSY_PREFIX, busy["code"]
                        ),
                        "done_marker": CONCURRENCY_DONE_MARKER,
                        "post_marker": CONCURRENCY_POST_MARKER,
                    }

    complete_main_lifecycles = 0
    complete_pathconfig_lifecycles = 0
    complete_startup_fill_lifecycles = 0
    complete_fill_time_calls = 0
    complete_runtime_fill_time_calls = 0
    bound_importlib_pass_records = 0
    bound_pathconfig_records = 0
    bound_main_records = 0
    bound_fill_time_records = 0
    first_fill_time_mismatch = None

    def qualify_fill_time_sequence(
        lifecycle_number, phase, phase_records, minimum_calls=0
    ):
        nonlocal first_fill_time_mismatch

        whole_call_count = len(phase_records) % FILL_TIME_RECORDS_PER_CALL == 0
        if not whole_call_count:
            qualification_errors.append(
                "lifecycle {} {} fill_time record count {} is not a whole "
                "{}-record call".format(
                    lifecycle_number,
                    phase,
                    len(phase_records),
                    FILL_TIME_RECORDS_PER_CALL,
                )
            )

        mismatch = None
        for position, actual in enumerate(phase_records):
            expected_event = FILL_TIME_SUCCESS_EVENTS[
                position % FILL_TIME_RECORDS_PER_CALL
            ]
            if actual["event"] != expected_event:
                mismatch = {
                    "lifecycle": lifecycle_number,
                    "phase": phase,
                    "position": position,
                    "call": position // FILL_TIME_RECORDS_PER_CALL + 1,
                    "record_in_call": position % FILL_TIME_RECORDS_PER_CALL,
                    "line": actual["line"],
                    "expected": expected_event,
                    "actual": actual["event"],
                }
                if first_fill_time_mismatch is None:
                    first_fill_time_mismatch = mismatch
                qualification_errors.append(
                    "lifecycle {} {} fill_time sequence first differs at call "
                    "{} record {} (line {}): expected {}, got {}".format(
                        lifecycle_number,
                        phase,
                        mismatch["call"],
                        mismatch["record_in_call"],
                        mismatch["line"],
                        mismatch["expected"],
                        mismatch["actual"],
                    )
                )
                break

        complete_calls = 0
        for start in range(0, len(phase_records), FILL_TIME_RECORDS_PER_CALL):
            call = phase_records[start : start + FILL_TIME_RECORDS_PER_CALL]
            if len(call) != FILL_TIME_RECORDS_PER_CALL:
                break
            if [record["event"] for record in call] == list(
                FILL_TIME_SUCCESS_EVENTS
            ):
                complete_calls += 1
            else:
                break
        if complete_calls < minimum_calls:
            qualification_errors.append(
                "lifecycle {} {} has {} complete fill_time calls; at least {} "
                "required".format(
                    lifecycle_number, phase, complete_calls, minimum_calls
                )
            )
        sequence_exact = (
            whole_call_count
            and mismatch is None
            and complete_calls
            == len(phase_records) // FILL_TIME_RECORDS_PER_CALL
        )
        return complete_calls, sequence_exact

    for lifecycle in lifecycles:
        lifecycle_number = lifecycle["index"]
        begin_offset = lifecycle["begin_byte_offset"]
        end_offset = lifecycle["end_byte_offset"]
        if begin_offset is None or end_offset is None:
            lifecycle.update(
                {
                    "importlib_pass_marker_count": 0,
                    "pathconfig_begin_marker_count": 0,
                    "pathconfig_pass_marker_count": 0,
                    "pathconfig_fail_marker_count": 0,
                    "pathconfig_valid": False,
                    "main_marker_count": 0,
                    "startup_fill_time_call_count": 0,
                    "runtime_fill_time_call_count": 0,
                }
            )
            continue

        lifecycle_init = [
            record
            for record in init_records
            if begin_offset < record["byte_offset"] < end_offset
        ]
        lifecycle_importlib = [
            record
            for record in importlib_pass_records
            if begin_offset < record["byte_offset"] < end_offset
        ]
        lifecycle_pathconfig = [
            record
            for record in pathconfig_records
            if begin_offset < record["byte_offset"] < end_offset
        ]
        lifecycle_main = [
            record
            for record in main_records
            if begin_offset < record["byte_offset"] < end_offset
        ]
        lifecycle_fill = [
            record
            for record in fill_time_records
            if begin_offset < record["byte_offset"] < end_offset
        ]
        bound_importlib_pass_records += len(lifecycle_importlib)
        bound_pathconfig_records += len(lifecycle_pathconfig)
        bound_main_records += len(lifecycle_main)
        bound_fill_time_records += len(lifecycle_fill)

        local_init_exact = len(lifecycle_init) == len(expected_init_cycle) and all(
            all(actual.get(key) == value for key, value in expected.items())
            for actual, expected in zip(lifecycle_init, expected_init_cycle)
        )
        if not local_init_exact:
            qualification_errors.append(
                "lifecycle {} does not contain one exact {}-record CPython "
                "initialization sequence".format(
                    lifecycle_number, len(expected_init_cycle)
                )
            )

        if len(lifecycle_importlib) != 1:
            qualification_errors.append(
                "lifecycle {} IMPORTLIB:PASS marker count {} != 1".format(
                    lifecycle_number, len(lifecycle_importlib)
                )
            )

        lifecycle_pathconfig_events = [
            record["event"] for record in lifecycle_pathconfig
        ]
        expected_pathconfig_events = ["BEGIN", "PASS"]
        pathconfig_sequence_exact = (
            lifecycle_pathconfig_events == expected_pathconfig_events
        )
        if not pathconfig_sequence_exact:
            qualification_errors.append(
                "lifecycle {} PATHCONFIG markers {} != {}".format(
                    lifecycle_number,
                    lifecycle_pathconfig_events,
                    expected_pathconfig_events,
                )
            )

        pathconfig_ordered = False
        if (
            len(lifecycle_importlib) == 1
            and pathconfig_sequence_exact
            and len(lifecycle_main) == 1
        ):
            pathconfig_ordered = (
                lifecycle_importlib[0]["byte_offset"]
                < lifecycle_pathconfig[0]["byte_offset"]
                < lifecycle_pathconfig[1]["byte_offset"]
                < lifecycle_main[0]["byte_offset"]
            )
            if not pathconfig_ordered:
                qualification_errors.append(
                    "lifecycle {} PATHCONFIG BEGIN/PASS are not ordered between "
                    "IMPORTLIB:PASS and MAIN:PASS".format(lifecycle_number)
                )
        if pathconfig_ordered:
            complete_pathconfig_lifecycles += 1

        if len(lifecycle_main) != 1:
            qualification_errors.append(
                "lifecycle {} MAIN:PASS marker count {} != 1".format(
                    lifecycle_number, len(lifecycle_main)
                )
            )
            main_offset = end_offset
        else:
            complete_main_lifecycles += 1
            main_offset = lifecycle_main[0]["byte_offset"]
            if lifecycle_init and lifecycle_init[-1]["byte_offset"] >= main_offset:
                qualification_errors.append(
                    "lifecycle {} MAIN:PASS does not follow CPython initialization".format(
                        lifecycle_number
                    )
                )
            for marker_offset in lifecycle.get("test_marker_offsets", ()):
                if marker_offset <= main_offset:
                    qualification_errors.append(
                        "lifecycle {} Python test marker does not follow "
                        "MAIN:PASS".format(lifecycle_number)
                    )

        startup_fill = [
            record for record in lifecycle_fill if record["byte_offset"] < main_offset
        ]
        runtime_fill = [
            record for record in lifecycle_fill if record["byte_offset"] > main_offset
        ]
        if (
            startup_fill
            and lifecycle_init
            and startup_fill[0]["byte_offset"] <= lifecycle_init[-1]["byte_offset"]
        ):
            qualification_errors.append(
                "lifecycle {} startup fill_time diagnostics do not follow "
                "CPython initialization".format(lifecycle_number)
            )

        startup_calls, startup_sequence_exact = qualify_fill_time_sequence(
            lifecycle_number,
            "startup",
            startup_fill,
            MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE,
        )
        runtime_calls, runtime_sequence_exact = qualify_fill_time_sequence(
            lifecycle_number, "runtime", runtime_fill
        )
        complete_fill_time_calls += startup_calls + runtime_calls
        complete_runtime_fill_time_calls += runtime_calls
        if (
            startup_calls >= MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE
            and startup_sequence_exact
        ):
            complete_startup_fill_lifecycles += 1

        lifecycle.update(
            {
                "importlib_pass_marker_count": len(lifecycle_importlib),
                "pathconfig_begin_marker_count": sum(
                    record["event"] == "BEGIN"
                    for record in lifecycle_pathconfig
                ),
                "pathconfig_pass_marker_count": sum(
                    record["event"] == "PASS"
                    for record in lifecycle_pathconfig
                ),
                "pathconfig_fail_marker_count": sum(
                    record["event"] == "FAIL"
                    for record in lifecycle_pathconfig
                ),
                "pathconfig_valid": pathconfig_ordered,
                "main_marker_count": len(lifecycle_main),
                "startup_fill_time_call_count": startup_calls,
                "runtime_fill_time_call_count": runtime_calls,
                "startup_diagnostics_valid": (
                    local_init_exact
                    and pathconfig_ordered
                    and len(lifecycle_main) == 1
                    and startup_calls
                    >= MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE
                    and startup_sequence_exact
                    and runtime_sequence_exact
                ),
            }
        )

    probe_begin_records = [
        record for record in softfloat_probe_records if record["event"] == "BEGIN"
    ]
    probe_pass_records = [
        record for record in softfloat_probe_records if record["event"] == "PASS"
    ]
    softfloat_probe_evidence = {
        "test": SOFTFLOAT_PROBE_TEST_NAME,
        "begin_marker_count": len(probe_begin_records),
        "pass_marker_count": len(probe_pass_records),
        "malformed_marker_count": len(softfloat_probe_malformed),
        "expected_fill_time_call_count": SOFTFLOAT_PROBE_FILL_TIME_CALLS,
        "fill_time_call_count": 0,
        "fill_time_record_count": 0,
        "ordered_within_lifecycle": False,
        "sequence_exact": False,
        "valid": False,
    }
    if len(probe_begin_records) != 1:
        qualification_errors.append(
            "soft-float probe BEGIN marker count {} != 1".format(
                len(probe_begin_records)
            )
        )
    if len(probe_pass_records) != 1:
        qualification_errors.append(
            "soft-float probe PASS marker count {} != 1".format(
                len(probe_pass_records)
            )
        )

    probe_assignments = [
        (worker_index, test_index)
        for worker_index, worker in enumerate(plan.python_workers)
        for test_index, test in enumerate(worker.tests)
        if test.name == SOFTFLOAT_PROBE_TEST_NAME
    ]
    if len(probe_assignments) != 1:
        qualification_errors.append(
            "soft-float probe test must be assigned to exactly one regular worker"
        )
    elif len(probe_begin_records) == 1 and len(probe_pass_records) == 1:
        worker_index, test_index = probe_assignments[0]
        softfloat_probe_evidence["worker_index"] = worker_index + 1
        if worker_index >= len(lifecycles):
            qualification_errors.append(
                "soft-float probe worker has no complete lifecycle"
            )
        else:
            lifecycle = lifecycles[worker_index]
            begin_offset = lifecycle["begin_byte_offset"]
            end_offset = lifecycle["end_byte_offset"]
            marker_offsets = lifecycle.get("test_marker_offsets", [])
            probe_begin_offset = probe_begin_records[0]["byte_offset"]
            probe_pass_offset = probe_pass_records[0]["byte_offset"]
            lifecycle_main = [
                record
                for record in main_records
                if begin_offset is not None
                and end_offset is not None
                and begin_offset < record["byte_offset"] < end_offset
            ]
            arithmetic_pass_offset = (
                marker_offsets[test_index]
                if test_index < len(marker_offsets)
                else None
            )
            ordered = (
                begin_offset is not None
                and end_offset is not None
                and len(lifecycle_main) == 1
                and arithmetic_pass_offset is not None
                and len(interactive_script_begins) == 1
                and len(interactive_script_passes) == 1
                and begin_offset
                < lifecycle_main[0]["byte_offset"]
                < interactive_script_begins[0]["byte_offset"]
                < probe_begin_offset
                < probe_pass_offset
                < arithmetic_pass_offset
                < interactive_script_passes[0]["byte_offset"]
                < end_offset
            )
            softfloat_probe_evidence["ordered_within_lifecycle"] = ordered
            if not ordered:
                qualification_errors.append(
                    "soft-float probe is not ordered MAIN < SCRIPT_BEGIN < "
                    "BEGIN < PASS < ARITH:PASS < SCRIPT_PASS inside its "
                    "assigned lifecycle"
                )

            probe_fill = [
                record
                for record in fill_time_records
                if probe_begin_offset
                < record["byte_offset"]
                < probe_pass_offset
            ]
            expected_probe_events = list(FILL_TIME_SUCCESS_EVENTS) * (
                SOFTFLOAT_PROBE_FILL_TIME_CALLS
            )
            actual_probe_events = [record["event"] for record in probe_fill]
            sequence_exact = actual_probe_events == expected_probe_events
            softfloat_probe_evidence["fill_time_record_count"] = len(probe_fill)
            softfloat_probe_evidence["fill_time_call_count"] = (
                len(probe_fill) // FILL_TIME_RECORDS_PER_CALL
                if sequence_exact
                else 0
            )
            softfloat_probe_evidence["sequence_exact"] = sequence_exact
            if len(probe_fill) != len(expected_probe_events):
                qualification_errors.append(
                    "soft-float probe fill_time record count {} != {} "
                    "({} exact calls)".format(
                        len(probe_fill),
                        len(expected_probe_events),
                        SOFTFLOAT_PROBE_FILL_TIME_CALLS,
                    )
                )
            elif not sequence_exact:
                mismatch_position = next(
                    index
                    for index, (actual, expected) in enumerate(
                        zip(actual_probe_events, expected_probe_events)
                    )
                    if actual != expected
                )
                qualification_errors.append(
                    "soft-float probe fill_time sequence differs at record {}: "
                    "expected {}, got {}".format(
                        mismatch_position,
                        expected_probe_events[mismatch_position],
                        actual_probe_events[mismatch_position],
                    )
                )
            softfloat_probe_evidence["valid"] = (
                ordered
                and sequence_exact
                and not softfloat_probe_malformed
            )

    if bound_importlib_pass_records != len(importlib_pass_records):
        qualification_errors.append(
            "{} CPython IMPORTLIB:PASS markers are outside complete "
            "lifecycles".format(
                len(importlib_pass_records) - bound_importlib_pass_records
            )
        )
    if bound_pathconfig_records != len(pathconfig_records):
        qualification_errors.append(
            "{} CPython PATHCONFIG markers are outside complete lifecycles".format(
                len(pathconfig_records) - bound_pathconfig_records
            )
        )
    if bound_main_records != len(main_records):
        qualification_errors.append(
            "{} CPython MAIN:PASS markers are outside complete lifecycles".format(
                len(main_records) - bound_main_records
            )
        )
    if bound_fill_time_records != len(fill_time_records):
        qualification_errors.append(
            "{} fill_time diagnostic records are outside complete lifecycles".format(
                len(fill_time_records) - bound_fill_time_records
            )
        )

    if len(interactive_banners) != 1:
        qualification_errors.append(
            "interactive REPL banner count {} != 1".format(
                len(interactive_banners)
            )
        )
    if len(interactive_expressions) != 1:
        qualification_errors.append(
            "interactive REPL expression marker count {} != 1".format(
                len(interactive_expressions)
            )
        )
    if len(interactive_script_begins) != 1:
        qualification_errors.append(
            "interactive REPL script BEGIN marker count {} != 1".format(
                len(interactive_script_begins)
            )
        )
    if len(interactive_script_passes) != 1:
        qualification_errors.append(
            "interactive REPL script PASS marker count {} != 1".format(
                len(interactive_script_passes)
            )
        )
    if len(interactive_prompt_offsets) != expected_interactive_prompt_count:
        qualification_errors.append(
            "interactive REPL prompt count {} != {}".format(
                len(interactive_prompt_offsets), expected_interactive_prompt_count
            )
        )
    if not interactive_echoes_exact:
        qualification_errors.append(
            "interactive REPL setup/execution command echoes are not exact and ordered"
        )

    interactive_order_valid = False
    interactive_lifecycle_index = expected_worker_names.index(
        INTERACTIVE_REPL_TEST_NAME
    )
    if (
        len(lifecycles) > interactive_lifecycle_index
        and len(interactive_banners) == 1
        and len(interactive_expressions) == 1
        and len(interactive_script_begins) == 1
        and len(interactive_script_passes) == 1
        and len(interactive_prompt_offsets) == expected_interactive_prompt_count
    ):
        interactive_lifecycle = lifecycles[interactive_lifecycle_index]
        begin_offset = interactive_lifecycle["begin_byte_offset"]
        end_offset = interactive_lifecycle["end_byte_offset"]
        if begin_offset is not None and end_offset is not None:
            interactive_test_offsets = interactive_lifecycle.get(
                "test_marker_offsets", []
            )
            tests_inside_script = (
                len(interactive_test_offsets)
                == len(plan.python_workers[interactive_lifecycle_index].tests)
                and all(
                    interactive_script_begins[0]["byte_offset"]
                    < offset
                    < interactive_script_passes[0]["byte_offset"]
                    for offset in interactive_test_offsets
                )
            )
            interactive_order_valid = (
                begin_offset
                < interactive_banners[0]["byte_offset"]
                < interactive_prompt_offsets[0]
                < interactive_prompt_offsets[expected_repl_setup_count]
                < interactive_script_begins[0]["byte_offset"]
                < interactive_script_passes[0]["byte_offset"]
                < interactive_prompt_offsets[expected_repl_setup_count + 1]
                < interactive_expressions[0]["byte_offset"]
                < interactive_prompt_offsets[expected_repl_setup_count + 2]
                < end_offset
                and tests_inside_script
                and interactive_echoes_exact
            )
    if not interactive_order_valid:
        qualification_errors.append(
            "interactive REPL banner/script/prompts/expression are outside its lifecycle"
        )
    if (
        plan.repl_live_hold_seconds > 0
        and len(lifecycles) > interactive_lifecycle_index
        and lifecycles[interactive_lifecycle_index]["sample_count"] < 1
    ):
        qualification_errors.append(
            "interactive REPL smoke lifecycle has no live SAMPLE telemetry"
        )

    interactive_evidence = {
        "worker_index": interactive_lifecycle_index + 1,
        "worker_name": INTERACTIVE_REPL_TEST_NAME,
        "banner": (
            interactive_banners[0]["text"]
            if len(interactive_banners) == 1
            else None
        ),
        "expression_marker": (
            interactive_expressions[0]["text"]
            if len(interactive_expressions) == 1
            else None
        ),
        "prompt_count": len(interactive_prompt_offsets),
        "expected_prompt_count": expected_interactive_prompt_count,
        "setup_prompt_count": max(0, len(interactive_prompt_offsets) - 3),
        "setup_command_count": expected_repl_setup_count,
        "setup_command_bytes": sum(
            len((command + "\r").encode("ascii"))
            for command in expected_repl_setup_commands
        ),
        "setup_echoes_exact": interactive_echoes_exact,
        "script_begin_marker": (
            INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER
            if len(interactive_script_begins) == 1
            else None
        ),
        "script_pass_marker": (
            INTERACTIVE_REPL_SCRIPT_PASS_MARKER
            if len(interactive_script_passes) == 1
            else None
        ),
        "ordered_within_lifecycle": interactive_order_valid,
    }

    if first is None or any(
        deltas.get(field, 0) <= 0
        for field in ("entry_count", "load_count", "load_bytes")
    ):
        qualification_errors.append(
            "whole-run entry/load/load-byte deltas must all be positive"
        )
    if classification != "overlay-load-progress":
        qualification_errors.append(
            "classification {} is not overlay-load-progress".format(classification)
        )

    final_xmem = xmem_records[-1] if xmem_records else None
    if final_xmem is None:
        qualification_errors.append("xmem telemetry has no complete stats record")
        xmem_hit_rate = None
    else:
        for field in ("hits", "misses", "fills", "writes"):
            if final_xmem[field] <= 0:
                qualification_errors.append(
                    "final xmem {} counter must be positive".format(field)
                )
        read_lookups = final_xmem["hits"] + final_xmem["misses"]
        xmem_hit_rate = (
            final_xmem["hits"] / read_lookups if read_lookups else None
        )

    record_valid = (
        not malformed and not xmem_malformed and not validation_errors
    )
    serial_qualification_valid = record_valid and not qualification_errors
    if result is not None:
        result_validation_errors.extend(
            validate_python_hil_result(
                result,
                worker_stacks,
                interactive_evidence,
                plan,
                entropy_fingerprint_evidence=entropy_fingerprint_evidence,
                concurrency_evidence=concurrency_evidence,
            )
        )
    qualification_valid = (
        serial_qualification_valid and not result_validation_errors
    )

    analysis = {
        "valid": record_valid,
        "record_valid": record_valid,
        "serial_qualification_valid": serial_qualification_valid,
        "qualification_valid": qualification_valid,
        "result_checked": result is not None,
        "record_count": len(records),
        "stats_count": len(stats_records),
        "expected_stats_count": 4 * expected_workers
        + stage_counts["SAMPLE"],
        "error_count": sum(record["kind"] == "error" for record in records),
        "malformed_count": len(malformed),
        "worker_malformed_count": len(worker_malformed),
        "python_test_marker_count": len(test_marker_records),
        "entropy_fingerprint_count": len(entropy_fingerprint_records),
        "entropy_fingerprint": entropy_fingerprint_evidence,
        "restart_marker_count": len(restart_marker_records),
        "concurrency_marker_count": len(concurrency_marker_records),
        "concurrency_evidence": concurrency_evidence,
        "validation_error_count": len(validation_errors),
        "qualification_error_count": len(qualification_errors),
        "result_validation_error_count": len(result_validation_errors),
        "stages": [record["stage"] for record in records],
        "stage_counts": stage_counts,
        "xmem_stage_counts": xmem_stage_counts,
        "expected_lifecycle_count": expected_workers,
        "expected_worker_names": list(expected_worker_names),
        "lifecycle_count": len(lifecycles),
        "worker_exit_count": len(worker_exits),
        "worker_stack_count": len(worker_stacks),
        "interactive_repl": interactive_evidence,
        "interactive_echo_records": interactive_echo_records,
        "softfloat_probe": softfloat_probe_evidence,
        "cpython_initialization": {
            "static_type_count": PYTHON_STATIC_TYPE_COUNT,
            "records_per_lifecycle": len(expected_init_cycle),
            "expected_record_count": expected_init_records,
            "record_count": len(init_records),
            "malformed_count": len(init_malformed),
            "complete_lifecycle_count": complete_init_lifecycles,
            "first_mismatch": first_init_mismatch,
        },
        "cpython_startup": {
            "importlib_pass_marker_count": len(importlib_pass_records),
            "importlib_malformed_count": len(importlib_malformed),
            "pathconfig_begin_marker_count": len(
                pathconfig_event_records["BEGIN"]
            ),
            "pathconfig_pass_marker_count": len(
                pathconfig_event_records["PASS"]
            ),
            "pathconfig_fail_marker_count": len(
                pathconfig_event_records["FAIL"]
            ),
            "pathconfig_malformed_count": len(pathconfig_malformed),
            "complete_pathconfig_lifecycle_count": (
                complete_pathconfig_lifecycles
            ),
            "main_marker_count": len(main_records),
            "main_malformed_count": len(main_malformed),
            "complete_main_lifecycle_count": complete_main_lifecycles,
            "minimum_startup_fill_time_calls_per_lifecycle": (
                MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE
            ),
            "fill_time_records_per_call": FILL_TIME_RECORDS_PER_CALL,
            "minimum_startup_fill_time_call_count": (
                expected_workers * MIN_STARTUP_FILL_TIME_CALLS_PER_LIFECYCLE
            ),
            "fill_time_record_count": len(fill_time_records),
            "fill_time_malformed_count": len(fill_time_malformed),
            "complete_fill_time_call_count": complete_fill_time_calls,
            "complete_runtime_fill_time_call_count": (
                complete_runtime_fill_time_calls
            ),
            "complete_startup_fill_time_lifecycle_count": (
                complete_startup_fill_lifecycles
            ),
            "first_fill_time_mismatch": first_fill_time_mismatch,
        },
        "xmem_cache": {
            "record_count": len(xmem_records),
            "error_count": len(xmem_errors),
            "malformed_count": len(xmem_malformed),
            "hit_rate": xmem_hit_rate,
            "final": (
                {
                    key: final_xmem[key]
                    for key in ("hits", "misses", "fills", "writes", "bypasses")
                }
                if final_xmem is not None
                else None
            ),
        },
        "trailing_fragment_bytes": len(trailing_fragment),
        "trailing_fragment_markers": trailing_markers,
        "race_counts": race_counts,
        "classification": classification,
        "deltas": deltas,
    }
    if last is not None:
        analysis["last"] = {
            "stage": last["stage"],
            "current_depth": last["current_depth"],
            "maximum_depth": last["maximum_depth"],
            "loaded_group": last["loaded_group"],
            "loading_group": last["loading_group"],
            "transition": last["transition"],
            "last_error": last["last_error"],
        }

    return {
        "records": records,
        "malformed": malformed,
        "validation_errors": validation_errors,
        "qualification_errors": qualification_errors,
        "result_validation_errors": result_validation_errors,
        "worker_exits": worker_exits,
        "worker_stacks": worker_stacks,
        "worker_malformed": worker_malformed,
        "test_marker_records": test_marker_records,
        "entropy_fingerprint_records": entropy_fingerprint_records,
        "entropy_fingerprint_malformed": entropy_fingerprint_malformed,
        "restart_marker_records": restart_marker_records,
        "restart_marker_malformed": restart_marker_malformed,
        "concurrency_marker_records": concurrency_marker_records,
        "concurrency_marker_malformed": concurrency_marker_malformed,
        "softfloat_probe_records": softfloat_probe_records,
        "softfloat_probe_malformed": softfloat_probe_malformed,
        "softfloat_probe": softfloat_probe_evidence,
        "interactive_repl": interactive_evidence,
        "cpython_initialization": analysis["cpython_initialization"],
        "cpython_startup": analysis["cpython_startup"],
        "init_records": init_records,
        "init_malformed": init_malformed,
        "importlib_pass_records": importlib_pass_records,
        "importlib_malformed": importlib_malformed,
        "pathconfig_records": pathconfig_records,
        "pathconfig_malformed": pathconfig_malformed,
        "main_records": main_records,
        "main_malformed": main_malformed,
        "fill_time_records": fill_time_records,
        "fill_time_malformed": fill_time_malformed,
        "xmem_records": xmem_records,
        "xmem_errors": xmem_errors,
        "xmem_malformed": xmem_malformed,
        "lifecycles": lifecycles,
        "analysis": analysis,
    }


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def upload_preamble(size: int, crc32: int) -> bytes:
    if not 192 <= size <= CONTAINER_CAPACITY:
        raise PythonHilError("container size is outside the board backing window")
    return UPLOAD_HEADER.pack(
        UPLOAD_MAGIC,
        UPLOAD_PROTOCOL,
        UPLOAD_HEADER.size,
        size,
        crc32,
        0,
    )


def upload_frames(stream: BinaryIO, size: int) -> Iterable[Tuple[int, bytes]]:
    offset = 0
    while offset < size:
        payload = stream.read(min(UPLOAD_FRAME_SIZE, size - offset))
        if len(payload) != min(UPLOAD_FRAME_SIZE, size - offset):
            raise PythonHilError("container changed or became truncated during upload")
        checksum = binascii.crc32(payload) & 0xFFFFFFFF
        yield offset + len(payload), UPLOAD_FRAME.pack(
            offset, len(payload), checksum
        ) + payload
        offset += len(payload)
    if stream.read(1):
        raise PythonHilError("container grew during upload")


def send_logical_frame(
    session: "SerialSession", frame: bytes, deadline: Optional[float] = None
) -> None:
    """Stream one logical frame through bounded lower-ring-sized writes.

    The real serial session uses readiness-gated nonblocking descriptor writes
    so the monotonic deadline also bounds a wedged tty queue.  Every subsequent
    wire chunk rechecks the same overall deadline.  During protocol-v3 payload
    transfer the target consumes the lower ring directly into the unpublished
    overlay slot, so adding a host sleep would only idle the wire and is neither
    part of correctness nor the transport contract.
    """

    for offset in range(0, len(frame), UPLOAD_WIRE_CHUNK_SIZE):
        if deadline is not None and time.monotonic() >= deadline:
            raise PythonHilError("container upload exceeded its deadline")
        end = min(offset + UPLOAD_WIRE_CHUNK_SIZE, len(frame))
        writer = getattr(session, "write_blocking", session.write)
        writer(frame[offset:end], deadline=deadline)
        if end < len(frame) and UPLOAD_CHUNK_PAUSE_SECONDS > 0:
            time.sleep(UPLOAD_CHUNK_PAUSE_SECONDS)


def faulted_upload_frame(frame: bytes, kind: str) -> bytes:
    """Return a byte-count-preserving deterministic invalid frame."""

    if len(frame) < UPLOAD_FRAME.size:
        raise PythonHilError("generated upload frame is truncated")
    frame_offset, payload_size, checksum = UPLOAD_FRAME.unpack(
        frame[: UPLOAD_FRAME.size]
    )
    payload = frame[UPLOAD_FRAME.size :]
    if len(payload) != payload_size:
        raise PythonHilError("generated upload frame has invalid payload size")

    if kind == UPLOAD_FAULT_BAD_CRC:
        checksum ^= 1
    elif kind == UPLOAD_FAULT_BAD_OFFSET:
        frame_offset += 1
    elif kind == UPLOAD_FAULT_BAD_FINAL_SIZE:
        if payload_size >= UPLOAD_FRAME_SIZE:
            raise PythonHilError(
                "declared-size fault requires a short final upload frame"
            )
        payload_size += 1
    else:
        raise PythonHilError("unknown upload fault injection kind: {}".format(kind))

    # Only header fields are substituted.  The expected number of payload
    # bytes stays on the wire so the target can drain the complete attempt,
    # issue P2NK(current_offset), and safely accept the exact retransmission.

    return UPLOAD_FRAME.pack(frame_offset, payload_size, checksum) + payload


def send_upload_frames(
    session: "SerialSession",
    container_path: pathlib.Path,
    size: int,
    upload_timeout: float,
    ack_timeout: float,
    inject_faults: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> Mapping[str, object]:
    """Send logical frames one at a time with bounded explicit-NACK retry."""

    if inject_faults and (
        size <= 2 * UPLOAD_FRAME_SIZE or size % UPLOAD_FRAME_SIZE == 0
    ):
        raise PythonHilError(
            "upload fault qualification requires two full frames and a "
            "distinct short final frame"
        )

    started = time.monotonic()
    deadline = started + upload_timeout
    frame_count = 0
    frame_transmissions = 0
    frame_retries = 0
    injected_faults = []
    next_progress = UPLOAD_PROGRESS_INTERVAL

    with container_path.open("rb") as stream:
        for committed, frame in upload_frames(stream, size):
            frame_count += 1
            frame_offset, payload_size, _checksum = UPLOAD_FRAME.unpack(
                frame[: UPLOAD_FRAME.size]
            )
            if frame_offset + payload_size != committed:
                raise PythonHilError("generated upload frame has invalid bounds")

            retries = 0
            fault_kind = None
            if inject_faults:
                if frame_count == 1:
                    fault_kind = UPLOAD_FAULT_BAD_CRC
                elif frame_count == 2:
                    fault_kind = UPLOAD_FAULT_BAD_OFFSET
                elif committed == size and payload_size < UPLOAD_FRAME_SIZE:
                    fault_kind = UPLOAD_FAULT_BAD_FINAL_SIZE
            fault_sent = False
            while True:
                remaining = upload_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise PythonHilError("container upload exceeded its deadline")

                injected_attempt = fault_kind is not None and not fault_sent
                outbound = (
                    faulted_upload_frame(frame, fault_kind)
                    if injected_attempt
                    else frame
                )
                if injected_attempt:
                    injected_faults.append(
                        {
                            "kind": fault_kind,
                            "frame_offset": frame_offset,
                            "transmission": frame_transmissions + 1,
                        }
                    )
                    fault_sent = True
                send_logical_frame(session, outbound, deadline)
                frame_transmissions += 1

                remaining = upload_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise PythonHilError("container upload exceeded its deadline")
                raw_response = session.read_exact(
                    UPLOAD_ACK.size, min(ack_timeout, remaining)
                )
                magic, target_offset = UPLOAD_ACK.unpack(raw_response)

                if magic == UPLOAD_ACK_MAGIC:
                    if injected_attempt:
                        raise PythonHilError(
                            "target ACKed deliberately invalid upload frame: "
                            "kind={} raw={}".format(
                                fault_kind, raw_response.hex()
                            )
                        )
                    if target_offset != committed:
                        raise PythonHilError(
                            "target returned an invalid upload ACK: "
                            "expected={} raw={}".format(
                                committed, raw_response.hex()
                            )
                        )
                    break

                if magic == UPLOAD_NACK_MAGIC:
                    if target_offset != frame_offset:
                        raise PythonHilError(
                            "target returned an invalid upload NACK: "
                            "expected={} raw={}".format(
                                frame_offset, raw_response.hex()
                            )
                        )
                    if retries >= UPLOAD_FRAME_RETRIES:
                        detail = b""
                        wait_line_prefix = getattr(
                            session, "wait_line_prefix", None
                        )
                        if wait_line_prefix is not None:
                            try:
                                detail = wait_line_prefix(
                                    (b"P2PY:UPLOAD:FRAMEFAIL:",),
                                    min(ack_timeout, 2.0),
                                )
                            except PythonHilError:
                                pass
                        raise PythonHilError(
                            "target rejected upload frame at offset {} after {} "
                            "retries{}".format(
                                frame_offset,
                                retries,
                                (
                                    ": {}".format(
                                        detail.decode("ascii", "replace")
                                    )
                                    if detail
                                    else ""
                                ),
                            )
                        )
                    retries += 1
                    frame_retries += 1
                    continue

                raise PythonHilError(
                    "target returned an invalid upload response: raw={}".format(
                        raw_response.hex()
                    )
                )

            if committed >= next_progress or committed == size:
                print(
                    "P2PYHIL:UPLOAD:ACKED={}:TOTAL={}".format(committed, size),
                    flush=True,
                )
                report_progress(
                    progress_callback,
                    "upload_frames",
                    state="acked",
                    acked_bytes=committed,
                    total_bytes=size,
                    frame_count=frame_count,
                    frame_transmissions=frame_transmissions,
                    frame_retries=frame_retries,
                )
                while next_progress <= committed:
                    next_progress += UPLOAD_PROGRESS_INTERVAL

    injected_fault_kinds = [fault["kind"] for fault in injected_faults]
    if inject_faults and tuple(injected_fault_kinds) != UPLOAD_FAULT_SEQUENCE:
        raise PythonHilError("upload fault qualification sequence was incomplete")

    return {
        "frame_count": frame_count,
        "frame_transmissions": frame_transmissions,
        "frame_retries": frame_retries,
        "fault_injection_enabled": inject_faults,
        "injected_fault_count": len(injected_faults),
        "injected_fault_kinds": injected_fault_kinds,
        "injected_faults": injected_faults,
        "window_frames": UPLOAD_WINDOW_FRAMES,
        "window_count": frame_count,
        "wire_chunk_bytes": UPLOAD_WIRE_CHUNK_SIZE,
        "wire_chunk_seconds": UPLOAD_CHUNK_WIRE_SECONDS,
        "inter_chunk_gap_seconds": UPLOAD_CHUNK_GAP_SECONDS,
        "inter_chunk_pause_seconds": UPLOAD_CHUNK_PAUSE_SECONDS,
        "seconds": time.monotonic() - started,
    }


def parse_ready(line: bytes) -> Tuple[int, int, int, int, int]:
    match = READY_RE.fullmatch(line)
    if match is None:
        raise PythonHilError("target upload READY marker is malformed")
    protocol = int(match.group(1))
    base = int(match.group(2), 16)
    capacity = int(match.group(3))
    frame_size = int(match.group(4))
    baud = int(match.group(5))
    if (
        protocol != UPLOAD_PROTOCOL
        or base != CONTAINER_BASE
        or capacity != CONTAINER_CAPACITY
        or frame_size != UPLOAD_FRAME_SIZE
        or baud != RUNTIME_BAUD
    ):
        raise PythonHilError("target upload contract does not match this runner")
    return protocol, base, capacity, frame_size, baud


def upload_pass_marker(size: int, crc32: int) -> bytes:
    """Build the exact success marker, including zero UART RX drops."""

    return "P2PY:UPLOAD:PASS:SIZE={}:CRC={:08X}:RXDROPS=0".format(
        size, crc32
    ).encode("ascii")


def validate_test_commands() -> None:
    names = set()
    markers = set()
    for test in PYTHON_TESTS:
        encoded = (test.command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
            raise PythonHilError(
                "{} Python command exceeds the console write bound".format(
                    test.name
                )
            )
        if test.marker in test.command:
            raise PythonHilError(
                "{} marker would be satisfied by console echo".format(test.name)
            )
        if test.name in names or test.marker in markers:
            raise PythonHilError("Python test names and markers must be unique")
        for command in test.setup_commands:
            encoded = (command + "\r").encode("ascii")
            if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
                raise PythonHilError(
                    "{} Python setup command exceeds the console write bound".format(
                        test.name
                    )
                )
            if test.marker in command:
                raise PythonHilError(
                    "{} marker would be satisfied by setup echo".format(test.name)
                )
        names.add(test.name)
        markers.add(test.marker)

    worker_names = set()
    assigned_tests = []
    for worker in FULL_PYTHON_TEST_WORKERS:
        if worker.name in worker_names:
            raise PythonHilError("Python worker names must be unique")
        worker_names.add(worker.name)
        assigned_tests.extend(test.name for test in worker.tests)
        for command in (worker.command,) + worker.setup_commands:
            encoded = (command + "\r").encode("ascii")
            if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
                raise PythonHilError(
                    "{} Python worker command exceeds the console write bound".format(
                        worker.name
                    )
                )
            for marker in markers:
                if marker in command:
                    raise PythonHilError(
                        "{} worker command could echo test marker {}".format(
                            worker.name, marker
                        )
                    )
    if sorted(assigned_tests) != sorted(test.name for test in PYTHON_TESTS):
        raise PythonHilError(
            "full Python workers must assign every selected test exactly once"
        )
    if [worker.name for worker in FULL_PYTHON_TEST_WORKERS] != [
        INTERACTIVE_REPL_TEST_NAME,
        CONCURRENCY_HOLDER_TEST_NAME,
        CONCURRENCY_POST_TEST_NAME,
    ]:
        raise PythonHilError(
            "full qualification must retain exactly the plain REPL, -E holder, "
            "and -I restart workers"
        )
    if FULL_PYTHON_TEST_WORKERS[0].command != INTERACTIVE_REPL_START_COMMAND:
        raise PythonHilError("the plain REPL must remain upload-triggering")
    if QUALIFICATION_BATCH_TESTS[-1].name != "final":
        raise PythonHilError("the aggregate final marker must end the batch")
    if any("'" in line for line in QUALIFICATION_BATCH_SCRIPT):
        raise PythonHilError(
            "qualification batch script cannot be safely single-quoted by NSH"
        )
    try:
        compile(
            "\n".join(QUALIFICATION_BATCH_SCRIPT) + "\n",
            QUALIFICATION_BATCH_PATH,
            "exec",
        )
    except SyntaxError as exc:
        raise PythonHilError("qualification batch script is invalid") from exc

    for path, source in (
        (IGNORE_ENV_STATE_PATH, IGNORE_ENV_STATE_SCRIPT),
        (ISOLATED_RESTART_PATH, ISOLATED_RESTART_SCRIPT),
    ):
        try:
            compile(_script_text(source), path, "exec")
        except SyntaxError as exc:
            raise PythonHilError(
                "prepared restart script {} is invalid".format(path)
            ) from exc

    for full_qualification in (False, True):
        commands = persistent_repl_setup_commands(full_qualification) + (
            persistent_repl_exec_command(),
        )
        for command in commands:
            encoded = (command + "\r").encode("ascii")
            if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
                raise PythonHilError(
                    "persistent REPL setup command exceeds the console ABI"
                )
            if INTERACTIVE_REPL_PROMPT.decode("ascii") in command:
                raise PythonHilError(
                    "persistent REPL command contains the prompt ACK token"
                )
            for marker in markers | {
                INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER,
                INTERACTIVE_REPL_SCRIPT_PASS_MARKER,
            }:
                if marker in command:
                    raise PythonHilError(
                        "persistent REPL command could echo marker {}".format(
                            marker
                        )
                    )

    for command in (
        CONCURRENCY_HOLDER_COMMAND,
        CONCURRENCY_SECOND_COMMAND,
        CONCURRENCY_POST_COMMAND,
    ):
        encoded = (command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
            raise PythonHilError(
                "concurrency command exceeds the console write bound"
            )
    for marker in (
        CONCURRENCY_HOLDER_MARKER,
        CONCURRENCY_DONE_MARKER,
        CONCURRENCY_SECOND_MARKER,
        CONCURRENCY_POST_MARKER,
    ):
        if any(
            marker in command
            for command in (
                CONCURRENCY_HOLDER_COMMAND,
                CONCURRENCY_SECOND_COMMAND,
                CONCURRENCY_POST_COMMAND,
            )
        ):
            raise PythonHilError(
                "concurrency marker would be satisfied by console echo"
            )

    for command in (
        INTERACTIVE_REPL_START_COMMAND,
        INTERACTIVE_REPL_EXPRESSION_COMMAND,
        INTERACTIVE_REPL_EXIT_COMMAND,
    ):
        encoded = (command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
            raise PythonHilError(
                "interactive REPL command exceeds the console write bound"
            )
    if INTERACTIVE_REPL_EXPRESSION_MARKER in INTERACTIVE_REPL_EXPRESSION_COMMAND:
        raise PythonHilError(
            "interactive REPL marker would be satisfied by console echo"
        )


class LiveSerialEvidence:
    """Append and checkpoint byte-exact serial evidence during a HIL run.

    The compact progress document is a commit record.  Its byte counts and
    hashes are published only after all referenced append-only files have
    reached ``fsync()``.  A reader can therefore copy exactly those prefixes
    while the runner continues to own the sole serial reader.
    """

    FILES = {
        "rx": "serial.raw",
        "tx": "serial-tx.raw",
        "telemetry": "telemetry-progress.jsonl",
    }
    MARKERS = (
        ("overlay", OVERLAY_TELEMETRY_PREFIX),
        ("worker_exit", WORKER_EXIT_PREFIX.encode("ascii")),
        ("worker_stack", WORKER_STACK_PREFIX),
    )

    def __init__(
        self,
        artifact_dir: pathlib.Path,
        sync_interval: float = LIVE_EVIDENCE_SYNC_SECONDS,
    ) -> None:
        if sync_interval <= 0:
            raise PythonHilError("live evidence sync interval must be positive")

        self.artifact_dir = artifact_dir
        self.progress_path = artifact_dir / "serial-progress.json"
        self.sync_interval = sync_interval
        self.failed = False
        self.closed = False
        self._fds = {}
        self._counts = {name: 0 for name in self.FILES}
        self._committed = {name: 0 for name in self.FILES}
        self._hashes = {name: hashlib.sha256() for name in self.FILES}
        self._dirty = {name: True for name in self.FILES}
        self._checkpoint_sequence = 0
        self._last_checkpoint = time.monotonic()
        self._line_buffer = bytearray()
        self._line_start_offset = 0
        self._line_number = 0
        self._telemetry_sequence = 0
        self._last_telemetry_record = None

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
        try:
            for name, filename in self.FILES.items():
                self._fds[name] = os.open(
                    artifact_dir / filename, flags, 0o600
                )
            self.checkpoint(force=True)
        except BaseException as exc:
            self.failed = True
            self._close_descriptors()
            if isinstance(exc, PythonHilError):
                raise
            raise PythonHilError(
                "live serial evidence initialization failed: {}".format(exc)
            ) from exc

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if not isinstance(written, int) or written <= 0:
                raise OSError("append-only evidence write made no progress")
            remaining = remaining[written:]

    def _append(self, name: str, data: bytes) -> None:
        if not data:
            return
        self._write_all(self._fds[name], data)
        self._counts[name] += len(data)
        self._hashes[name].update(data)
        self._dirty[name] = True

    def _scan_complete_lines(self, data: bytes) -> int:
        self._line_buffer.extend(data)
        emitted = 0
        while True:
            newline = self._line_buffer.find(b"\n")
            if newline < 0:
                break

            raw_line = bytes(self._line_buffer[:newline])
            del self._line_buffer[: newline + 1]
            line_offset = self._line_start_offset
            self._line_start_offset += newline + 1
            self._line_number += 1
            line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line

            matches = []
            for kind, marker in self.MARKERS:
                offset = line.find(marker)
                if offset >= 0:
                    matches.append((offset, kind))

            for offset, kind in sorted(matches):
                self._telemetry_sequence += 1
                record = {
                    "sequence": self._telemetry_sequence,
                    "kind": kind,
                    "serial_line": self._line_number,
                    "serial_byte_offset": line_offset + offset,
                    "received_utc": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "raw": line[offset:].decode("ascii", "replace"),
                }
                encoded = (
                    json.dumps(record, separators=(",", ":"), sort_keys=True)
                    + "\n"
                ).encode("utf-8")
                self._append("telemetry", encoded)
                self._last_telemetry_record = record
                emitted += 1

        return emitted

    def append_rx(self, data: bytes) -> None:
        if self.closed:
            raise PythonHilError("live serial evidence is already closed")
        try:
            self._append("rx", data)
            telemetry_records = self._scan_complete_lines(data)
            if telemetry_records or (
                time.monotonic() - self._last_checkpoint
                >= self.sync_interval
            ):
                self.checkpoint()
        except BaseException as exc:
            self.failed = True
            if isinstance(exc, PythonHilError):
                raise
            raise PythonHilError(
                "live serial RX evidence failed: {}".format(exc)
            ) from exc

    def append_tx(self, data: bytes) -> None:
        if self.closed:
            raise PythonHilError("live serial evidence is already closed")
        try:
            self._append("tx", data)
        except BaseException as exc:
            self.failed = True
            if isinstance(exc, PythonHilError):
                raise
            raise PythonHilError(
                "live serial TX evidence failed: {}".format(exc)
            ) from exc

    def checkpoint(self, force: bool = False, state: str = "running") -> None:
        if self.closed:
            raise PythonHilError("live serial evidence is already closed")
        if self.failed:
            raise PythonHilError("live serial evidence previously failed")
        if not force and not any(self._dirty.values()):
            return

        try:
            for name in self.FILES:
                if self._dirty[name] or force:
                    os.fsync(self._fds[name])

            committed = dict(self._counts)
            self._checkpoint_sequence += 1
            progress = {
                "format": LIVE_EVIDENCE_FORMAT,
                "state": state,
                "checkpoint_sequence": self._checkpoint_sequence,
                "updated_utc": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "serial_rx_file": self.FILES["rx"],
                "serial_rx_committed_bytes": committed["rx"],
                "serial_rx_sha256": self._hashes["rx"].copy().hexdigest(),
                "serial_tx_file": self.FILES["tx"],
                "serial_tx_committed_bytes": committed["tx"],
                "serial_tx_sha256": self._hashes["tx"].copy().hexdigest(),
                "telemetry_file": self.FILES["telemetry"],
                "telemetry_committed_bytes": committed["telemetry"],
                "telemetry_sha256": self._hashes[
                    "telemetry"
                ].copy().hexdigest(),
                "telemetry_record_count": self._telemetry_sequence,
                "last_telemetry_record": self._last_telemetry_record,
                "trailing_serial_line_bytes": len(self._line_buffer),
            }
            write_status_atomic(self.progress_path, progress)
            self._committed = committed
            self._dirty = {name: False for name in self.FILES}
            self._last_checkpoint = time.monotonic()
        except BaseException as exc:
            self.failed = True
            if isinstance(exc, PythonHilError):
                raise
            raise PythonHilError(
                "live serial evidence checkpoint failed: {}".format(exc)
            ) from exc

    def reconcile(self, serial_rx: bytes, serial_tx: bytes) -> None:
        """Append test-injected suffixes and prove both in-memory prefixes."""

        for name, complete in (("rx", serial_rx), ("tx", serial_tx)):
            count = self._counts[name]
            if count > len(complete):
                raise PythonHilError(
                    "live {} evidence exceeds the in-memory serial record".format(
                        name
                    )
                )
            expected = hashlib.sha256(complete[:count]).digest()
            if expected != self._hashes[name].digest():
                raise PythonHilError(
                    "live {} evidence differs from the in-memory serial record".format(
                        name
                    )
                )
            suffix = complete[count:]
            if name == "rx":
                self.append_rx(suffix)
            else:
                self.append_tx(suffix)

    def _close_descriptors(self) -> None:
        for descriptor in self._fds.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._fds.clear()
        self.closed = True

    def close(self, checkpoint: bool = True) -> None:
        if self.closed:
            return
        failure = None
        try:
            if checkpoint:
                self.checkpoint(force=True, state="closed")
        except BaseException as exc:
            failure = exc
        finally:
            self._close_descriptors()
        if failure is not None:
            raise failure


def read_live_serial_evidence(artifact_dir: pathlib.Path) -> Mapping[str, object]:
    """Read one durable live prefix without opening or inspecting serial."""

    progress_path = artifact_dir / "serial-progress.json"
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PythonHilError("invalid live serial progress: {}".format(exc)) from exc
    if progress.get("format") != LIVE_EVIDENCE_FORMAT:
        raise PythonHilError("live serial progress has an invalid format")

    result = {"progress": progress}
    fields = (
        (
            "serial_rx",
            "serial_rx_file",
            "serial_rx_committed_bytes",
            "serial_rx_sha256",
            LiveSerialEvidence.FILES["rx"],
        ),
        (
            "serial_tx",
            "serial_tx_file",
            "serial_tx_committed_bytes",
            "serial_tx_sha256",
            LiveSerialEvidence.FILES["tx"],
        ),
        (
            "telemetry",
            "telemetry_file",
            "telemetry_committed_bytes",
            "telemetry_sha256",
            LiveSerialEvidence.FILES["telemetry"],
        ),
    )
    for result_name, file_key, size_key, hash_key, required_filename in fields:
        filename = progress.get(file_key)
        size = progress.get(size_key)
        expected_hash = progress.get(hash_key)
        if (
            filename != required_filename
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(expected_hash, str)
        ):
            raise PythonHilError(
                "live serial progress has invalid {} metadata".format(
                    result_name
                )
            )
        try:
            with (artifact_dir / filename).open("rb") as stream:
                data = stream.read(size)
        except OSError as exc:
            raise PythonHilError(
                "cannot read committed {} evidence: {}".format(result_name, exc)
            ) from exc
        if len(data) != size:
            raise PythonHilError(
                "committed {} evidence is truncated".format(result_name)
            )
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise PythonHilError(
                "committed {} evidence hash does not match".format(result_name)
            )
        if result_name == "telemetry" and data and not data.endswith(b"\n"):
            raise PythonHilError(
                "committed telemetry evidence ends with a partial record"
            )
        result[result_name] = data

    return result


class SerialSession:
    def __init__(
        self,
        connection: object,
        evidence: Optional[LiveSerialEvidence] = None,
    ):
        self.connection = connection
        self.evidence = evidence
        self.pending = bytearray()
        self.received = bytearray()
        self.sent = bytearray()
        self.raw_descriptor = None

        # Keep the POSIX descriptor nonblocking and gate each write through
        # select().  A blocking os.write() has no monotonic deadline and can
        # hang forever after a USB serial disconnect or a wedged tty queue.
        # Protocol v3 already bounds every call to one 1024-byte wire chunk,
        # so readiness-gated partial writes preserve the frame byte stream.

        try:
            descriptor = self.connection.fileno()
        except (AttributeError, TypeError, ValueError):
            descriptor = None

        if descriptor is not None:
            try:
                flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
                fcntl.fcntl(descriptor, fcntl.F_SETFL,
                            flags | os.O_NONBLOCK)
                flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            except OSError as exc:
                raise PythonHilError(
                    "cannot enable deadline-safe serial writes: {}".format(exc)
                ) from exc
            if not flags & os.O_NONBLOCK:
                raise PythonHilError(
                    "serial descriptor remained blocking"
                )
            self.raw_descriptor = descriptor

    def write(
        self, data: bytes, deadline: Optional[float] = None
    ) -> None:
        if len(data) > MAX_UART_WRITE:
            raise PythonHilError("serial write exceeds the bounded UART window")
        original_write_timeout = None
        deadline_bounded = deadline is not None
        if deadline_bounded:
            if not hasattr(self.connection, "write_timeout"):
                raise PythonHilError(
                    "serial connection cannot enforce the upload deadline"
                )
            original_write_timeout = self.connection.write_timeout
        offset = 0
        try:
            while offset < len(data):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PythonHilError(
                            "container upload exceeded its deadline"
                        )
                    if original_write_timeout is None:
                        self.connection.write_timeout = remaining
                    else:
                        self.connection.write_timeout = min(
                            original_write_timeout, remaining
                        )
                try:
                    written = self.connection.write(data[offset:])
                except Exception as exc:
                    if (
                        deadline is not None
                        and time.monotonic() >= deadline
                    ):
                        raise PythonHilError(
                            "container upload exceeded its deadline"
                        ) from exc
                    raise
                if (
                    not isinstance(written, int)
                    or written <= 0
                    or written > len(data) - offset
                ):
                    raise PythonHilError("serial write made no progress")
                transmitted = data[offset : offset + written]
                self.sent.extend(transmitted)
                if self.evidence is not None:
                    self.evidence.append_tx(transmitted)
                offset += written
                if deadline is not None and time.monotonic() >= deadline:
                    raise PythonHilError(
                        "container upload exceeded its deadline"
                    )
        finally:
            if deadline_bounded:
                self.connection.write_timeout = original_write_timeout

    def write_blocking(
        self, data: bytes, deadline: Optional[float] = None
    ) -> None:
        """Write one wire chunk with a real monotonic descriptor deadline."""

        if len(data) > MAX_UART_WRITE:
            raise PythonHilError("serial write exceeds the bounded UART window")
        if deadline is not None and time.monotonic() >= deadline:
            raise PythonHilError("container upload exceeded its deadline")

        descriptor = self.raw_descriptor
        if descriptor is None:
            self.write(data, deadline=deadline)
            return

        offset = 0
        while offset < len(data):
            wait_timeout = None
            if deadline is not None:
                wait_timeout = deadline - time.monotonic()
                if wait_timeout <= 0:
                    raise PythonHilError(
                        "container upload exceeded its deadline"
                    )

            try:
                _readable, writable, _exceptional = select.select(
                    [], [descriptor], [], wait_timeout
                )
            except InterruptedError:
                continue
            if not writable:
                raise PythonHilError(
                    "container upload exceeded its deadline"
                )

            try:
                written = os.write(descriptor, data[offset:])
            except InterruptedError:
                continue
            except BlockingIOError:
                continue
            if written <= 0 or written > len(data) - offset:
                raise PythonHilError("serial write made no progress")
            transmitted = data[offset : offset + written]
            self.sent.extend(transmitted)
            if self.evidence is not None:
                self.evidence.append_tx(transmitted)
            offset += written

        if deadline is not None and time.monotonic() >= deadline:
            raise PythonHilError("container upload exceeded its deadline")

    def _receive_raw(self) -> bytes:
        waiting = getattr(self.connection, "in_waiting", 0)
        data = self.connection.read(max(1, min(int(waiting or 1), 4096)))
        if data:
            if not isinstance(data, bytes):
                raise PythonHilError("serial read returned non-bytes")
            self.received.extend(data)
            if self.evidence is not None:
                self.evidence.append_rx(data)
            self.pending.extend(data)
        return data

    def _receive(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise PythonHilError("serial receive timeout")
        data = self._receive_raw()
        if data:
            for marker in FATAL_SERIAL_PREFIXES:
                if marker in self.pending:
                    raise PythonHilError(
                        "target reported fatal serial marker {}".format(
                            marker.decode("ascii")
                        )
                    )

    def read_exact(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self.pending) < size:
            self._receive(deadline)
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result

    def wait_token(self, token: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            index = self.pending.find(token)
            if index >= 0:
                end = index + len(token)
                result = bytes(self.pending[:end])
                del self.pending[:end]
                return result
            self._receive(deadline)

    def wait_line_prefix(self, prefixes: Sequence[bytes], timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                line = bytes(self.pending[:newline]).rstrip(b"\r")
                del self.pending[: newline + 1]
                if any(line.startswith(prefix) for prefix in prefixes):
                    return line
                continue
            self._receive(deadline)


def failure_drain_terminal_marker(
    data: bytes, *, start_is_line_boundary: bool = True
) -> Optional[bytes]:
    """Return a line-oriented terminal marker found in diagnostic RX."""

    for marker in FAILURE_DRAIN_TERMINAL_MARKERS:
        offset = data.find(marker)
        while offset >= 0:
            if (
                (offset == 0 and start_is_line_boundary)
                or (offset > 0 and data[offset - 1] in b"\r\n")
            ):
                return marker
            offset = data.find(marker, offset + 1)
    return None


def drain_failure_serial(
    session: SerialSession,
    timeout: float,
    progress_callback: Optional[ProgressCallback] = None,
) -> Mapping[str, object]:
    """Record target-only RX after a test failure without touching TX.

    A shell prompt is accepted only at a line boundary, and the descriptor
    remains open for a quiet interval after that prompt.  Without that paired
    marker-and-quiet condition the drain always runs to its hard deadline, so
    a temporary gap in a slow traceback cannot truncate the evidence.
    """

    if not math.isfinite(timeout) or timeout <= 0:
        raise PythonHilError("failure drain timeout must be positive and finite")

    started = time.monotonic()
    deadline = started + timeout
    initial_rx_bytes = len(session.received)
    last_rx = started
    last_progress = started
    scan_limit = FAILURE_DRAIN_SCAN_BYTES + 1
    scan = bytearray(session.received[-scan_limit:])
    scan_start_is_line_boundary = len(session.received) <= scan_limit
    terminal = failure_drain_terminal_marker(
        bytes(scan), start_is_line_boundary=scan_start_is_line_boundary
    )
    stop_reason = "timeout"
    read_error = None

    report_progress(
        progress_callback,
        "failure_drain",
        state="draining",
        timeout_seconds=timeout,
        quiet_seconds=FAILURE_DRAIN_QUIET_SECONDS,
        received_bytes=0,
    )

    while True:
        now = time.monotonic()
        if terminal is not None and now - last_rx >= FAILURE_DRAIN_QUIET_SECONDS:
            stop_reason = "terminal_marker_quiet"
            break
        if now >= deadline:
            break

        try:
            data = session._receive_raw()
        except Exception as exc:
            stop_reason = "serial_error"
            read_error = {
                "type": type(exc).__name__,
                "reason": str(exc) or repr(exc),
            }
            break

        now = time.monotonic()
        if data:
            last_rx = now
            scan.extend(data)
            if terminal is None:
                terminal = failure_drain_terminal_marker(
                    bytes(scan),
                    start_is_line_boundary=scan_start_is_line_boundary,
                )
            if len(scan) > scan_limit:
                del scan[:-scan_limit]
                scan_start_is_line_boundary = False

        if (
            now - last_progress >= FAILURE_DRAIN_PROGRESS_SECONDS
            and now < deadline
        ):
            report_progress(
                progress_callback,
                "failure_drain",
                state="draining",
                timeout_seconds=timeout,
                quiet_seconds=FAILURE_DRAIN_QUIET_SECONDS,
                received_bytes=len(session.received) - initial_rx_bytes,
                terminal_marker=(
                    terminal.decode("ascii") if terminal is not None else None
                ),
            )
            last_progress = now

        if not data:
            remaining = deadline - now
            quiet_remaining = (
                FAILURE_DRAIN_QUIET_SECONDS - (now - last_rx)
                if terminal is not None
                else remaining
            )
            time.sleep(max(0.0, min(0.01, remaining, quiet_remaining)))

    result = {
        "state": "complete" if read_error is None else "error",
        "stop_reason": stop_reason,
        "timeout_seconds": timeout,
        "quiet_seconds": FAILURE_DRAIN_QUIET_SECONDS,
        "elapsed_seconds": time.monotonic() - started,
        "received_bytes": len(session.received) - initial_rx_bytes,
        "terminal_marker": (
            terminal.decode("ascii") if terminal is not None else None
        ),
        "write_bytes": 0,
    }
    if read_error is not None:
        result["read_error"] = read_error
    report_progress(progress_callback, "failure_drain", **result)
    return result


def validate_artifacts(
    image: pathlib.Path,
    resident_elf: pathlib.Path,
    container_path: pathlib.Path,
) -> Mapping[str, object]:
    if not image.is_file() or image.stat().st_size == 0:
        raise PythonHilError("resident NuttX image is missing or empty")
    try:
        container = p2_python_container.verify_container(container_path)
    except (OSError, p2_python_container.ContainerError) as exc:
        raise PythonHilError("invalid P2 Python container: {}".format(exc)) from exc
    if container.file_size > CONTAINER_CAPACITY:
        raise PythonHilError("container exceeds the fixed 13-MiB backing window")
    try:
        p2_python_package.verify_resident_elf(
            resident_elf, container.build_fingerprint
        )
    except (OSError, p2_python_package.PackageError) as exc:
        raise PythonHilError(
            "resident ELF does not match the P2 Python container: {}".format(exc)
        ) from exc
    validate_test_commands()
    return {
        "image": str(image),
        "image_size": image.stat().st_size,
        "image_sha256": sha256_file(image),
        "resident_elf": str(resident_elf),
        "resident_elf_size": resident_elf.stat().st_size,
        "resident_elf_sha256": sha256_file(resident_elf),
        "container": str(container_path),
        "container_size": container.file_size,
        "container_sha256": sha256_file(container_path),
        "container_crc32": "{:08X}".format(
            binascii.crc32(container_path.read_bytes()) & 0xFFFFFFFF
        ),
        "container_manifest_sha256": container.manifest_sha256.hex(),
        "container_fingerprint": container.build_fingerprint.hex(),
        "overlay_load_address": "0x{:08X}".format(container.overlay_load_address),
        "overlay_slot_size": container.overlay_slot_size,
    }


def loader_command(args: argparse.Namespace) -> Tuple[str, ...]:
    reset = "-DTR" if args.reset_method == "dtr" else "-RTS"
    command = (
        str(args.loadp2),
        "-p",
        args.serial,
        "-l",
        str(args.loader_baud),
        "-b",
        str(args.baud),
        "-ZERO",
        "-v",
        reset,
        str(args.image),
    )
    if "-FLASH" in command or "-PATCH" in command:
        raise PythonHilError("persistent loadp2 operations are forbidden")
    return command


def run_loader(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """RAM-load and start NuttX, then release the serial device completely."""

    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        error = PythonHilError("loadp2 RAM load timed out: {}".format(exc))
        error.loader_output = exc.output or b""
        raise error from exc
    except OSError as exc:
        raise PythonHilError("loadp2 RAM load failed: {}".format(exc)) from exc


def open_serial(port: str, baud: int):
    """Open a shared UART descriptor while preserving inactive reset lines.

    On macOS, the last close of a tty with HUPCL can pulse the board reset
    line.  The HIL transaction deliberately keeps one non-reading descriptor
    open across loadp2's close and the test-session open.  Both descriptors
    therefore have to be shared and must keep DTR/RTS deasserted electrically
    (the USB adapter uses the asserted boolean state as the inactive level).
    """

    try:
        import serial
    except ImportError as exc:
        raise PythonHilError("pyserial is required for Python HIL") from exc

    arguments = dict(
        port=None,
        baudrate=baud,
        timeout=0.1,
        write_timeout=10.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=False,
    )
    try:
        connection = serial.Serial(**arguments)
    except TypeError as exc:
        if "exclusive" not in str(exc):
            raise
        arguments.pop("exclusive")
        connection = serial.Serial(**arguments)

    try:
        connection.dtr = True
        connection.rts = True
        connection.port = port
        connection.open()
    except Exception:
        connection.close()
        raise
    return connection


def wait_stack_telemetry(
    session: SerialSession, test_timeout: float
) -> Mapping[str, int]:
    line = session.wait_line_prefix(
        (
            b"P2PY:WORKER:STACK:",
            b"Traceback ",
            b"ERROR:",
            b"P2PY:UPLOAD:FAIL:",
        ),
        test_timeout,
    )
    match = STACK_RE.fullmatch(line)
    if match is None:
        raise PythonHilError(
            "Python worker stack telemetry is missing or malformed: {}".format(
                line.decode("ascii", "replace")
            )
        )

    free = int(match.group(1))
    size = int(match.group(2))
    if size <= 0 or free > size:
        raise PythonHilError("Python worker stack telemetry is impossible")
    if free < STACK_MINIMUM_FREE:
        raise PythonHilError(
            "Python worker stack headroom {} is below {} bytes".format(
                free, STACK_MINIMUM_FREE
            )
        )
    return {"free": free, "size": size, "used": size - free}


def wait_worker_exit(session: SerialSession, test_timeout: float) -> int:
    line = session.wait_line_prefix(
        (
            WORKER_EXIT_PREFIX.encode("ascii"),
            b"Traceback ",
            b"ERROR:",
            b"P2PY:UPLOAD:FAIL:",
        ),
        test_timeout,
    )
    match = WORKER_EXIT_RE.fullmatch(line)
    if match is None:
        raise PythonHilError(
            "CPython worker exit status is missing or malformed: {}".format(
                line.decode("ascii", "replace")
            )
        )

    code = int(match.group(1))
    if code != 0:
        raise PythonHilError("CPython worker exited with status {}".format(code))
    return code


def hold_interactive_repl_alive(
    session: SerialSession, hold_seconds: float
) -> Mapping[str, object]:
    """Keep a smoke REPL alive and require fresh periodic telemetry."""

    if hold_seconds <= 0:
        raise PythonHilError("interactive REPL live hold must be positive")

    sample_prefix = b"P2PY:OVL:SAMPLE:"
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )

    # Do not let a SAMPLE buffered before the hold satisfy the live proof.
    # Removing lines from pending does not remove them from byte-exact raw
    # evidence, which is independently parsed again after the run.

    while True:
        newline = session.pending.find(b"\n")
        if newline < 0:
            break
        line = bytes(session.pending[:newline]).rstrip(b"\r")
        del session.pending[: newline + 1]
        if any(line.startswith(prefix) for prefix in failure_prefixes):
            raise PythonHilError(
                "interactive Python exited before its smoke live hold: {}".format(
                    line.decode("ascii", "replace")
                )
            )

    started = time.monotonic()
    deadline = started + hold_seconds
    sample = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = session.wait_line_prefix(
                (sample_prefix,) + failure_prefixes,
                min(0.5, remaining),
            )
        except PythonHilError as exc:
            if str(exc) == "serial receive timeout":
                continue
            raise
        if line.startswith(sample_prefix):
            sample = line
            continue
        raise PythonHilError(
            "interactive Python exited during its smoke live hold: {}".format(
                line.decode("ascii", "replace")
            )
        )

    elapsed = time.monotonic() - started
    if sample is None:
        raise PythonHilError(
            "interactive Python emitted no fresh overlay SAMPLE during its "
            "{:.3f}s smoke live hold".format(hold_seconds)
        )
    if elapsed < hold_seconds:
        raise PythonHilError("interactive Python smoke live hold ended early")
    return {
        "requested_seconds": hold_seconds,
        "elapsed_seconds": elapsed,
        "sample_marker": sample.decode("ascii"),
    }


def write_repl_line(session: SerialSession, command: str) -> int:
    """Send one console-safe REPL line."""

    encoded = (command + "\r").encode("ascii")
    if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
        raise PythonHilError("persistent REPL command exceeds the console ABI")
    session.write(encoded)
    return len(encoded)


def wait_python_worker_tests(
    session: SerialSession,
    worker: PythonTestWorker,
    test_timeout: float,
    progress_callback: Optional[ProgressCallback] = None,
    completed_offset: int = 0,
    total_tests: Optional[int] = None,
) -> Mapping[str, object]:
    """Require every assigned test marker in exact worker order."""

    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    completed = []
    durations = {}
    entropy_fingerprint = None
    total = total_tests if total_tests is not None else len(worker.tests)
    for test_index, test in enumerate(worker.tests):
        started = time.monotonic()
        report_progress(
            progress_callback,
            "python_test",
            state="running",
            test=test.name,
            worker=worker.name,
            index=completed_offset + test_index + 1,
            total=total,
            completed=completed_offset + test_index,
            timeout_seconds=test_timeout,
        )
        if test.name == "hardware_entropy":
            fingerprint_line = session.wait_line_prefix(
                (
                    ENTROPY_FINGERPRINT_PREFIX.encode("ascii"),
                    test.marker.encode("ascii"),
                )
                + failure_prefixes,
                test_timeout,
            )
            entropy_fingerprint = parse_entropy_fingerprint(fingerprint_line)

        result = session.wait_line_prefix(
            (test.marker.encode("ascii"),) + failure_prefixes, test_timeout
        )
        if result != test.marker.encode("ascii"):
            raise PythonHilError(
                "{} Python test failed in worker {}: {}".format(
                    test.name,
                    worker.name,
                    result.decode("ascii", "replace"),
                )
            )
        completed.append(test.name)
        durations[test.name] = time.monotonic() - started
        report_progress(
            progress_callback,
            "python_test",
            state="complete",
            test=test.name,
            worker=worker.name,
            index=completed_offset + test_index + 1,
            total=total,
            completed=completed_offset + test_index + 1,
        )
    return {
        "completed_tests": completed,
        "durations_seconds": durations,
        "entropy_fingerprint": entropy_fingerprint,
    }


def run_interactive_repl_test(
    session: SerialSession,
    test_timeout: float,
    live_hold_seconds: float = 0.0,
    worker: Optional[PythonTestWorker] = None,
    full_qualification: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> Mapping[str, object]:
    """Run tests and 6*7 inside one complete plain-REPL lifecycle."""

    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    if worker is None:
        worker = SMOKE_PYTHON_TEST_WORKERS[0]
    if worker.name != INTERACTIVE_REPL_TEST_NAME or worker.command != "python":
        raise PythonHilError("interactive worker must be the plain python REPL")

    banner = session.wait_line_prefix(
        (INTERACTIVE_REPL_BANNER_PREFIX,) + failure_prefixes,
        test_timeout,
    )
    if not banner.startswith(INTERACTIVE_REPL_BANNER_PREFIX):
        raise PythonHilError(
            "interactive Python banner was not observed: {}".format(
                banner.decode("ascii", "replace")
            )
        )
    session.wait_token(INTERACTIVE_REPL_PROMPT, test_timeout)

    setup_commands = persistent_repl_setup_commands(full_qualification)
    setup_bytes = 0
    setup_started = time.monotonic()
    for index, command in enumerate(setup_commands):
        report_progress(
            progress_callback,
            "interactive_repl_setup",
            state="running",
            command=index + 1,
            total=len(setup_commands),
        )
        setup_bytes += write_repl_line(session, command)
        session.wait_token(INTERACTIVE_REPL_PROMPT, test_timeout)
    report_progress(
        progress_callback,
        "interactive_repl_setup",
        state="complete",
        command=len(setup_commands),
        total=len(setup_commands),
        command_bytes=setup_bytes,
    )

    execution_command = persistent_repl_exec_command()
    write_repl_line(session, execution_command)
    script_begin = session.wait_line_prefix(
        (INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii"),)
        + failure_prefixes,
        test_timeout,
    )
    if script_begin != INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii"):
        raise PythonHilError("persistent REPL prepared script did not start")
    test_result = wait_python_worker_tests(
        session,
        worker,
        test_timeout,
        progress_callback=progress_callback,
        completed_offset=0,
        total_tests=len(worker.tests),
    )
    script_pass = session.wait_line_prefix(
        (INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii"),)
        + failure_prefixes,
        test_timeout,
    )
    if script_pass != INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii"):
        raise PythonHilError("persistent REPL prepared script did not pass")
    session.wait_token(INTERACTIVE_REPL_PROMPT, test_timeout)

    write_repl_line(session, INTERACTIVE_REPL_EXPRESSION_COMMAND)
    expression = session.wait_line_prefix(
        (INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii"),)
        + failure_prefixes,
        test_timeout,
    )
    if expression != INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii"):
        raise PythonHilError(
            "interactive Python expression failed: {}".format(
                expression.decode("ascii", "replace")
            )
        )
    session.wait_token(INTERACTIVE_REPL_PROMPT, test_timeout)

    live_hold = None
    if live_hold_seconds > 0:
        if live_hold_seconds > test_timeout:
            raise PythonHilError(
                "interactive REPL live hold exceeds the test timeout"
            )
        live_hold = hold_interactive_repl_alive(session, live_hold_seconds)

    write_repl_line(session, INTERACTIVE_REPL_EXIT_COMMAND)
    exit_code = wait_worker_exit(session, test_timeout)
    stack = wait_stack_telemetry(session, test_timeout)
    session.wait_token(b"nsh> ", test_timeout)
    stack_sample = {"test": INTERACTIVE_REPL_TEST_NAME, **stack}
    result = {
        "banner": banner.decode("ascii", "replace"),
        "prompt": INTERACTIVE_REPL_PROMPT.decode("ascii"),
        "expression_marker": expression.decode("ascii"),
        "script_path": QUALIFICATION_BATCH_PATH,
        "script_tests": list(test_result["completed_tests"]),
        "script_begin_marker": script_begin.decode("ascii"),
        "script_pass_marker": script_pass.decode("ascii"),
        "setup": {
            "command_count": len(setup_commands),
            "command_bytes": setup_bytes,
            "prompt_ack_count": len(setup_commands),
            "maximum_command_bytes": max(
                len((command + "\r").encode("ascii"))
                for command in setup_commands
            ),
            "elapsed_seconds": time.monotonic() - setup_started,
        },
        "execution_command": execution_command,
        "exit_command": INTERACTIVE_REPL_EXIT_COMMAND,
        "exit_code": exit_code,
        "stack_sample": stack_sample,
    }
    if live_hold is not None:
        result["live_hold"] = live_hold
    result["test_durations_seconds"] = test_result["durations_seconds"]
    result["entropy_fingerprint"] = test_result["entropy_fingerprint"]
    return result


def parse_entropy_fingerprint(line: bytes) -> str:
    match = ENTROPY_FINGERPRINT_RE.fullmatch(line)
    if match is None:
        raise PythonHilError("hardware entropy fingerprint is malformed")
    return match.group(1).decode("ascii")


def run_nsh_setup(
    session: SerialSession, command: bytes, test_timeout: float
) -> str:
    session.write(command + b"\r")
    output = session.wait_token(b"nsh> ", test_timeout)
    for failure in (b"command not found", b" failed:", b"ERROR:"):
        if failure in output:
            raise PythonHilError(
                "NSH setup command failed: {}: {}".format(
                    command.decode("ascii"), output.decode("ascii", "replace")
                )
            )
    return output.decode("ascii", "replace")


def runtime_stage_deadline_error(
    timeout: float, expected: bytes, completed: int
) -> PythonHilError:
    return PythonHilError(
        "CPython runtime-stage overall deadline of {:.3f}s expired before {} "
        "after {}/{} stages".format(
            timeout,
            expected.decode("ascii"),
            completed,
            len(RUNTIME_STAGES),
        )
    )


def wait_runtime_stages(
    session: SerialSession,
    timeout: float,
    progress_callback: Optional[ProgressCallback] = None,
) -> Tuple[Sequence[str], float]:
    """Wait for all CPython startup markers under one overall deadline."""

    started = time.monotonic()
    deadline = started + timeout
    completed = []
    for index, expected in enumerate(RUNTIME_STAGES):
        remaining = deadline - time.monotonic()
        report_progress(
            progress_callback,
            "runtime_stages",
            state="waiting",
            next_stage=expected.decode("ascii"),
            completed=index,
            total=len(RUNTIME_STAGES),
            overall_deadline_seconds=timeout,
            remaining_seconds=max(0.0, remaining),
        )
        if remaining <= 0:
            raise runtime_stage_deadline_error(timeout, expected, index)
        try:
            stage = session.wait_line_prefix(
                (expected, b"Traceback ", b"ERROR:", b"P2PY:UPLOAD:FAIL:"),
                remaining,
            )
        except PythonHilError as exc:
            if str(exc) != "serial receive timeout":
                raise
            raise runtime_stage_deadline_error(
                timeout, expected, index
            ) from exc
        if stage != expected:
            raise PythonHilError(
                "CPython runtime stage failed before {}: {}".format(
                    expected.decode("ascii"), stage.decode("ascii", "replace")
                )
            )
        completed.append(stage.decode("ascii"))
        report_progress(
            progress_callback,
            "runtime_stages",
            state="stage_complete",
            stage=stage.decode("ascii"),
            completed=index + 1,
            total=len(RUNTIME_STAGES),
            overall_deadline_seconds=timeout,
        )

    elapsed = time.monotonic() - started
    report_progress(
        progress_callback,
        "runtime_stages",
        state="complete",
        completed=len(RUNTIME_STAGES),
        total=len(RUNTIME_STAGES),
        overall_deadline_seconds=timeout,
        elapsed_seconds=elapsed,
    )
    return completed, elapsed


def run_python_tests(
    session: SerialSession,
    container_path: pathlib.Path,
    boot_timeout: float,
    upload_timeout: float,
    test_timeout: float,
    inject_upload_faults: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    plan: PythonQualificationPlan = FULL_QUALIFICATION_PLAN,
) -> Mapping[str, object]:
    report_progress(
        progress_callback,
        "boot_prompt",
        state="waiting",
        timeout_seconds=boot_timeout,
    )
    session.write(b"\r")
    session.wait_token(b"nsh> ", boot_timeout)
    report_progress(progress_callback, "boot_prompt", state="ready")

    # The P2 CPython launcher owns /tmp setup.  Pre-mounting it here would
    # hide a board artifact that only works after manual shell preparation.
    shell_setup = []

    first_worker = plan.python_workers[0]
    if (
        first_worker.name != INTERACTIVE_REPL_TEST_NAME
        or first_worker.command != INTERACTIVE_REPL_START_COMMAND
        or first_worker.setup_commands
    ):
        raise PythonHilError(
            "the upload-triggering worker must be the setup-free plain REPL"
        )
    report_progress(
        progress_callback,
        "upload_ready",
        state="waiting",
        timeout_seconds=test_timeout,
    )
    session.write((first_worker.command + "\r").encode("ascii"))
    ready = session.wait_line_prefix(
        (
            b"P2PY:UPLOAD:READY:",
            b"P2PY:UPLOAD:FAIL:",
            b"nsh: python:",
            b"ERROR:",
        ),
        test_timeout,
    )
    if ready.startswith(b"nsh: python:"):
        raise PythonHilError("Python builtin is unavailable: {}".format(
            ready.decode("ascii", "replace")
        ))
    if ready.startswith(b"ERROR:"):
        raise PythonHilError(ready.decode("ascii", "replace"))
    if ready.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(ready.decode("ascii", "replace"))
    parse_ready(ready)
    report_progress(progress_callback, "upload_ready", state="ready")

    size = container_path.stat().st_size
    crc32 = 0
    with container_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            crc32 = binascii.crc32(block, crc32)
    crc32 &= 0xFFFFFFFF
    session.write(upload_preamble(size, crc32))
    report_progress(
        progress_callback,
        "upload_accept",
        state="waiting",
        size_bytes=size,
        crc32="{:08X}".format(crc32),
    )
    accept = session.wait_line_prefix(
        (b"P2PY:UPLOAD:ACCEPT:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if accept.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(accept.decode("ascii", "replace"))
    expected_accept = "P2PY:UPLOAD:ACCEPT:SIZE={}:CRC={:08X}".format(
        size, crc32
    ).encode("ascii")
    if accept != expected_accept:
        raise PythonHilError("target upload ACCEPT marker does not match artifact")
    report_progress(progress_callback, "upload_accept", state="accepted")

    report_progress(
        progress_callback,
        "upload_frames",
        state="starting",
        acked_bytes=0,
        total_bytes=size,
        timeout_seconds=upload_timeout,
    )
    upload_transport = send_upload_frames(
        session,
        container_path,
        size,
        upload_timeout,
        test_timeout,
        inject_faults=inject_upload_faults,
        progress_callback=progress_callback,
    )

    upload = session.wait_line_prefix(
        (b"P2PY:UPLOAD:PASS:", b"P2PY:UPLOAD:FAIL:"), test_timeout * 3
    )
    if upload.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(upload.decode("ascii", "replace"))
    expected_upload = upload_pass_marker(size, crc32)
    if upload != expected_upload:
        raise PythonHilError("target upload PASS marker does not match artifact")
    report_progress(
        progress_callback,
        "upload_frames",
        state="complete",
        acked_bytes=size,
        total_bytes=size,
    )
    report_progress(
        progress_callback,
        "runtime_ready",
        state="waiting",
        timeout_seconds=test_timeout,
    )
    runtime = session.wait_line_prefix(
        (b"P2PY:RUNTIME:READY:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if not runtime.startswith(b"P2PY:RUNTIME:READY:"):
        raise PythonHilError(runtime.decode("ascii", "replace"))
    report_progress(progress_callback, "runtime_ready", state="ready")

    runtime_stages, runtime_stage_seconds = wait_runtime_stages(
        session, test_timeout, progress_callback
    )

    completed = []
    durations = {}
    worker_durations = {}
    stack_samples = []
    entropy_fingerprint = None

    repl_started = time.monotonic()
    report_progress(progress_callback, "interactive_repl", state="running")
    interactive_repl = run_interactive_repl_test(
        session,
        test_timeout,
        live_hold_seconds=plan.repl_live_hold_seconds,
        worker=first_worker,
        full_qualification=plan.full_qualification,
        progress_callback=progress_callback,
    )
    completed.extend(interactive_repl["script_tests"])
    completed.append(INTERACTIVE_REPL_TEST_NAME)
    stack_samples.append(interactive_repl["stack_sample"])
    durations.update(interactive_repl["test_durations_seconds"])
    entropy_fingerprint = interactive_repl["entropy_fingerprint"]
    worker_durations[INTERACTIVE_REPL_TEST_NAME] = (
        time.monotonic() - repl_started
    )
    report_progress(progress_callback, "interactive_repl", state="complete")

    if plan.include_concurrency:
        if len(plan.python_workers) != 3:
            raise PythonHilError(
                "full qualification must contain exactly three successful starts"
            )
        report_progress(progress_callback, "concurrency_guard", state="running")
        concurrency = run_concurrency_test(
            session,
            test_timeout,
            holder_worker=plan.python_workers[1],
            post_worker=plan.python_workers[2],
            progress_callback=progress_callback,
            completed_offset=len(completed) - 1,
            total_tests=len(plan.python_tests),
        )
        completed.extend(concurrency["completed_tests"])
        completed.append("concurrency_guard")
        stack_samples.extend(concurrency["stack_samples"])
        durations.update(concurrency["test_durations_seconds"])
        worker_durations.update(concurrency["worker_durations_seconds"])
        report_progress(
            progress_callback, "concurrency_guard", state="complete"
        )
    else:
        concurrency = {"skipped": True, "reason": SMOKE_SKIP_REASON}

    if plan.include_restart_stress:
        report_progress(
            progress_callback,
            "restart_stress",
            state="running",
            iterations=RESTART_STRESS_COUNT,
        )
        restart = run_restart_stress(session, test_timeout)
        completed.append("restart_stress_20")
        stack_samples.extend(restart["stack_samples"])
        report_progress(
            progress_callback,
            "restart_stress",
            state="complete",
            iterations=RESTART_STRESS_COUNT,
        )
    else:
        restart = {
            "skipped": True,
            "reason": (
                SMOKE_SKIP_REASON
                if plan.level == "smoke"
                else FULL_RESTART_SKIP_REASON
            ),
        }

    return {
        "completed_tests": completed,
        "upload_size": size,
        "upload_crc32": "{:08X}".format(crc32),
        "ready_marker": ready.decode("ascii"),
        "upload_marker": upload.decode("ascii"),
        "runtime_marker": runtime.decode("ascii"),
        "runtime_stages": runtime_stages,
        "runtime_stage_deadline_seconds": test_timeout,
        "runtime_stage_seconds": runtime_stage_seconds,
        "shell_setup": shell_setup,
        "upload_transport": upload_transport,
        "interactive_repl": interactive_repl,
        "concurrency": concurrency,
        "restart_stress": restart,
        "stack_samples": stack_samples,
        "minimum_stack_free": min(sample["free"] for sample in stack_samples),
        "entropy_fingerprint": entropy_fingerprint,
        "test_durations_seconds": durations,
        "worker_durations_seconds": worker_durations,
    }


def run_restart_stress(
    session: SerialSession, test_timeout: float
) -> Mapping[str, object]:
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    durations = []
    stack_samples = []
    for iteration in range(RESTART_STRESS_COUNT):
        marker = "P2PYTEST:RESTART:{}:PASS".format(iteration)
        command = (
            "python -c 'import tracemalloc,zlib;tracemalloc.start();"
            "x=bytearray(1024);assert tracemalloc.get_traced_memory()[0]>0;"
            "s=b\"x\"*32769;assert zlib.decompress(zlib.compress(s))==s;"
            "tracemalloc.stop();"
            "print(\"P2PYTEST:RESTART:\"+str({})+\":PASS\")'".format(iteration)
        )
        encoded = (command + "\r").encode("ascii")
        if (
            len(encoded) > LINE_MAX
            or len(encoded) > MAX_UART_WRITE
            or marker in command
        ):
            raise PythonHilError("restart stress command violates the console ABI")
        started = time.monotonic()
        session.write(encoded)
        result = session.wait_line_prefix(
            (marker.encode("ascii"),) + failure_prefixes, test_timeout
        )
        if result != marker.encode("ascii"):
            raise PythonHilError(
                "restart stress iteration {} failed: {}".format(
                    iteration, result.decode("ascii", "replace")
                )
            )
        wait_worker_exit(session, test_timeout)
        stack = wait_stack_telemetry(session, test_timeout)
        stack["test"] = "restart_stress_{}".format(iteration)
        stack_samples.append(stack)
        session.wait_token(b"nsh> ", test_timeout)
        durations.append(time.monotonic() - started)

    return {
        "count": RESTART_STRESS_COUNT,
        "durations_seconds": durations,
        "maximum_seconds": max(durations),
        "stack_samples": stack_samples,
    }


def run_concurrency_test(
    session: SerialSession,
    test_timeout: float,
    holder_worker: PythonTestWorker = FULL_PYTHON_TEST_WORKERS[1],
    post_worker: PythonTestWorker = FULL_PYTHON_TEST_WORKERS[2],
    progress_callback: Optional[ProgressCallback] = None,
    completed_offset: int = 0,
    total_tests: Optional[int] = None,
) -> Mapping[str, object]:
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    holder_marker = CONCURRENCY_HOLDER_MARKER.encode("ascii")
    done_marker = CONCURRENCY_DONE_MARKER.encode("ascii")
    second_marker = CONCURRENCY_SECOND_MARKER.encode("ascii")
    post_marker = CONCURRENCY_POST_MARKER.encode("ascii")
    busy_prefix = CONCURRENCY_BUSY_PREFIX.encode("ascii")

    if (
        holder_worker.name != CONCURRENCY_HOLDER_TEST_NAME
        or holder_worker.command != CONCURRENCY_HOLDER_COMMAND
        or post_worker.name != CONCURRENCY_POST_TEST_NAME
        or post_worker.command != CONCURRENCY_POST_COMMAND
    ):
        raise PythonHilError("concurrency workers do not match the -E/-I plan")

    holder_started = time.monotonic()
    session.write((holder_worker.command + "\r").encode("ascii"))
    session.wait_token(b"nsh> ", test_timeout)
    holder_tests = wait_python_worker_tests(
        session,
        holder_worker,
        test_timeout,
        progress_callback=progress_callback,
        completed_offset=completed_offset,
        total_tests=total_tests,
    )
    holder = session.wait_line_prefix((holder_marker,) + failure_prefixes, test_timeout)
    if holder != holder_marker:
        raise PythonHilError("background Python holder failed to start")

    session.write((CONCURRENCY_SECOND_COMMAND + "\r").encode("ascii"))
    busy = session.wait_line_prefix(
        (busy_prefix, second_marker) + failure_prefixes, test_timeout
    )
    if busy == second_marker:
        raise PythonHilError("concurrent Python launch was incorrectly admitted")
    if not busy.startswith(busy_prefix):
        raise PythonHilError("concurrent Python launch did not fail with EBUSY")
    try:
        code = int(busy[len(busy_prefix) :])
    except ValueError as exc:
        raise PythonHilError("concurrent Python busy code is malformed") from exc
    if code <= 0:
        raise PythonHilError("concurrent Python busy code is invalid")

    session.wait_token(b"nsh> ", test_timeout)
    done = session.wait_line_prefix((done_marker,) + failure_prefixes, test_timeout)
    if done != done_marker:
        raise PythonHilError("background Python holder did not finish cleanly")

    wait_worker_exit(session, test_timeout)
    holder_stack = wait_stack_telemetry(session, test_timeout)
    holder_seconds = time.monotonic() - holder_started

    post_started = time.monotonic()
    session.write((post_worker.command + "\r").encode("ascii"))
    post_tests = wait_python_worker_tests(
        session,
        post_worker,
        test_timeout,
        progress_callback=progress_callback,
        completed_offset=completed_offset + len(holder_worker.tests),
        total_tests=total_tests,
    )
    post = session.wait_line_prefix((post_marker,) + failure_prefixes, test_timeout)
    if post != post_marker:
        raise PythonHilError("Python did not restart after concurrent contention")
    wait_worker_exit(session, test_timeout)
    post_stack = wait_stack_telemetry(session, test_timeout)
    session.wait_token(b"nsh> ", test_timeout)
    post_seconds = time.monotonic() - post_started

    return {
        "holder_marker": holder.decode("ascii"),
        "busy_marker": busy.decode("ascii"),
        "done_marker": done.decode("ascii"),
        "post_marker": post.decode("ascii"),
        "completed_tests": (
            holder_tests["completed_tests"] + post_tests["completed_tests"]
        ),
        "test_durations_seconds": {
            **holder_tests["durations_seconds"],
            **post_tests["durations_seconds"],
        },
        "worker_durations_seconds": {
            holder_worker.name: holder_seconds,
            post_worker.name: post_seconds,
        },
        "stack_samples": [
            {"test": CONCURRENCY_HOLDER_TEST_NAME, **holder_stack},
            {"test": CONCURRENCY_POST_TEST_NAME, **post_stack},
        ],
    }


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--qualification-level",
        choices=("full", "smoke", "overnight"),
        default="full",
        help=(
            "full runs the three-start qualification; smoke proves arithmetic "
            "inside one real REPL but cannot produce a full PASS artifact; "
            "overnight adds 20 explicit restart-stress lifecycles"
        ),
    )
    parser.add_argument("--serial", required=True)
    parser.add_argument("--baud", type=int, default=RUNTIME_BAUD)
    parser.add_argument("--loader-baud", type=int, default=2000000)
    parser.add_argument("--loadp2", type=pathlib.Path)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--resident-elf", required=True, type=pathlib.Path)
    parser.add_argument("--container", required=True, type=pathlib.Path)
    parser.add_argument("--artifact-dir", required=True, type=pathlib.Path)
    parser.add_argument("--reset-method", choices=("dtr", "rts"), default="dtr")
    parser.add_argument("--load-timeout", type=float, default=60.0)
    parser.add_argument("--boot-timeout", type=float, default=90.0)
    parser.add_argument("--upload-timeout", type=float, default=1800.0)
    parser.add_argument("--test-timeout", type=float, default=120.0)
    parser.add_argument(
        "--failure-drain-timeout",
        type=float,
        default=0.0,
        help=(
            "after a Python test failure, keep both shared serial descriptors "
            "open and record target RX for up to this many seconds; 0 disables"
        ),
    )
    parser.add_argument(
        "--skip-upload-fault-injection",
        action="store_true",
        help=(
            "diagnostic smoke mode: upload only valid frames; this does not "
            "qualify retransmission recovery"
        ),
    )
    parser.add_argument("--lock-file", type=pathlib.Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    for name in (
        "baud",
        "loader_baud",
        "load_timeout",
        "boot_timeout",
        "upload_timeout",
        "test_timeout",
    ):
        if getattr(args, name) <= 0:
            parser.error("--{} must be positive".format(name.replace("_", "-")))
    if (
        not math.isfinite(args.failure_drain_timeout)
        or args.failure_drain_timeout < 0
    ):
        parser.error("--failure-drain-timeout must be finite and non-negative")
    if args.baud != RUNTIME_BAUD:
        parser.error("--baud must be {} for the P2 runtime UART ABI".format(
            RUNTIME_BAUD
        ))
    args.image = args.image.expanduser().resolve()
    args.resident_elf = args.resident_elf.expanduser().resolve()
    args.container = args.container.expanduser().resolve()
    args.artifact_dir = args.artifact_dir.expanduser().resolve()
    args.lock_file = args.lock_file.expanduser().resolve()
    if args.loadp2 is not None:
        args.loadp2 = args.loadp2.expanduser().resolve()
    return args


def execute(
    args: argparse.Namespace,
    inputs: Mapping[str, object],
    plan: PythonQualificationPlan = FULL_QUALIFICATION_PLAN,
) -> int:
    if args.artifact_dir.exists():
        raise PythonHilError("artifact directory already exists")
    args.artifact_dir.mkdir(parents=True)

    execute_started = time.monotonic()
    inject_upload_faults = not getattr(
        args, "skip_upload_fault_injection", False
    )
    failure_drain_timeout = getattr(args, "failure_drain_timeout", 0.0)
    status = {
        "format": plan.artifact_format,
        "status": "RUNNING",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inputs": dict(inputs),
        "serial": args.serial,
        "baud": args.baud,
        "qualification_level": plan.level,
        "full_qualification": plan.full_qualification,
        "qualification_scope": qualification_scope(plan),
        "upload_fault_injection": {
            "enabled": inject_upload_faults,
            "kinds": list(UPLOAD_FAULT_SEQUENCE) if inject_upload_faults else [],
        },
        "failure_drain": {
            "enabled": failure_drain_timeout > 0,
            "state": "armed" if failure_drain_timeout > 0 else "disabled",
            "timeout_seconds": failure_drain_timeout,
            "quiet_seconds": FAILURE_DRAIN_QUIET_SECONDS,
            "terminal_markers": [
                marker.decode("ascii")
                for marker in FAILURE_DRAIN_TERMINAL_MARKERS
            ],
        },
        "expected_successful_workers": len(plan.expected_worker_names),
        "expected_successful_worker_names": list(plan.expected_worker_names),
        "interactive_repl_contract": {
            "start_command": INTERACTIVE_REPL_START_COMMAND,
            "setup_command_count": len(
                persistent_repl_setup_commands(plan.full_qualification)
            ),
            "expected_prompt_count": persistent_repl_prompt_count(
                plan.full_qualification
            ),
            "execution_command": persistent_repl_exec_command(),
            "expression_command": INTERACTIVE_REPL_EXPRESSION_COMMAND,
            "expression_marker": INTERACTIVE_REPL_EXPRESSION_MARKER,
            "exit_command": INTERACTIVE_REPL_EXIT_COMMAND,
        },
        "tests": [dataclasses.asdict(test) for test in plan.python_tests],
        "omitted_tests": list(plan.omitted_test_names),
        "progress_history": [],
    }
    status_path = args.artifact_dir / "status.json"
    loader_output = b""
    session: Optional[SerialSession] = None
    evidence: Optional[LiveSerialEvidence] = None
    evidence_failure: Optional[PythonHilError] = None
    telemetry_failure: Optional[PythonHilError] = None
    progress_sequence = 0

    def persist_progress(
        phase: str, details: Mapping[str, object]
    ) -> None:
        nonlocal progress_sequence

        progress_sequence += 1
        now = datetime.datetime.now(datetime.timezone.utc)
        event = {
            "sequence": progress_sequence,
            "phase": phase,
            "updated_utc": now.isoformat(),
            "run_elapsed_seconds": time.monotonic() - execute_started,
            **details,
        }
        remaining = event.get("remaining_seconds")
        if phase == "runtime_stages" and isinstance(remaining, (int, float)):
            event["overall_deadline_utc"] = (
                now + datetime.timedelta(seconds=max(0.0, remaining))
            ).isoformat()
        status["current_phase"] = phase
        status["progress"] = event
        status["progress_history"].append(event)
        if (
            evidence is not None
            and not evidence.failed
            and not evidence.closed
        ):
            evidence.checkpoint()
        write_status_atomic(status_path, status)

    try:
        evidence = LiveSerialEvidence(args.artifact_dir)
        persist_progress("environment", {"state": "validating"})
        for variable in ("P2_HIL", "P2_ALLOW_RESET", "P2_ALLOW_PSRAM_WRITE"):
            if os.environ.get(variable) != "1":
                raise PythonHilError("{}=1 is required with --execute".format(variable))
        if (
            args.loadp2 is None
            or not args.loadp2.is_file()
            or not os.access(args.loadp2, os.X_OK)
        ):
            raise PythonHilError("--loadp2 must name the executable pinned loader")
        try:
            mode = os.stat(args.serial).st_mode
        except OSError as exc:
            raise PythonHilError(
                "serial device is unavailable: {}".format(exc)
            ) from exc
        if not stat.S_ISCHR(mode):
            raise PythonHilError("--serial must name a character device")

        persist_progress("board_lock", {"state": "waiting"})
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PythonHilError("P2 Python HIL lock is busy") from exc
            persist_progress("board_lock", {"state": "acquired"})

            command = loader_command(args)
            status["loader_command"] = list(command)
            persist_progress(
                "loader",
                {"state": "running", "timeout_seconds": args.load_timeout},
            )
            guard = open_serial(args.serial, args.baud)
            status["serial_handoff_guard"] = "nonreading-shared-descriptor"
            connection = None
            try:
                try:
                    loaded = run_loader(command, args.load_timeout)
                except BaseException as exc:
                    loader_output = getattr(exc, "loader_output", b"")
                    raise
                loader_output = loaded.stdout
                status["loader_exit_code"] = loaded.returncode
                if loaded.returncode != 0:
                    raise PythonHilError(
                        "loadp2 exited with status {}".format(loaded.returncode)
                    )
                persist_progress(
                    "loader",
                    {"state": "complete", "exit_code": loaded.returncode},
                )

                connection = open_serial(args.serial, args.baud)
                try:
                    session = SerialSession(connection, evidence)
                    try:
                        result = run_python_tests(
                            session,
                            args.container,
                            args.boot_timeout,
                            args.upload_timeout,
                            args.test_timeout,
                            inject_upload_faults,
                            progress_callback=persist_progress,
                            plan=plan,
                        )
                    except Exception as test_failure:
                        if failure_drain_timeout > 0:
                            status["failure_drain"].update(
                                {
                                    "state": "draining",
                                    "original_failure_type": type(
                                        test_failure
                                    ).__name__,
                                    "original_failure_reason": str(test_failure)
                                    or repr(test_failure),
                                }
                            )
                            try:
                                drain = drain_failure_serial(
                                    session,
                                    failure_drain_timeout,
                                    progress_callback=persist_progress,
                                )
                            except Exception as drain_failure:
                                drain = {
                                    "state": "error",
                                    "stop_reason": "drain_error",
                                    "timeout_seconds": failure_drain_timeout,
                                    "quiet_seconds": FAILURE_DRAIN_QUIET_SECONDS,
                                    "received_bytes": 0,
                                    "write_bytes": 0,
                                    "drain_error": {
                                        "type": type(drain_failure).__name__,
                                        "reason": str(drain_failure)
                                        or repr(drain_failure),
                                    },
                                }
                            status["failure_drain"].update(drain)
                        raise
                    persist_progress("tests", {"state": "complete"})
                finally:
                    connection.close()
            finally:
                guard.close()

        status.update(result)
        status["status"] = plan.success_status
    except BaseException as exc:
        status["status"] = "FAIL"
        status["failure_type"] = type(exc).__name__
        status["reason"] = str(exc) or repr(exc)
        persist_progress(
            "failed",
            {
                "state": "failed",
                "failure_type": type(exc).__name__,
                "reason": str(exc) or repr(exc),
            },
        )
        raise
    finally:
        serial_rx = bytes(session.received) if session is not None else b""
        serial_tx = bytes(session.sent) if session is not None else b""
        if evidence is not None:
            try:
                if evidence.failed:
                    evidence.close(checkpoint=False)
                else:
                    evidence.reconcile(serial_rx, serial_tx)
                    evidence.close()
                    live_snapshot = read_live_serial_evidence(args.artifact_dir)
                    if live_snapshot["serial_rx"] != serial_rx:
                        raise PythonHilError(
                            "committed live RX evidence differs from final bytes"
                        )
                    if live_snapshot["serial_tx"] != serial_tx:
                        raise PythonHilError(
                            "committed live TX evidence differs from final bytes"
                        )
            except BaseException as exc:
                if isinstance(exc, PythonHilError):
                    failure = exc
                else:
                    failure = PythonHilError(
                        "live serial evidence finalization failed: {}".format(exc)
                    )
                if status.get("status") == "FAIL":
                    status["live_evidence_failure"] = str(failure)
                else:
                    evidence_failure = failure
                    status["status"] = "FAIL"
                    status["failure_type"] = type(failure).__name__
                    status["reason"] = str(failure)
        (args.artifact_dir / "loader.log").write_bytes(loader_output)
        status["serial_rx_bytes"] = len(serial_rx)
        status["serial_tx_bytes"] = len(serial_tx)
        try:
            successful = status["status"] in ("PASS", "SMOKE_PASS")
            overlay_telemetry = parse_overlay_telemetry(
                serial_rx,
                status if successful else None,
                plan,
            )
        except Exception as exc:
            overlay_telemetry = {
                "records": [],
                "malformed": [],
                "validation_errors": [],
                "qualification_errors": [],
                "result_validation_errors": [],
                "worker_exits": [],
                "worker_stacks": [],
                "worker_malformed": [],
                "lifecycles": [],
                "analysis": {
                    "valid": False,
                    "record_valid": False,
                    "serial_qualification_valid": False,
                    "qualification_valid": False,
                    "parser_error": "{}: {}".format(type(exc).__name__, exc),
                },
            }
        status["overlay_telemetry"] = overlay_telemetry
        telemetry_analysis = overlay_telemetry["analysis"]
        successful = status["status"] in ("PASS", "SMOKE_PASS")
        if successful and not telemetry_analysis.get(
            "qualification_valid", False
        ):
            reason = (
                "overlay telemetry validation failed: {} malformed, "
                "{} record errors, {} qualification errors, "
                "{} result cross-check errors"
            ).format(
                telemetry_analysis.get("malformed_count", 0),
                telemetry_analysis.get("validation_error_count", 0),
                telemetry_analysis.get("qualification_error_count", 0),
                telemetry_analysis.get("result_validation_error_count", 0),
            )
            telemetry_failure = PythonHilError(reason)
            status["status"] = "FAIL"
            status["failure_type"] = type(telemetry_failure).__name__
            status["reason"] = reason
        if status["status"] in ("PASS", "SMOKE_PASS"):
            progress_details: Dict[str, object] = {"state": "complete"}
            if not plan.full_qualification:
                progress_details["level"] = plan.level
            persist_progress("passed", progress_details)
        elif status.get("current_phase") != "failed":
            persist_progress(
                "failed",
                {
                    "state": "failed",
                    "failure_type": status.get("failure_type", "unknown"),
                    "reason": status.get("reason", "unknown failure"),
                },
            )
        status["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_status_atomic(status_path, status)

    if evidence_failure is not None:
        raise evidence_failure
    if telemetry_failure is not None:
        raise telemetry_failure

    if plan.full_qualification:
        print("P2PYHIL:PASS:ARTIFACT={}".format(args.artifact_dir))
    else:
        print(
            "P2PYHIL:SMOKE:PASS:NOT_FULL:ARTIFACT={}".format(
                args.artifact_dir
            )
        )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        qualification = qualification_plan(args.qualification_level)
        inputs = validate_artifacts(
            args.image, args.resident_elf, args.container
        )
        inject_upload_faults = not args.skip_upload_fault_injection
        hil_plan = {
            "format": "p2-python-hil-plan-v1",
            "mode": "execute" if args.execute else "dry-run",
            "serial": args.serial,
            "baud": args.baud,
            "qualification_level": qualification.level,
            "full_qualification": qualification.full_qualification,
            "qualification_scope": qualification_scope(qualification),
            "upload_fault_injection": {
                "enabled_on_execute": inject_upload_faults,
                "kinds": (
                    list(UPLOAD_FAULT_SEQUENCE) if inject_upload_faults else []
                ),
            },
            "expected_successful_workers": len(
                qualification.expected_worker_names
            ),
            "expected_successful_worker_names": list(
                qualification.expected_worker_names
            ),
            "interactive_repl_contract": {
                "start_command": INTERACTIVE_REPL_START_COMMAND,
                "setup_command_count": len(
                    persistent_repl_setup_commands(
                        qualification.full_qualification
                    )
                ),
                "expected_prompt_count": persistent_repl_prompt_count(
                    qualification.full_qualification
                ),
                "execution_command": persistent_repl_exec_command(),
                "expression_command": INTERACTIVE_REPL_EXPRESSION_COMMAND,
                "expression_marker": INTERACTIVE_REPL_EXPRESSION_MARKER,
                "exit_command": INTERACTIVE_REPL_EXIT_COMMAND,
            },
            "artifact_dir": str(args.artifact_dir),
            "loader_command": (
                list(loader_command(args)) if args.loadp2 is not None else None
            ),
            "reset_method": args.reset_method,
            "timeouts_seconds": {
                "load": args.load_timeout,
                "boot": args.boot_timeout,
                "upload": args.upload_timeout,
                "test": args.test_timeout,
                "failure_drain": args.failure_drain_timeout,
            },
            "inputs": inputs,
            "tests": [test.name for test in qualification.python_tests],
            "omitted_tests": list(qualification.omitted_test_names),
        }
        if not args.execute:
            print(json.dumps(hil_plan, indent=2, sort_keys=True))
            print("DRY-RUN: no serial open, reset, RAM load, or PSRAM write occurred")
            return 0
        return execute(args, inputs, qualification)
    except PythonHilError as exc:
        print("P2PYHIL:FAIL:{}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("P2PYHIL:FAIL:interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            "P2PYHIL:FAIL:{}:{}".format(type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
