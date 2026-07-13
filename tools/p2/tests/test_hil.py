import datetime
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import hil
import psram_protocol
import storage_protocol


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.epoch = datetime.datetime(2026, 7, 12, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def advance(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value)


class FakeSession:
    def __init__(self, clock, events, returncode=None):
        self.clock = clock
        self.events = list(events)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.closed = False
        self.writes = []

    def read(self, timeout):
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, bytes):
                self.clock.advance(0.001)
                return event
            if event == "eof":
                if self.returncode is None:
                    self.returncode = 0
                return None
            if isinstance(event, (int, float)):
                self.clock.advance(event)
                return b""
            if isinstance(event, BaseException):
                raise event
        self.clock.advance(timeout)
        return b""

    def poll(self):
        return self.returncode

    def write(self, data):
        self.writes.append(bytes(data))

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-loadp2", 0.2)
        return self.returncode

    def close(self):
        self.closed = True


class SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.commands = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        if not self.sessions:
            raise AssertionError("no fake loadp2 session remains")
        return self.sessions.pop(0)


class RecordingLock:
    def __init__(self):
        self.constructed = 0
        self.entered = 0
        self.exited = 0

    def factory(self, path, timeout=0.0, monotonic=None):
        del path, timeout, monotonic
        self.constructed += 1
        recorder = self

        class Context:
            def __enter__(self):
                recorder.entered += 1
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                del exc_type, exc_value, traceback
                recorder.exited += 1

        return Context()


GOOD_OUTPUT = (
    b"loader output\nP2HELLO:ENTRY\r\n"
    b"P2HELLO:DATA=OK\r\nP2HELLO:BSS=OK\r\n"
    b"P2HELLO:PTRA=0x00000100\r\n"
    b"P2HELLO:COUNTER=0x1234ABCD\r\n"
    b"P2HELLO:READY\r\nP2HELLO:ECHO=?\r\n"
)

GOOD_CONTEXT_OUTPUT = (
    b"loader output\nP2CTX:START\r\n"
    b"P2CTX:SWITCHES=1000000\r\nP2CTX:REGS=OK\r\n"
    b"P2CTX:STACKS=OK\r\nP2CTX:PASS\r\n"
)

GOOD_SMARTPINS_OUTPUT = (
    b"loader output\n"
    b"P2SMART:BEGIN\r\n"
    b"P2SMART:WIRING=P0-P1,P2-P3,P4-P5,P6-P7\r\n"
    b"P2SMART:CAPS=GPIO,EDGE,UART,PWM_CAPTURE,SPI\r\n"
    b"P2SMART:GPIO:BEGIN=0-1\r\n"
    b"P2SMART:GPIO:SAMPLE=0:TX=0:RX=0\r\n"
    b"P2SMART:GPIO:SAMPLE=1:TX=1:RX=1\r\n"
    b"P2SMART:GPIO:SAMPLE=2:TX=1:RX=1\r\n"
    b"P2SMART:GPIO:SAMPLE=3:TX=0:RX=0\r\n"
    b"P2SMART:GPIO:SAMPLE=4:TX=1:RX=1\r\n"
    b"P2SMART:GPIO:SAMPLE=5:TX=0:RX=0\r\n"
    b"P2SMART:GPIO:SAMPLE=6:TX=0:RX=0\r\n"
    b"P2SMART:GPIO:SAMPLE=7:TX=1:RX=1\r\n"
    b"P2SMART:GPIO:SAFE=FLOAT\r\n"
    b"P2SMART:GPIO:PASS\r\n"
    b"P2SMART:EDGE:BEGIN=0-1\r\n"
    b"P2SMART:EDGE:COUNT=6\r\n"
    b"P2SMART:EDGE:SAFE=FLOAT\r\n"
    b"P2SMART:EDGE:PASS\r\n"
    b"P2SMART:UART:BEGIN=2-3\r\n"
    b"P2SMART:UART:COUNT=16:FNV1A=504B8F7B\r\n"
    b"P2SMART:UART:SAFE=FLOAT\r\n"
    b"P2SMART:UART:PASS\r\n"
    b"P2SMART:PWM_CAPTURE:BEGIN=4-5\r\n"
    b"P2SMART:PWM_CAPTURE:SAMPLE=0:FREQ=998:DUTY=25:EDGES=49\r\n"
    b"P2SMART:PWM_CAPTURE:SAMPLE=1:FREQ=1002:DUTY=50:EDGES=50\r\n"
    b"P2SMART:PWM_CAPTURE:SAMPLE=2:FREQ=1000:DUTY=75:EDGES=51\r\n"
    b"P2SMART:PWM_CAPTURE:SAFE=FLOAT\r\n"
    b"P2SMART:PWM_CAPTURE:PASS\r\n"
    b"P2SMART:SPI:BEGIN=MOSI=6:MISO=7:SCK=8:CS=9:"
    b"MODE=0:REQUEST_HZ=100000\r\n"
    b"P2SMART:SPI:COUNT=16:TX=504B8F7B:RX=504B8F7B\r\n"
    b"P2SMART:SPI:SAFE=MOSI6,MISO7,SCK8,CS9=FLOAT\r\n"
    b"P2SMART:SPI:PASS\r\n"
    b"P2SMART:PASS\r\n"
)

GOOD_BOOT_OUTPUT = (
    b"loader output\nP2BOOT:ENTRY\r\n"
    b"P2BOOT:DATA=OK\r\nP2BOOT:BSS=OK\r\nP2BOOT:NX_START\r\n"
)


def good_psram_response(sequence="A55A0713"):
    lines = [
        "p2psram {}".format(sequence),
        "P2PSRAM:BEGIN:SEQUENCE={}".format(sequence),
        "P2PSRAM:GEOMETRY:SIZE=33554432:CHIPS=4:CHIP_SIZE=8388608:"
        "WORD=4:MAX_REQUEST=65536:COG=2",
        "P2PSRAM:PROFILE:MAX_REQUEST=65536:QPI_HZ=5000000:"
        "TICK_USEC=10000:TIMEOUT_TICKS=500:CANCEL_GRACE_TICKS=100",
        "P2PSRAM:WALKING:PASS:BITS=32",
        "P2PSRAM:ADDRESS:PASS:LINES=23",
        "P2PSRAM:BOUNDARY:PASS:COUNT=5",
        "P2PSRAM:RANDOM:PASS:COUNT=1024",
    ]
    for value in range(4 * 1024 * 1024, 32 * 1024 * 1024 + 1, 4 * 1024 * 1024):
        lines.append(
            "P2PSRAM:PROGRESS:SEQUENCE={}:WRITE={}".format(sequence, value)
        )
    for value in range(4 * 1024 * 1024, 32 * 1024 * 1024 + 1, 4 * 1024 * 1024):
        lines.append(
            "P2PSRAM:PROGRESS:SEQUENCE={}:READ={}".format(sequence, value)
        )
    lines.extend(
        (
            "P2PSRAM:FULL:PASS:BYTES=33554432:FNV1A=634C9DC5",
            "P2PSRAM:THROUGHPUT:WRITE_BPS=900000:READ_BPS=1100000",
            "P2PSRAM:CONCURRENT:PASS:WORK=32768:ELAPSED_TICKS=4:"
            "CPU_AVAILABLE_PERMILLE=930:CPU_OCCUPANCY_PERMILLE=70",
            "P2PSRAM:TIMEOUT:PASS:RESULT=110:BYTES=32768:"
            "DEADLINE_TICKS=1:MIN_WIRE_USEC=24576:TICK_USEC=10000",
            "P2PSRAM:RECOVERY:PASS",
            "P2PSRAM:CE_TIMING:PASS:MAX_CYCLES=711:LIMIT_CYCLES=1440",
            "P2PSRAM:PASS:SEQUENCE={}".format(sequence),
        )
    )
    return ("\r\n".join(lines) + "\r\n").encode("ascii")

