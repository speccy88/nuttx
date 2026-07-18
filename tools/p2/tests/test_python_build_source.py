#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Cross-tree source checks for the P2 CPython build contract."""

from __future__ import annotations

import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
APPS = pathlib.Path(os.environ.get("NUTTX_APPS_DIR", ROOT.parent / "apps"))
MAKEFILE = APPS / "interpreters/python/Makefile"
MAKEDEFS = APPS / "interpreters/python/Make.defs"
APPS_MAKEDEFS = APPS / "Make.defs"
BUILTIN_MAKEFILE = APPS / "builtin/Makefile"
ZLIB_MAKEFILE = APPS / "system/zlib/Makefile"
BUILD_WRAPPER = ROOT / "tools/p2/build.sh"
ARCH_KCONFIG = ROOT / "arch/p2/Kconfig"


@unittest.skipUnless(MAKEFILE.is_file(), "set NUTTX_APPS_DIR to the apps tree")
class PythonBuildSourceTests(unittest.TestCase):
    def test_generated_python_staging_does_not_dirty_clean_source(self):
        ignores = (ROOT / ".gitignore").read_text()

        self.assertIn("/p2-overlay.ld\n", ignores)
        self.assertIn("/p2-python-package/\n", ignores)

    @classmethod
    def setUpClass(cls) -> None:
        cls.makefile = MAKEFILE.read_text(encoding="utf-8")
        cls.makedefs = MAKEDEFS.read_text(encoding="utf-8")
        cls.apps_makedefs = APPS_MAKEDEFS.read_text(encoding="utf-8")
        cls.builtin_makefile = BUILTIN_MAKEFILE.read_text(encoding="utf-8")
        cls.zlib_makefile = ZLIB_MAKEFILE.read_text(encoding="utf-8")
        cls.build_wrapper = BUILD_WRAPPER.read_text(encoding="utf-8")
        cls.arch_kconfig = ARCH_KCONFIG.read_text(encoding="utf-8")
        cls.setup_local = (MAKEFILE.parent / "Setup.local.in").read_text(
            encoding="utf-8"
        )
        cls.wrapper = (MAKEFILE.parent / "python_wrapper.c").read_text(encoding="utf-8")
        cls.launcher = (MAKEFILE.parent / "python_launcher.c").read_text(
            encoding="utf-8"
        )

    def test_overlay_link_scans_every_cpython_archive(self) -> None:
        self.assertIn(
            "P2_OVERLAY_INPUTS += $(TOPDIR)/staging/libapps.a",
            self.makedefs,
        )
        libapps = self.makedefs.index(
            "P2_OVERLAY_INPUTS += $(TOPDIR)/staging/libapps.a"
        )
        zlib_gate = self.makedefs.rfind(
            "ifeq ($(CONFIG_P2_HUB_OVERLAY_ZLIB),y)", 0, libapps
        )
        self.assertNotEqual(zlib_gate, -1)
        self.assertLess(zlib_gate, libapps)
        self.assertIn(
            "P2_OVERLAY_INPUTS += $(APPDIR)/interpreters/python/install/target/"
            "libpython$(CPYTHON_VERSION_MINOR).a",
            self.makedefs,
        )
        self.assertIn(
            "P2_OVERLAY_INPUTS += $(APPDIR)/interpreters/python/build/target/"
            "Modules/_hacl/libHacl_Hash_SHA2.a",
            self.makedefs,
        )
        self.assertIn(
            "P2_OVERLAY_INPUTS += $(APPDIR)/interpreters/python/build/target/"
            "Modules/expat/libexpat.a",
            self.makedefs,
        )

    def test_generated_builtin_table_directly_invalidates_its_object(self) -> None:
        include = self.builtin_makefile.index(
            "include $(APPDIR)/Application.mk"
        )
        objects = self.builtin_makefile.index(
            "BUILTIN_LIST_OBJS := "
            "$(filter $(PREFIX)builtin_list.c%,$(COBJS))",
            include,
        )
        dependency = self.builtin_makefile.index(
            "$(BUILTIN_LIST_OBJS): builtin_list.h builtin_proto.h",
            objects,
        )
        self.assertLess(include, objects)
        self.assertLess(objects, dependency)

    def test_configure_probes_are_not_overlay_transformed(self) -> None:
        configure_start = self.makefile.index("$(TARGETBUILD)/Makefile:")
        configure_end = self.makefile.index("BUNDLED_WHEELS_DIR", configure_start)
        configure_rule = self.makefile[configure_start:configure_end]

        self.assertIn("$(CPYTHON_PATH)/configure", configure_rule)
        self.assertNotIn("$(P2_CPYTHON_CFLAGS)", configure_rule)
        self.assertIn("-p2-unified-memory([[:space:]]|$$)", configure_rule)
        self.assertIn("--target=p2([[:space:]]|$$)", configure_rule)
        self.assertIn('CC="$(P2_CONFIGURE_CC)"', configure_rule)
        self.assertIn('CXX="$(P2_CONFIGURE_CXX)"', configure_rule)
        self.assertIn("--without-computed-gotos", configure_rule)
        self.assertIn("P2_CONFIGURE_CC=$(CC) --target=p2", self.makefile)

    def test_configure_probe_archives_recover_after_cpython_clean(self) -> None:
        self.assertIn(
            "P2_CONFIGURE_LIBC=$(P2_CONFIGURE_LINKDIR)/libc.a",
            self.makefile,
        )
        self.assertIn(
            "P2_CONFIGURE_LIBP2=$(P2_CONFIGURE_LINKDIR)/libp2.a",
            self.makefile,
        )
        self.assertIn(
            "$(P2_CONFIGURE_LIBC) $(P2_CONFIGURE_LIBP2):",
            self.makefile,
        )
        stamp = self.makefile.index("$(P2_CONFIGURE_LINK_STAMP):")
        target = self.makefile.index("$(TARGETBUILD)/Makefile:", stamp)
        stamp_rule = self.makefile[stamp:target]
        self.assertIn("$(P2_CONFIGURE_LIBC)", stamp_rule)
        self.assertIn("$(P2_CONFIGURE_LIBP2)", stamp_rule)

    def test_production_build_receives_overlay_and_xdata_flags(self) -> None:
        build_start = self.makefile.index("$(TARGETLIBPYTHON):")
        build_end = self.makefile.index("MODULE    =", build_start)
        build_rule = self.makefile[build_start:build_end]

        self.assertIn(
            "P2_CPYTHON_CFLAGS += -mllvm -p2-unified-memory",
            self.makefile,
        )
        self.assertIn(
            "P2_CPYTHON_CFLAGS += -mllvm -p2-externalize-data",
            self.makefile,
        )
        self.assertNotIn(
            "-p2-externalize-constant-data", self.apps_makedefs
        )
        self.assertEqual(
            build_rule.count('EXTRA_CFLAGS="$(P2_CPYTHON_CFLAGS)"'),
            2,
        )
        self.assertIn("libpython$(CPYTHON_VERSION_MINOR).a wasm_stdlib", build_rule)

    def test_builtin_zlib_is_container_backed_on_p2(self) -> None:
        for flag in (
            "-p2-hub-overlays",
            "-p2-hub-overlays-all",
            "-p2-hub-overlay-auto-groups=",
            "-p2-hub-overlay-slot-address=",
            "-p2-externalize-data",
        ):
            self.assertIn(flag, self.zlib_makefile)
        zlib_gate = self.zlib_makefile.index(
            "ifeq ($(CONFIG_P2_HUB_OVERLAY_ZLIB),y)"
        )
        overlay_flags = self.zlib_makefile.index("-p2-hub-overlays-all")
        self.assertLess(zlib_gate, overlay_flags)
        self.assertIn("check-zlib-overlay.py", self.build_wrapper)
        self.assertIn("zlib-overlay-audit.txt", self.build_wrapper)
        self.assertIn(
            "zlib-link-input-libapps.a", self.build_wrapper
        )
        self.assertIn(
            'cp "$zlib_archive" "$zlib_audit_archive"',
            self.build_wrapper,
        )
        self.assertIn(
            '--archive "$zlib_audit_archive"', self.build_wrapper
        )
        self.assertIn("--slot-start \"$p2_overlay_slot_start\"", self.build_wrapper)
        self.assertIn("--slot-end \"$p2_overlay_slot_end\"", self.build_wrapper)
        self.assertIn("--xmem-start 0x10000000", self.build_wrapper)
        self.assertIn("--xmem-end 0x12000000", self.build_wrapper)
        self.assertIn(
            "zlib code or data escaped the P2 Python overlay container",
            self.build_wrapper,
        )
        zlib_config = self.arch_kconfig[
            self.arch_kconfig.index("config P2_HUB_OVERLAY_ZLIB") :
            self.arch_kconfig.index("config P2_HUB_OVERLAY_LIBM")
        ]
        for dependency in (
            "depends on INTERPRETERS_CPYTHON_EXTERNAL_ROMFS",
            "depends on !LIB_ZLIB_TEST",
            "depends on !UTILS_GZIP",
            "depends on !UTILS_ZIP",
            "depends on !UTILS_UNZIP",
        ):
            self.assertIn(dependency, zlib_config)

    def test_launcher_serializes_the_process_global_runtime(self) -> None:
        self.assertIn("static mutex_t g_cpython_runtime_lock", self.launcher)
        lock = self.launcher.index("nxmutex_trylock(&g_cpython_runtime_lock)")
        create = self.launcher.index("pthread_create(&worker", lock)
        join = self.launcher.index("pthread_join(worker, NULL)", create)
        unlock = self.launcher.index("return python_unlock(args.result)", join)
        self.assertLess(lock, create)
        self.assertLess(create, join)
        self.assertLess(join, unlock)
        self.assertIn(
            "python_worker_main(args->argc, args->argv)", self.launcher
        )
        self.assertIn("ret == -EAGAIN || ret == -EBUSY", self.launcher)
        self.assertIn("P2PY:RUNTIME:BUSY:CODE=%d", self.launcher)
        self.assertIn("P2PY:WORKER:EXIT:CODE=%d", self.launcher)
        self.assertIn("up_check_tcbstack(tcb, size)", self.launcher)
        self.assertIn("P2PY:WORKER:STACK:FREE=%zu:SIZE=%zu", self.launcher)
        worker_result = self.launcher.index(
            "args->result = python_worker_main(args->argc, args->argv)"
        )
        exit_marker = self.launcher.index(
            "P2PY:WORKER:EXIT:CODE=%d", worker_result
        )
        stack_marker = self.launcher.index(
            "P2PY:WORKER:STACK:FREE=%zu:SIZE=%zu", exit_marker
        )
        self.assertLess(worker_result, exit_marker)
        self.assertLess(exit_marker, stack_marker)

        prepare = self.wrapper.index("board_cpython_runtime_prepare")
        run = self.wrapper.index("py_bytesmain(argc, argv)", prepare)
        self.assertLess(prepare, run)

    def test_p2_bootstraps_zlib_and_omits_unsafe_runtime_surfaces(self) -> None:
        active = []
        for line in self.setup_local.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "*disabled*":
                break
            active.append(line)

        self.assertEqual(active, ["zlib zlibmodule.c"])
        setup_rule = self.makefile[
            self.makefile.index("$(SETUP_LOCAL):") :
            self.makefile.index("# For the Python's `configure`", self.makefile.index("$(SETUP_LOCAL):"))
        ]
        self.assertIn('@echo "_thread" >> $@', setup_rule)
        self.assertIn('@echo "_interpreters" >> $@', setup_rule)
        self.assertIn("single-Python-task runtime", setup_rule)
        self.assertIn("/^MODBUILT_NAMES=/", self.build_wrapper)
        self.assertIn('if ($field == "zlib")', self.build_wrapper)
        self.assertNotIn(
            "grep -Fqx 'MODULE_ZLIB_STATE=builtin'", self.build_wrapper
        )
        self.assertIn("PyInit_zlib", self.build_wrapper)
        self.assertIn("P2 CPython must explicitly disable _thread", self.build_wrapper)
        self.assertIn("P2 CPython must explicitly disable subinterpreters", self.build_wrapper)
        self.assertIn("PyInit__thread", self.build_wrapper)
        self.assertIn("PyInit__interpreters", self.build_wrapper)
        self.assertIn("prefix=/usr/local", self.makefile)

    def test_hil_filesystem_and_uart_window_are_build_contracts(self) -> None:
        for setting in (
            "CONFIG_FS_TMPFS=y",
            "CONFIG_P2_HUB_OVERLAY_ZLIB=y",
            "CONFIG_INTERPRETERS_CPYTHON_ROMFS_SECTORSIZE=512",
            "CONFIG_UART0_BAUD=230400",
            "CONFIG_UART0_RXBUFSIZE=2048",
            "# CONFIG_NSH_DISABLE_ECHO is not set",
            "# CONFIG_NSH_DISABLE_MKDIR is not set",
            "# CONFIG_NSH_DISABLE_MOUNT is not set",
            "# CONFIG_RAW_BINARY is not set",
        ):
            self.assertIn(setting, self.build_wrapper)

    def test_p2_runtime_initializer_is_container_backed_not_a_huge_function(
        self,
    ) -> None:
        patch = (
            MAKEFILE.parent
            / "patch"
            / ("0012-hack-place-_PyRuntime-structure-into-PSRAM-bss-regio.patch")
        ).read_text(encoding="utf-8")
        self.assertIn("#if defined(__propeller2__)", patch)
        p2_branch = patch.split("#if defined(__propeller2__)", 1)[1].split("#elif", 1)[
            0
        ]
        self.assertNotIn("_PyRuntime =", p2_branch)
        self.assertIn(
            "P2 loader materializes the static .p2.xdata initializer once",
            patch,
        )


if __name__ == "__main__":
    unittest.main()