GOOD_BRINGUP_OUTPUT = GOOD_BOOT_OUTPUT + b"".join(
    (marker.label + "\r\n").encode("ascii") for marker in hil.BRINGUP_APP_MARKERS
)

GOOD_NSH_BEFORE_SLEEP = (
    b"help\r\n"
    b"help usage:  help [-v] [<cmd>]\r\n"
    b"  echo  free  help  ls  mount  ps  sleep  uname  uptime\r\n"
    b"nsh> echo P2NSH:HELP=OK\r\nP2NSH:HELP=OK\r\nnsh> "
    b"uname -a\r\nNuttX 12.10.0 dev p2 p2-ec32mb\r\n"
    b"nsh> echo P2NSH:UNAME=OK\r\nP2NSH:UNAME=OK\r\nnsh> "
    b"ps\r\n  TID   PID  PPID PRI POLICY   TYPE    NPX STATE\r\n"
    b"    0     0     0   0 FIFO     Kthread --- Ready\r\n"
    b"nsh> echo P2NSH:PS=OK\r\nP2NSH:PS=OK\r\nnsh> "
    b"free\r\n      total       used       free    maxused    maxfree"
    b"  nused  nfree name\r\n"
    b"     400000      10000     390000      10000     390000"
    b"      2      1 Umem\r\n"
    b"nsh> echo P2NSH:FREE=OK\r\nP2NSH:FREE=OK\r\nnsh> "
    b"uptime\r\n00:00:07 up 0 days, 0:00, load average: 0.00, 0.00, 0.00\r\n"
    b"nsh> echo P2NSH:UPTIME=OK\r\nP2NSH:UPTIME=OK\r\nnsh> "
    b"sleep 1\r\n"
).replace(b"nsh> ", b"nsh> \x1b[K")

GOOD_NSH_AFTER_SLEEP = (
    b"nsh> echo P2NSH:SLEEP=OK\r\nP2NSH:SLEEP=OK\r\nnsh> "
    b"ls /dev\r\n/dev:\r\n console\r\n null\r\n ttyS0\r\n"
    b"nsh> echo P2NSH:LSDEV=OK\r\nP2NSH:LSDEV=OK\r\nnsh> "
    b"mount\r\n  /proc type procfs\r\n"
    b"nsh> echo P2NSH:MOUNT=OK\r\nP2NSH:MOUNT=OK\r\nnsh> "
    b"echo P2_NSH_OK\r\nP2_NSH_OK\r\nnsh> "
).replace(b"nsh> ", b"nsh> \x1b[K")

GOOD_NSH_RESPONSE = GOOD_NSH_BEFORE_SLEEP + GOOD_NSH_AFTER_SLEEP


def full_ostest_config(assertions=False, priority_inheritance=True):
    values = {
        "CONFIG_BUILD_FLAT": "y",
        "CONFIG_CANCELLATION_POINTS": "y",
        "CONFIG_DEV_NULL": "y",
        "CONFIG_ENABLE_ALL_SIGNALS": "y",
        "CONFIG_DEBUG_FULLOPT": "y",
        "CONFIG_FILE_STREAM": "y",
        "CONFIG_FS_NAMED_SEMAPHORES": "y",
        "CONFIG_HRTIMER": "y",
        "CONFIG_INIT_ENTRYPOINT": '"ostest_main"',
        "CONFIG_P2_BOOT_TRACE": "y",
        "CONFIG_PTHREAD_MUTEX_BOTH": "y",
        "CONFIG_PTHREAD_MUTEX_TYPES": "y",
        "CONFIG_RR_INTERVAL": "200",
        "CONFIG_SCHED_EVENTS": "y",
        "CONFIG_SCHED_SPORADIC": "y",
        "CONFIG_SCHED_WAITPID": "y",
        "CONFIG_SCHED_WORKQUEUE": "y",
        "CONFIG_SIG_EVTHREAD": "y",
        "CONFIG_SIG_SIGKILL_ACTION": "y",
        "CONFIG_SIG_SIGSTOP_ACTION": "y",
        "CONFIG_TESTING_OSTEST": "y",
        "CONFIG_TESTING_OSTEST_LOOPS": "1",
        "CONFIG_TESTING_OSTEST_WAITRESULT": "y",
        "CONFIG_TLS_NCLEANUP": "4",
        "CONFIG_TLS_NELEM": "4",
    }
    if priority_inheritance:
        values["CONFIG_PRIORITY_INHERITANCE"] = "y"
        values["CONFIG_PTHREAD_MUTEX_DEFAULT_PRIO_INHERIT"] = "y"
    else:
        values["CONFIG_PTHREAD_MUTEX_DEFAULT_PRIO_NONE"] = "y"
    if assertions:
        values["CONFIG_DEBUG_ASSERTIONS"] = "y"
    return values


def ostest_output(markers):
    return "\r\n".join(marker.label for marker in markers) + "\r\n"


class HilTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temp.name)
        self.loadp2 = self.directory / "loadp2"
        self.loadp2.write_bytes(b"fake pinned loader\n")
        self.loadp2.chmod(0o755)
        self.image = self.directory / "hello.elf"
        self.image.write_bytes(b"\x7fELF" + b"image" * 20)
        self.lock = self.directory / "toolchain.lock"
        load_sha = hashlib.sha256(self.loadp2.read_bytes()).hexdigest()
        self.lock.write_text(
            "sha256={}  {}\n".format(load_sha, self.loadp2), encoding="utf-8"
        )
        self.clock = ManualClock()

    def tearDown(self):
        self.temp.cleanup()

    def env(self):
        return {
            "P2_HIL": "1",
            "P2_PORT": "/dev/fake-p2",
            "P2_RESET_METHOD": "loadp2",
            "P2_LOADER_BAUD": "2000000",
            "P2_CONSOLE_BAUD": "230400",
            "P2_LOCK_FILE": str(self.directory / "board.lock"),
            "P2_TOOLCHAIN_LOCK": str(self.lock),
            "LOADP2": str(self.loadp2),
        }

    def argv(self, name="run"):
        return [
            "--execute",
            "--image",
            str(self.image),
            "--artifact-dir",
            str(self.directory / name),
            "--timeout",
            "0.3",
        ]

    def invoke(self, argv, environment, factory, lock):
        return hil.main(
            argv,
            env=environment,
            process_factory=factory,
            monotonic=self.clock.monotonic,
            utc_now=self.clock.utc_now,
            lock_factory=lock.factory,
            owner_probe=lambda port: (),
            port_validator=lambda port: port == "/dev/fake-p2",
        )

    def write_smartpins_config(self, *, dac_adc=False):
        values = [
            "CONFIG_TESTING_P2SMARTPINS=y",
            "CONFIG_TESTING_P2SMARTPINS_EDGE=y",
            "CONFIG_TESTING_P2SMARTPINS_UART=y",
            "CONFIG_TESTING_P2SMARTPINS_PWM_CAPTURE=y",
            "CONFIG_P2_EC32MB_GPIO=y",
            "CONFIG_P2_EC32MB_GPIO_OUT_PIN=0",
            "CONFIG_P2_EC32MB_GPIO_IN_PIN=1",
            "CONFIG_P2_EC32MB_UART1=y",
            "CONFIG_P2_EC32MB_UART1_TX_PIN=2",
            "CONFIG_P2_EC32MB_UART1_RX_PIN=3",
            "CONFIG_P2_EC32MB_UART1_BAUD=115200",
            "CONFIG_P2_EC32MB_PWM=y",
            "CONFIG_P2_EC32MB_PWM_PIN=4",
            "CONFIG_P2_EC32MB_CAPTURE=y",
            "CONFIG_P2_EC32MB_CAPTURE_PIN=5",
            "CONFIG_SPI_BITBANG=y",
            "CONFIG_SPI_DRIVER=y",
            "CONFIG_SPI_EXCHANGE=y",
            "CONFIG_P2_EC32MB_SPI=y",
            "CONFIG_P2_EC32MB_SPI_MOSI_PIN=6",
            "CONFIG_P2_EC32MB_SPI_MISO_PIN=7",
            "CONFIG_P2_EC32MB_SPI_SCK_PIN=8",
            "CONFIG_P2_EC32MB_SPI_CS_PIN=9",
            "CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY=100000",
        ]
        if dac_adc:
            values.append("CONFIG_TESTING_P2SMARTPINS_DAC_ADC=y")
        else:
            values.append("# CONFIG_TESTING_P2SMARTPINS_DAC_ADC is not set")
        values.append("CONFIG_TESTING_P2SMARTPINS_SPI=y")
        (self.directory / ".config").write_text(
            "\n".join(values) + "\n", encoding="utf-8"
        )

    def write_storage_config(self):
        values = [
            "{}={}".format(name, value)
            for name, value in hil.STORAGE_REQUIRED_CONFIG
        ]
        values.extend(
            "{}={}".format(name, value)
            for name, value in hil.STORAGE_ACTION_REQUIRED_CONFIG
        )
        (self.directory / ".config").write_text(
            "\n".join(values) + "\n", encoding="utf-8"
        )

    def write_psram_config(self):
        values = [
            "{}={}".format(name, value)
            for name, value in hil.PSRAM_REQUIRED_CONFIG
        ]
        (self.directory / ".config").write_text(
            "\n".join(values) + "\n", encoding="utf-8"
        )

    def storage_boot_output(self):
        return GOOD_BOOT_OUTPUT + (
            b"P2STORAGE:W25=PRIVATE JEDEC=EF7018\r\n"
            b"P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000\r\n"
            b"P2STORAGE:W25_GEOMETRY BLOCK=256 ERASE=4096 "
            b"ERASEBLOCKS=4096 BYTES=16777216\r\n"
            b"P2STORAGE:W25_LAYOUT BOOT=0x00000000+0x00080000 "
            b"DATA=0x00080000+0x00F80000 FIRSTBLOCK=2048 "
            b"NBLOCKS=63488\r\n"
            b"P2STORAGE:W25_BOOT_CRC32=89ABCDEF\r\n"
            b"P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO\r\n"
            b"P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000\r\n"
            b"P2STORAGE:MMCSD=/dev/mmcsd0\r\n"
            b"nsh> "
        )

    def test_execute_and_hil_environment_are_both_required_before_lock_or_process(self):
        factory = SessionFactory([])
        lock = RecordingLock()

        rc_no_execute = self.invoke([], self.env(), factory, lock)
        disabled = self.env()
        disabled["P2_HIL"] = "0"
        rc_no_hil = self.invoke(["--execute"], disabled, factory, lock)

        self.assertEqual(rc_no_execute, hil.EXIT_SAFETY)
        self.assertEqual(rc_no_hil, hil.EXIT_SAFETY)
        self.assertEqual(factory.commands, [])
        self.assertEqual(lock.entered, 0)

    def test_nsh_wrapper_locks_phase9_cycles_timeout_build_and_protocol(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-nsh.py"
        spec = importlib.util.spec_from_file_location("p2_test_nsh", wrapper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=17) as main:
            rc = wrapper.main(["--cycles", "1", "--timeout", "1"])

        self.assertEqual(rc, 17)
        arguments = main.call_args.args[0]
        self.assertEqual(
            arguments[-7:],
            [
                "--protocol",
                "nsh",
                "--cycles",
                "50",
                "--timeout",
                "30",
                "--build-standalone",
            ],
        )

    def test_bringup_wrapper_locks_phase8_cycles_timeout_build_and_protocol(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-bringup.py"
        spec = importlib.util.spec_from_file_location("p2_test_bringup", wrapper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=19) as main:
            rc = wrapper.main(["--cycles", "1", "--timeout", "1"])

        self.assertEqual(rc, 19)
        arguments = main.call_args.args[0]
        self.assertEqual(
            arguments[-7:],
            [
                "--protocol",
                "bringup",
                "--cycles",
                "100",
                "--timeout",
                "10",
                "--build-standalone",
            ],
        )

    def test_ostest_wrapper_locks_five_production_cycles_and_build(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-ostest.py"
        spec = importlib.util.spec_from_file_location("p2_test_ostest", wrapper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=23) as main:
            rc = wrapper.main(["--cycles", "1", "--timeout", "1"])

        self.assertEqual(rc, 23)
        arguments = main.call_args.args[0]
        self.assertEqual(
            arguments[-11:],
            [
                "--protocol",
                "ostest",
                "--cycles",
                "5",
                "--timeout",
                "3600",
                "--ostest-assertions",
                "disabled",
                "--ostest-profile",
                "ostest-pi-production",
                "--build-standalone",
            ],
        )

    def test_smartpins_wrapper_locks_direct_loopback_run_and_build(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-smartpins.py"
        spec = importlib.util.spec_from_file_location(
            "p2_test_smartpins", wrapper_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=37) as main:
            rc = wrapper.main(
                ["--execute", "--cycles", "1", "--timeout", "1"]
            )

        self.assertEqual(rc, 37)
        self.assertEqual(
            main.call_args.args[0],
            [
                "--execute",
                "--protocol",
                "smartpins",
                "--cycles",
                "50",
                "--timeout",
                "15",
                "--build-standalone",
            ],
        )

    def test_psram_wrapper_locks_destructive_full_coverage_run_and_build(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-psram.py"
        spec = importlib.util.spec_from_file_location("p2_test_psram", wrapper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=41) as main:
            rc = wrapper.main(
                ["--execute", "--sequence", "A55A0713", "--timeout", "1"]
            )

        self.assertEqual(rc, 41)
        self.assertEqual(
            main.call_args.args[0],
            [
                "--execute",
                "--protocol",
                "psram",
                "--cycles",
                "1",
                "--timeout",
                "1800",
                "--psram-sequence",
                "A55A0713",
                "--build-standalone",
            ],
        )

    def test_ostest_wrapper_supports_one_prebuilt_assertion_run(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-ostest.py"
        spec = importlib.util.spec_from_file_location(
            "p2_test_ostest_assert", wrapper_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=29) as main:
            rc = wrapper.main(["--assertion-run", "--cycles", "9"])

        self.assertEqual(rc, 29)
        arguments = main.call_args.args[0]
        self.assertEqual(
            arguments[-11:],
            [
                "--protocol",
                "ostest",
                "--cycles",
                "1",
                "--timeout",
                "3600",
                "--ostest-assertions",
                "enabled",
                "--ostest-profile",
                "ostest-pi-assert",
                "--build-standalone",
            ],
        )
        self.assertIn("--build-standalone", arguments[-11:])

    def test_ostest_assertion_wrapper_cannot_be_weakened(self):
        wrapper_path = pathlib.Path(__file__).parents[1] / "test-ostest.py"
        spec = importlib.util.spec_from_file_location(
            "p2_test_ostest_assert_build", wrapper_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=31) as main:
            rc = wrapper.main(
                [
                    "--assertion-run",
                    "--profile",
                    "cond",
                    "--cycles",
                    "9",
                    "--ostest-assertions",
                    "disabled",
                    "--ostest-profile",
                    "ostest-pi-production",
                ]
            )

        self.assertEqual(rc, 31)
        self.assertEqual(
            main.call_args.args[0][-11:],
            [
                "--protocol",
                "ostest",
                "--cycles",
                "1",
                "--timeout",
                "3600",
                "--ostest-assertions",
                "enabled",
                "--ostest-profile",
                "ostest-cond-assert",
                "--build-standalone",
            ],
        )

    def test_ostest_build_runner_selects_exact_profile(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            hil.subprocess, "run", return_value=completed
        ) as run:
            rc = hil.default_build_runner("ostest-cond-production")

        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            [
                str(hil.REPO_ROOT / "tools" / "p2" / "build.sh"),
                "ostest-cond-production",
            ],
            cwd=str(hil.REPO_ROOT),
            check=False,
        )

    def test_smartpins_build_runner_selects_board_profile(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            hil.subprocess, "run", return_value=completed
        ) as run:
            rc = hil.default_build_runner("smartpins")

        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            [
                str(hil.REPO_ROOT / "tools" / "p2" / "build.sh"),
                "smartpins",
            ],
            cwd=str(hil.REPO_ROOT),
            check=False,
        )

    def test_psram_build_runner_selects_board_profile(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            hil.subprocess, "run", return_value=completed
        ) as run:
            rc = hil.default_build_runner("psram")

        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            [
                str(hil.REPO_ROOT / "tools" / "p2" / "build.sh"),
                "psram",
            ],
            cwd=str(hil.REPO_ROOT),
            check=False,
        )

    def test_exact_ram_only_command_and_single_lock_span_repeated_cycles(self):
        sessions = [
            FakeSession(self.clock, [GOOD_OUTPUT]),
            FakeSession(self.clock, [GOOD_OUTPUT]),
        ]
        factory = SessionFactory(sessions)
        lock = RecordingLock()
        argv = self.argv("two-cycles") + ["--cycles", "2"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual((lock.constructed, lock.entered, lock.exited), (1, 1, 1))
        self.assertEqual(len(factory.commands), 2)
        expected = (
            str(self.loadp2.resolve()),
            "-p",
            "/dev/fake-p2",
            "-l",
            "2000000",
            "-b",
            "230400",
            "-FIFO",
            "16384",
            "-ZERO",
            "-v",
            "-DTR",
            "-e",
            "pausems(500)send(?)",
            "-t",
            str(self.image.resolve()),
        )
        self.assertEqual(factory.commands, [expected, expected])
        self.assertNotIn("-PATCH", expected)
        self.assertNotIn("-FLASH", expected)
        self.assertTrue(all(session.terminated for session in sessions))
        overall = json.loads(
            (self.directory / "two-cycles" / "status.json").read_text()
        )
        self.assertEqual(overall["status"], "PASS")
        self.assertEqual(overall["cycles_passed"], 2)

    def test_context_protocol_requires_exact_markers_without_uart_script(self):
        session = FakeSession(self.clock, [GOOD_CONTEXT_OUTPUT])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("context") + ["--protocol", "context"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        command = factory.commands[0]
        self.assertNotIn("-e", command)
        self.assertNotIn("send(?)", command)
        self.assertEqual(command[-2:], ("-t", str(self.image.resolve())))
        markers = json.loads(
            (self.directory / "context" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertEqual(markers["reset_count"], 1)

    def test_smartpins_protocol_uses_exact_config_and_full_data_validation(self):
        self.write_smartpins_config()
        environment = self.env()
        environment["P2_ALLOW_LOOPBACK_TESTS"] = "1"
        session = FakeSession(self.clock, [GOOD_SMARTPINS_OUTPUT])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("smartpins") + ["--protocol", "smartpins"]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertNotIn("-e", factory.commands[0])
        metadata = json.loads(
            (self.directory / "smartpins" / "metadata.json").read_text()
        )
        self.assertEqual(
            metadata["smartpins_stages"],
            ["GPIO", "EDGE", "UART", "PWM_CAPTURE", "SPI"],
        )
        self.assertIn("DISABLED", metadata["dac_adc_status"])
        self.assertIn("ENABLED", metadata["spi_status"])
        markers = json.loads(
            (
                self.directory / "smartpins" / "cycle-001" / "markers.json"
            ).read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertTrue(markers["smartpins_protocol"]["complete"])

    def test_smartpins_full_validator_rejects_out_of_tolerance_pwm(self):
        self.write_smartpins_config()
        environment = self.env()
        environment["P2_ALLOW_LOOPBACK_TESTS"] = "1"
        output = GOOD_SMARTPINS_OUTPUT.replace(b"FREQ=998", b"FREQ=800")
        factory = SessionFactory([FakeSession(self.clock, [output])])
        lock = RecordingLock()
        argv = self.argv("smartpins-invalid-pwm") + [
            "--protocol",
            "smartpins",
        ]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (
                self.directory
                / "smartpins-invalid-pwm"
                / "cycle-001"
                / "status.json"
            ).read_text()
        )
        self.assertIn("outside 950..1050", status["reason"])

    def test_smartpins_requires_loopback_gate_and_forbids_direct_dac_adc(self):
        self.write_smartpins_config()
        factory = SessionFactory([])
        lock = RecordingLock()
        argv = self.argv("smartpins-no-gate") + ["--protocol", "smartpins"]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)
        self.write_smartpins_config(dac_adc=True)
        environment = self.env()
        environment["P2_ALLOW_LOOPBACK_TESTS"] = "1"
        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)

    def test_smartpins_config_rejects_pin_remaps_and_accepts_exact_spi(self):
        self.write_smartpins_config()
        values = hil.read_kconfig(self.directory / ".config")
        values["CONFIG_P2_EC32MB_UART1_RX_PIN"] = "7"
        with self.assertRaisesRegex(
            hil.SafetyError, "does not match installed direct jumpers"
        ):
            hil.validate_smartpins_config(values)

        values["CONFIG_P2_EC32MB_UART1_RX_PIN"] = "3"
        self.assertIn("SPI", hil.validate_smartpins_config(values))

        values["CONFIG_P2_EC32MB_SPI_MISO_PIN"] = "5"
        with self.assertRaisesRegex(
            hil.SafetyError, "does not match installed direct jumpers"
        ):
            hil.validate_smartpins_config(values)

    def test_storage_actions_enforce_erase_and_sd_gates_before_serial(self):
        factory = SessionFactory([])
        lock = RecordingLock()
        environment = self.env()
        environment["P2_ALLOW_FLASH_WRITE"] = "1"
        argv = self.argv("storage-no-erase") + [
            "--protocol",
            "storage",
            "--storage-action",
            "flash-write",
            "--storage-sequence",
            "1234ABCD",
        ]

        rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(factory.commands, [])
        self.assertEqual(lock.entered, 0)

        environment["P2_ALLOW_FLASH_ERASE"] = "1"
        argv = self.argv("storage-no-sd-gate") + [
            "--protocol",
            "storage",
            "--storage-action",
            "sd-write",
            "--storage-sequence",
            "1234ABCD",
        ]
        rc = self.invoke(argv, environment, factory, lock)
        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(factory.commands, [])
        self.assertEqual(lock.entered, 0)

    def test_storage_action_config_pins_paths_sizes_counts_and_interrupt_window(self):
        values = dict(hil.STORAGE_ACTION_REQUIRED_CONFIG)
        hil.validate_storage_action_config(values)

        for name, bad in (
            ("CONFIG_TESTING_P2STORAGE_FLASH_DEVPATH", '"/dev/mtdsmart0"'),
            ("CONFIG_TESTING_P2STORAGE_RECORD_SIZE", "512"),
            ("CONFIG_TESTING_P2STORAGE_BUS_ALTERNATE_COUNT", "16"),
            ("CONFIG_TESTING_P2STORAGE_INTERRUPT_HOLD_MSEC", "1000"),
        ):
            with self.subTest(name=name):
                drifted = dict(values)
                drifted[name] = bad
                with self.assertRaisesRegex(hil.SafetyError, name):
                    hil.validate_storage_action_config(drifted)

    def test_storage_action_sends_exact_nonce_command_after_prompt(self):
        self.write_storage_config()
        sequence = "1234ABCD"
        checksum = storage_protocol.stream_checksum("flash", sequence)
        response = (
            "p2storage flash-write {} {}\r\n"
            "P2STORAGE:BEGIN:COMMAND=flash-write\r\n"
            "P2STORAGE:FLASH:WRITE:SEQUENCE={}:BYTES=1048576:"
            "FNV1A={}:PASS\r\n"
            "P2STORAGE:READY:RESET=FLASH:SEQUENCE={}\r\n"
            "P2STORAGE:PASS:FLASH-WRITE\r\n"
        ).format(
            storage_protocol.ACKNOWLEDGEMENT,
            sequence,
            sequence,
            checksum,
            sequence,
        ).encode("ascii")
        session = FakeSession(
            self.clock, [self.storage_boot_output(), response]
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        environment = self.env()
        environment.update(
            {
                "P2_ALLOW_FLASH_WRITE": "1",
                "P2_ALLOW_FLASH_ERASE": "1",
            }
        )
        argv = self.argv("storage-flash-write") + [
            "--protocol",
            "storage",
            "--storage-action",
            "flash-write",
            "--storage-sequence",
            sequence,
        ]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual(
            session.writes,
            [storage_protocol.command_bytes("flash-write", sequence)],
        )
        marker_status = json.loads(
            (
                self.directory
                / "storage-flash-write"
                / "cycle-001"
                / "markers.json"
            ).read_text()
        )
        self.assertTrue(marker_status["complete"])
        self.assertTrue(marker_status["storage_protocol"]["complete"])
        self.assertEqual(
            marker_status["storage_protocol"]["expected_checksum"], checksum
        )

    def test_psram_sends_nonce_after_prompt_and_validates_full_32mib_run(self):
        self.write_psram_config()
        sequence = "A55A0713"
        session = FakeSession(
            self.clock,
            [GOOD_BOOT_OUTPUT + b"nsh> ", good_psram_response(sequence)],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        argv = self.argv("psram") + [
            "--protocol",
            "psram",
            "--psram-sequence",
            sequence,
        ]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual(session.writes, [psram_protocol.command_bytes(sequence)])
        markers = json.loads(
            (
                self.directory / "psram" / "cycle-001" / "markers.json"
            ).read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertTrue(markers["psram_protocol"]["complete"])
        self.assertEqual(
            markers["psram_protocol"]["values"]["full_bytes"],
            32 * 1024 * 1024,
        )
        metadata = json.loads(
            (self.directory / "psram" / "metadata.json").read_text()
        )
        self.assertEqual(metadata["external_bytes"], 32 * 1024 * 1024)
        self.assertEqual(metadata["psram_expected_fnv1a"], "634C9DC5")
        self.assertTrue(metadata["destructive"])
        self.assertFalse(metadata["native_memory"])
        copied_config = self.directory / "psram" / "inputs" / ".config"
        self.assertEqual(
            metadata["preserved_input_sha256"]["inputs/.config"],
            hil.sha256_file(copied_config),
        )

    def test_psram_config_drift_after_preservation_refuses_before_load(self):
        self.write_psram_config()
        sequence = "A55A0713"
        factory = SessionFactory([])
        lock = RecordingLock()
        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        argv = self.argv("psram-config-drift") + [
            "--protocol",
            "psram",
            "--psram-sequence",
            sequence,
        ]

        def drift_config(_port):
            config = self.directory / ".config"
            config.write_text(
                config.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )
            return ()

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = hil.main(
                argv,
                env=environment,
                process_factory=factory,
                monotonic=self.clock.monotonic,
                utc_now=self.clock.utc_now,
                lock_factory=lock.factory,
                owner_probe=drift_config,
                port_validator=lambda port: port == "/dev/fake-p2",
            )

        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(factory.commands, [])
        self.assertEqual(lock.entered, 1)
        status = json.loads(
            (self.directory / "psram-config-drift" / "status.json").read_text()
        )
        self.assertEqual(status["status"], "FAIL")
        self.assertIn("changed during the run", status["failure_reason"])

    def test_psram_gate_config_nonce_and_single_cycle_are_mandatory(self):
        self.write_psram_config()
        factory = SessionFactory([])
        lock = RecordingLock()
        base = self.argv("psram-safety") + ["--protocol", "psram"]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(
                base + ["--psram-sequence", "A55A0713"],
                self.env(),
                factory,
                lock,
            )
        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)

        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(base, environment, factory, lock)
        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(
                base + ["--psram-sequence", "a55a0713"],
                environment,
                factory,
                lock,
            )
        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(
                base
                + [
                    "--psram-sequence",
                    "A55A0713",
                    "--cycles",
                    "2",
                ],
                environment,
                factory,
                lock,
            )
        self.assertEqual(rc, hil.EXIT_SAFETY)
        self.assertEqual(lock.entered, 0)

    def test_psram_preserves_board_service_app_profile_and_pin_sources(self):
        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        args = hil.build_parser().parse_args(
            self.argv("psram-preserve")
            + [
                "--protocol",
                "psram",
                "--psram-sequence",
                "A55A0713",
            ]
        )
        values = dict(hil.PSRAM_REQUIRED_CONFIG)

        with mock.patch.object(hil, "read_kconfig", return_value=values):
            config = hil.config_from_args(
                args,
                environment,
                self.clock.utc_now,
                lambda port: port == "/dev/fake-p2",
            )

        config.artifact_dir.mkdir()
        preserved = set(hil.preserve_hil_inputs(config))
        for expected in (
            "inputs/p2psram_main.c",
            "inputs/p2_ec32mb_psram.h",
            "inputs/p2_ec32mb_psram.c",
            "inputs/p2_ec32mb_psram_logic.h",
            "inputs/p2_ec32mb_psram_service.S",
            "inputs/p2_ec32mb_psram_wire.h",
            "inputs/p2_ec32mb_pins.c",
            "inputs/p2_ec32mb_pins.h",
            "inputs/p2_ec32mb_boot.c",
            "inputs/defconfig",
            "inputs/p2-ec32mb-Kconfig",
            "inputs/src-Makefile",
        ):
            self.assertIn(expected, preserved)

    def test_psram_full_validator_rejects_missing_progress(self):
        self.write_psram_config()
        sequence = "A55A0713"
        response = good_psram_response(sequence).replace(
            b"P2PSRAM:PROGRESS:SEQUENCE=A55A0713:READ=33554432\r\n", b""
        )
        session = FakeSession(
            self.clock,
            [GOOD_BOOT_OUTPUT + b"nsh> ", response],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        argv = self.argv("psram-missing-progress") + [
            "--protocol",
            "psram",
            "--psram-sequence",
            sequence,
        ]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (
                self.directory
                / "psram-missing-progress"
                / "cycle-001"
                / "status.json"
            ).read_text()
        )
        self.assertIn("read progress", status["reason"])

    def test_psram_does_not_reuse_stale_progress_received_before_command(self):
        self.write_psram_config()
        sequence = "A55A0713"
        response = good_psram_response(sequence)
        stale_lines = []
        for direction in ("WRITE", "READ"):
            for value in range(
                4 * 1024 * 1024,
                32 * 1024 * 1024 + 1,
                4 * 1024 * 1024,
            ):
                line = (
                    "P2PSRAM:PROGRESS:SEQUENCE={}:{}={}\r\n".format(
                        sequence, direction, value
                    ).encode("ascii")
                )
                stale_lines.append(line)
                response = response.replace(line, b"")

        session = FakeSession(
            self.clock,
            [GOOD_BOOT_OUTPUT + b"".join(stale_lines) + b"nsh> ", response],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        environment = self.env()
        environment["P2_ALLOW_PSRAM_WRITE"] = "1"
        argv = self.argv("psram-stale-progress") + [
            "--protocol",
            "psram",
            "--psram-sequence",
            sequence,
        ]

        with mock.patch.object(hil, "REPO_ROOT", self.directory):
            rc = self.invoke(argv, environment, factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        self.assertEqual(session.writes, [psram_protocol.command_bytes(sequence)])
        status = json.loads(
            (
                self.directory
                / "psram-stale-progress"
                / "cycle-001"
                / "status.json"
            ).read_text()
        )
        self.assertIn("progress", status["reason"])

    def test_boot_protocol_ram_loads_nuttx_and_requires_ordered_startup_markers(self):
        session = FakeSession(self.clock, [GOOD_BOOT_OUTPUT])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("boot") + ["--protocol", "boot"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        command = factory.commands[0]
        self.assertNotIn("-e", command)
        self.assertNotIn("-PATCH", command)
        self.assertNotIn("-FLASH", command)
        self.assertEqual(command[-2:], ("-t", str(self.image.resolve())))
        self.assertEqual(session.writes, [])
        markers = json.loads(
            (self.directory / "boot" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertEqual(markers["reset_count"], 1)

    def test_bringup_protocol_requires_boot_and_app_markers_in_order(self):
        session = FakeSession(self.clock, [GOOD_BRINGUP_OUTPUT])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("bringup") + ["--protocol", "bringup"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertNotIn("-e", factory.commands[0])
        self.assertEqual(session.writes, [])
        markers = json.loads(
            (self.directory / "bringup" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertEqual(markers["reset_count"], 1)

    def test_bringup_failure_marker_cannot_be_hidden_by_later_pass(self):
        output = GOOD_BRINGUP_OUTPUT.replace(
            b"P2NUTTX:STACKS=OK\r\n",
            b"P2NUTTX:FAIL:STACKS\r\nP2NUTTX:STACKS=OK\r\n",
        )
        session = FakeSession(self.clock, [output])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("bringup-fail") + ["--protocol", "bringup"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (self.directory / "bringup-fail" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("P2NUTTX failure", status["reason"])

    def test_ostest_matrix_derives_all_enabled_flat_up_groups_in_order(self):
        values = full_ostest_config()

        hil.validate_ostest_config(values, "disabled")
        labels = [marker.label for marker in hil.ostest_markers(values)]

        required = (
            "user_main: task_restart test",
            "user_main: waitpid test",
            "user_main: wqueue test",
            "user_main: cancel test",
            "user_main: robust test",
            "user_main: semaphore test",
            "user_main: timed semaphore test",
            "user_main: Named semaphore test",
            "user_main: condition variable test",
            "Skipping, Test logic incompatible with priority inheritance",
            "user_main: timed message queue test",
            "user_main: message queue test",
            "user_main: signal handler test",
            "user_main: POSIX timer test",
            "user_main: hrtimer test",
            "hrtimer_test end...",
            "user_main: SIGEV_THREAD timer test",
            "user_main: round-robin scheduler test",
            "user_main: sporadic scheduler test",
            "user_main: priority inheritance test",
            "user_main: nxevent test",
            "Final memory usage:",
            "user_main: Exiting",
            "ostest_main: Exiting with status 0",
        )
        for label in required:
            self.assertIn(label, labels)
        self.assertEqual(
            labels[-2:],
            ["user_main: Exiting", "ostest_main: Exiting with status 0"],
        )
        self.assertLess(
            labels.index("user_main: timed message queue test"),
            labels.index("user_main: message queue test"),
        )

    def test_ostest_parser_requires_every_ordered_group_and_final_status(self):
        markers = hil.ostest_markers(full_ostest_config())
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )

        parser.feed(ostest_output(markers))

        self.assertTrue(parser.complete)
        self.assertIsNone(parser.failure_reason)
        self.assertEqual(parser.reset_count, 1)
        self.assertEqual(parser.missing, ())

    def test_marker_parser_does_not_duplicate_a_crlf_split_marker(self):
        marker = hil.MarkerSpec(
            "dynamic marker",
            re.compile(
                r"^P2STORAGE:VALUE=(?P<value>[0-9]+)\r?$", re.MULTILINE
            ),
        )
        parser = hil.MarkerParser((marker,), reject_duplicates=True)

        parser.feed("P2STORAGE:VALUE=6")
        parser.feed("4")
        parser.feed("\r")
        parser.feed("\n")

        self.assertTrue(parser.complete)
        self.assertIsNone(parser.failure_reason)
        self.assertEqual(parser.captures["value"], "64")
        self.assertEqual(parser.as_dict()["marker_counts"], {"dynamic marker": 1})

    def test_ostest_protocol_ram_loads_nuttx_without_terminal_input(self):
        values = full_ostest_config()
        markers = hil.ostest_markers(values)
        output = ostest_output(markers).replace(
            "user_main: hrtimer test\r\n",
            "user_main: hrtimer test\r\n"
            "hrtimer_test: [WARNING] hrtimer latency 90000 ns is too late\r\n",
        )
        session = FakeSession(self.clock, [output.encode("ascii")])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("ostest") + [
            "--protocol",
            "ostest",
            "--ostest-profile",
            "ostest-pi-production",
            "--timeout",
            "1800",
        ]

        with mock.patch.object(hil, "read_kconfig", return_value=values):
            rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertNotIn("-e", factory.commands[0])
        self.assertEqual(session.writes, [])
        marker_status = json.loads(
            (self.directory / "ostest" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(marker_status["complete"])
        self.assertEqual(marker_status["reset_count"], 1)
        self.assertIsNone(marker_status["duplicate_marker"])
        metadata = json.loads(
            (self.directory / "ostest" / "metadata.json").read_text()
        )
        self.assertFalse(metadata["debug_assertions"])
        self.assertEqual(metadata["ostest_profile"], "ostest-pi-production")
        self.assertEqual(metadata["required_groups"], [m.label for m in markers])
        self.assertEqual(len(metadata["ostest_config_sha256"]), 64)
        self.assertEqual(
            metadata["warning_counts"], {"ostest hrtimer timing WARNING": 1}
        )

    def test_ostest_parser_does_not_accept_a_missing_group(self):
        markers = hil.ostest_markers(full_ostest_config())
        output = ostest_output(markers).replace(
            "user_main: semaphore test\r\n", "", 1
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )

        parser.feed(output)

        self.assertFalse(parser.complete)
        self.assertIn("user_main: semaphore test", parser.missing)

    def test_ostest_parser_rejects_out_of_order_groups_and_reboots(self):
        markers = hil.ostest_markers(full_ostest_config())
        ordered = ostest_output(markers)
        out_of_order = ordered.replace(
            "user_main: mutex test\r\nuser_main: timed mutex test\r\n",
            "user_main: timed mutex test\r\nuser_main: mutex test\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )
        parser.feed(out_of_order)
        self.assertFalse(parser.complete)
        self.assertIn("out of order", parser.failure_reason)

        reboot = ordered.replace(
            "user_main: mutex test\r\n",
            "user_main: mutex test\r\nP2BOOT:ENTRY\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )
        parser.feed(reboot)
        self.assertEqual(parser.reset_count, 2)
        self.assertIn("unexpected entry/reset repetition", parser.failure_reason)

    def test_ostest_parser_allows_only_documented_expected_failure_phrases(self):
        markers = hil.ostest_markers(full_ostest_config())
        output = ostest_output(markers)
        output = output.replace(
            "user_main: waitpid test\r\n",
            "user_main: waitpid test\r\n"
            "waitpid_test: PASS: PID 7 waitpid failed with ECHILD.\r\n",
        )
        output = output.replace(
            "user_main: cancel test\r\n",
            "user_main: cancel test\r\n"
            "cancel_test: PASS pthread_join failed with status=ESRCH\r\n",
        )
        output = output.replace(
            "user_main: robust test\r\n",
            "user_main: robust test\r\n"
            "robust_test: Test complete with nerrors=0\r\n",
        )
        output = output.replace(
            "user_main: message queue test\r\n",
            "user_main: message queue test\r\n"
            "receiver_thread: mq_receive interrupted!\r\n",
        )
        output = output.replace(
            "user_main: wdog test\r\n",
            "user_main: wdog test\r\n"
            "WARNING: wdog latency ticks 7 (> 5 may indicate timing error)\r\n",
        )
        output = output.replace(
            "user_main: sporadic scheduler test\r\n",
            "user_main: sporadic scheduler test\r\n"
            "  -- There will some errors in the replenishment interval\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )

        parser.feed(output)

        self.assertTrue(parser.complete)
        self.assertIsNone(parser.failure_reason)

        split = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )
        split.feed("cancel_test: PASS pthread_join failed wi")
        self.assertIsNone(split.failure_reason)
        split.feed("th status=ESRCH\r\n")
        self.assertIsNone(split.failure_reason)

    def test_ostest_parser_rejects_error_failed_nonzero_and_duplicate_output(self):
        markers = hil.ostest_markers(full_ostest_config())
        failures = (
            ("worker: ERROR bad state\r\n", "ERROR/ERRROR"),
            ("worker: ERRROR create failed\r\n", "ERROR/ERRROR"),
            ("worker failed unexpectedly\r\n", "FAIL/FAILED"),
            ("receiver_thread: returning nerrors=2\r\n", "nonzero nerrors"),
            ("rr_test: Roundrobin Failed\r\n", "Roundrobin Failed"),
        )
        for index, (line, reason) in enumerate(failures):
            with self.subTest(index=index, line=line):
                parser = hil.MarkerParser(
                    markers,
                    hil.BOOT_MARKERS[0].pattern,
                    hil.OSTEST_FAILURE_PATTERNS,
                    reject_duplicates=True,
                )
                parser.feed(ostest_output(markers) + line)
                self.assertIn(reason, parser.failure_reason)

        duplicate = ostest_output(markers).replace(
            "user_main: mutex test\r\n",
            "user_main: mutex test\r\nuser_main: mutex test\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )
        parser.feed(duplicate)
        self.assertIn("duplicate protocol marker", parser.failure_reason)

    def test_ostest_parser_allows_repeated_tls_value_success_lines(self):
        markers = hil.ostest_markers(full_ostest_config())
        output = ostest_output(markers).replace(
            "tls: Successfully set\r\n",
            "tls: Successfully set 0\r\n"
            "tls: Successfully set ffffffff\r\n"
            "tls: Successfully set 55555555\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            reject_duplicates=True,
        )

        parser.feed(output)

        self.assertTrue(parser.complete)
        self.assertIsNone(parser.failure_reason)
        self.assertEqual(parser.as_dict()["marker_counts"]["tls: Successfully set"], 3)

    def test_ostest_hrtimer_warning_is_counted_but_not_a_failure(self):
        markers = hil.ostest_markers(full_ostest_config())
        output = ostest_output(markers).replace(
            "user_main: hrtimer test\r\n",
            "user_main: hrtimer test\r\n"
            "hrtimer_test: [WARNING] hrtimer latency 90000 ns is too late\r\n",
        )
        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.OSTEST_FAILURE_PATTERNS,
            warning_patterns=hil.OSTEST_WARNING_PATTERNS,
            reject_duplicates=True,
        )

        parser.feed(output)

        self.assertTrue(parser.complete)
        self.assertIsNone(parser.failure_reason)
        self.assertEqual(
            parser.warning_counts, {"ostest hrtimer timing WARNING": 1}
        )

    def test_ostest_config_validation_classifies_assertion_images(self):
        hil.validate_ostest_config(full_ostest_config(), "disabled")
        hil.validate_ostest_config(full_ostest_config(True), "enabled")
        with self.assertRaisesRegex(
            hil.SafetyError, "requires CONFIG_DEBUG_ASSERTIONS"
        ):
            hil.validate_ostest_config(full_ostest_config(), "enabled")
        with self.assertRaisesRegex(
            hil.SafetyError, "requires CONFIG_DEBUG_ASSERTIONS disabled"
        ):
            hil.validate_ostest_config(full_ostest_config(True), "disabled")

    def test_ostest_profiles_pin_pi_condition_and_assertion_state(self):
        cases = (
            ("ostest-pi-assert", True, True),
            ("ostest-pi-production", False, True),
            ("ostest-cond-assert", True, False),
            ("ostest-cond-production", False, False),
        )
        for profile, assertions, priority_inheritance in cases:
            with self.subTest(profile=profile):
                values = full_ostest_config(
                    assertions, priority_inheritance=priority_inheritance
                )
                hil.validate_ostest_config(values, "any", profile)

        with self.assertRaisesRegex(
            hil.SafetyError, "ostest-cond-assert requires"
        ):
            hil.validate_ostest_config(
                full_ostest_config(False, priority_inheritance=False),
                "any",
                "ostest-cond-assert",
            )
        with self.assertRaisesRegex(
            hil.SafetyError, "CONFIG_PRIORITY_INHERITANCE"
        ):
            hil.validate_ostest_config(
                full_ostest_config(True), "any", "ostest-cond-assert"
            )

    def test_ostest_profile_identity_checks_every_defconfig_value(self):
        for profile in hil.OSTEST_PROFILES:
            with self.subTest(profile=profile):
                values = hil.read_kconfig(hil.ostest_profile_path(profile))
                hil.validate_ostest_profile_values(values, profile)

        values = hil.read_kconfig(
            hil.ostest_profile_path("ostest-pi-production")
        )
        values["CONFIG_TESTING_OSTEST_RR_RANGE"] = "1"
        with self.assertRaisesRegex(
            hil.SafetyError, "CONFIG_TESTING_OSTEST_RR_RANGE=1"
        ):
            hil.validate_ostest_profile_values(
                values, "ostest-pi-production"
            )

    def test_ostest_condition_profile_requires_real_cond_test_summary(self):
        values = full_ostest_config(priority_inheritance=False)
        markers = hil.ostest_markers(values)
        labels = [marker.label for marker in markers]

        self.assertIn("cond_test: Initializing mutex", labels)
        self.assertIn("cond_test: Errors 0 0", labels)
        self.assertNotIn(
            "Skipping, Test logic incompatible with priority inheritance",
            labels,
        )
        self.assertNotIn("user_main: priority inheritance test", labels)

        parser = hil.MarkerParser(
            markers,
            hil.BOOT_MARKERS[0].pattern,
            hil.ostest_failure_patterns(values),
            reject_duplicates=True,
        )
        parser.feed(
            ostest_output(markers).replace(
                "cond_test: Initializing mutex\r\n",
                "Skipping, condition test did not execute\r\n"
                "cond_test: Initializing mutex\r\n",
            )
        )
        self.assertIn("unexpected ostest Skipping", parser.failure_reason)

    def test_ostest_config_validation_refuses_a_reduced_matrix(self):
        values = full_ostest_config()
        del values["CONFIG_SCHED_SPORADIC"]

        with self.assertRaisesRegex(hil.SafetyError, "CONFIG_SCHED_SPORADIC"):
            hil.validate_ostest_config(values)

    def test_nsh_protocol_runs_full_command_matrix_after_prompt(self):
        session = FakeSession(
            self.clock,
            [
                GOOD_BOOT_OUTPUT + b"NuttShell (NSH)\r\nnsh> ",
                GOOD_NSH_BEFORE_SLEEP,
                1.0,
                GOOD_NSH_AFTER_SLEEP,
            ],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh") + ["--protocol", "nsh", "--timeout", "2"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual(session.writes, [hil.NSH_COMMAND_BYTES])
        self.assertNotIn("-e", factory.commands[0])
        status = json.loads(
            (self.directory / "nsh" / "cycle-001" / "status.json").read_text()
        )
        self.assertTrue(status["interactive_send_completed"])
        self.assertGreaterEqual(
            status["nsh_sleep_elapsed_seconds"], hil.NSH_SLEEP_MIN_SECONDS
        )
        self.assertLessEqual(
            status["nsh_sleep_elapsed_seconds"], hil.NSH_SLEEP_MAX_SECONDS
        )
        command = json.loads(
            (self.directory / "nsh" / "cycle-001" / "command.json").read_text()
        )
        self.assertEqual(command["interactive_commands"], list(hil.NSH_COMMANDS))
        self.assertEqual(
            command["interactive_send_ascii"], hil.NSH_COMMAND_BYTES.decode("ascii")
        )
        markers = json.loads(
            (self.directory / "nsh" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertEqual(markers["reset_count"], 1)
        for marker in hil.NSH_COMMAND_MARKERS:
            self.assertIn(marker.label, markers["found"])

    def test_nsh_command_echo_without_specific_output_is_not_success(self):
        command_echo_only = b"help\r\nnsh> echo P2NSH:HELP=OK\r\n"
        command_echo_only += b"P2NSH:HELP=OK\r\nnsh> "
        session = FakeSession(
            self.clock,
            [GOOD_BOOT_OUTPUT + b"nsh> ", command_echo_only],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh-echo-only") + ["--protocol", "nsh"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        self.assertEqual(session.writes, [hil.NSH_COMMAND_BYTES])
        markers = json.loads(
            (
                self.directory
                / "nsh-echo-only"
                / "cycle-001"
                / "markers.json"
            ).read_text()
        )
        self.assertIn(
            "NSH help output, sentinel, and prompts", markers["missing"]
        )

    def test_nsh_does_not_accept_response_text_received_before_probe_send(self):
        unsolicited = (
            GOOD_BOOT_OUTPUT
            + b"nsh> "
            + GOOD_NSH_RESPONSE
        )
        session = FakeSession(self.clock, [unsolicited])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh-unsolicited") + ["--protocol", "nsh"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        self.assertEqual(session.writes, [hil.NSH_COMMAND_BYTES])
        markers = json.loads(
            (
                self.directory
                / "nsh-unsolicited"
                / "cycle-001"
                / "markers.json"
            ).read_text()
        )
        self.assertIn(
            "NSH help output, sentinel, and prompts", markers["missing"]
        )

    def test_nsh_required_command_not_found_fails_immediately(self):
        session = FakeSession(
            self.clock,
            [
                GOOD_BOOT_OUTPUT + b"nsh> ",
                b"help\r\nnsh: help: command not found\r\nnsh> ",
            ],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh-command-not-found") + ["--protocol", "nsh"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (
                self.directory
                / "nsh-command-not-found"
                / "cycle-001"
                / "status.json"
            ).read_text()
        )
        self.assertIn("required NSH command not found", status["reason"])

    def test_nsh_sleep_that_returns_too_quickly_is_not_success(self):
        session = FakeSession(
            self.clock,
            [
                GOOD_BOOT_OUTPUT + b"nsh> ",
                GOOD_NSH_BEFORE_SLEEP,
                0.1,
                GOOD_NSH_AFTER_SLEEP,
            ],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh-fast-sleep") + ["--protocol", "nsh"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (
                self.directory / "nsh-fast-sleep" / "cycle-001" / "status.json"
            ).read_text()
        )
        self.assertIn("sleep 1 timing outside", status["reason"])

    def test_nsh_missing_intermediate_sentinel_prompt_is_not_success(self):
        response = GOOD_NSH_RESPONSE.replace(
            b"P2NSH:FREE=OK\r\nnsh> ", b"P2NSH:FREE=OK\r\n", 1
        )
        before_sleep, after_sleep = response.split(b"sleep 1\r\n", 1)
        session = FakeSession(
            self.clock,
            [
                GOOD_BOOT_OUTPUT + b"nsh> ",
                before_sleep + b"sleep 1\r\n",
                1.0,
                after_sleep,
            ],
        )
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("nsh-missing-prompt") + [
            "--protocol",
            "nsh",
            "--timeout",
            "2",
        ]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        markers = json.loads(
            (
                self.directory
                / "nsh-missing-prompt"
                / "cycle-001"
                / "markers.json"
            ).read_text()
        )
        self.assertIn(
            "NSH free output, sentinel, and prompts", markers["missing"]
        )

    def test_boot_failure_marker_takes_precedence_over_later_success_text(self):
        output = (
            b"P2BOOT:ENTRY\r\nP2BOOT:DATA=FAIL\r\n"
            b"P2BOOT:DATA=OK\r\nP2BOOT:BSS=OK\r\nP2BOOT:NX_START\r\n"
        )
        session = FakeSession(self.clock, [output])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("boot-failure") + ["--protocol", "boot"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (
                self.directory / "boot-failure" / "cycle-001" / "status.json"
            ).read_text()
        )
        self.assertIn("P2BOOT:DATA=FAIL", status["reason"])

    def test_missing_marker_fails_and_records_exact_missing_marker(self):
        output = GOOD_OUTPUT.replace(b"P2HELLO:ECHO=?\r\n", b"")
        session = FakeSession(self.clock, [output])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("missing"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        markers = json.loads(
            (self.directory / "missing" / "cycle-001" / "markers.json").read_text()
        )
        self.assertIn("P2HELLO:ECHO=?", markers["missing"])
        status = json.loads(
            (self.directory / "missing" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("bounded timeout", status["reason"])

    def test_panic_wins_even_when_success_markers_share_the_chunk(self):
        session = FakeSession(self.clock, [GOOD_OUTPUT + b"PANIC: trap\n"])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("panic"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (self.directory / "panic" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("panic/assert marker", status["reason"])

    def test_bounded_timeout_terminates_and_closes_loader(self):
        session = FakeSession(self.clock, [])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("timeout"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertGreaterEqual(self.clock.value, 0.3)
        status = json.loads(
            (self.directory / "timeout" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("bounded timeout", status["reason"])

    def test_nonzero_loader_exit_is_a_failure(self):
        session = FakeSession(
            self.clock, [b"Could not open serial port\n", "eof"], returncode=7
        )
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("loader-exit"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (self.directory / "loader-exit" / "cycle-001" / "status.json").read_text()
        )
        self.assertEqual(status["loader_returncode"], 7)
        self.assertIn("loadp2 exited with code 7", status["reason"])


if __name__ == "__main__":
    unittest.main()
