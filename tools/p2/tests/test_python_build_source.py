#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Cross-tree source checks for the P2 CPython build contract."""

from __future__ import annotations

import ast
import os
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
APPS = pathlib.Path(os.environ.get("NUTTX_APPS_DIR", ROOT.parent / "apps"))
MAKEFILE = APPS / "interpreters/python/Makefile"
MAKEDEFS = APPS / "interpreters/python/Make.defs"
APPS_MAKEDEFS = APPS / "Make.defs"
BUILTIN_MAKEFILE = APPS / "builtin/Makefile"
ZLIB_MAKEFILE = APPS / "system/zlib/Makefile"
LIBM_MAKEFILE = ROOT / "libs/libm/Makefile"
BUILD_WRAPPER = ROOT / "tools/p2/build.sh"
LOCAL_BOOTSTRAP = ROOT / "tools/p2/bootstrap-local.sh"
CLOUD_BOOTSTRAP = ROOT / "tools/p2/bootstrap-cloud.sh"
OVERLAY_CHECKER = ROOT / "tools/p2/check-hub-overlay-codegen.py"
OVERLAY_PATCH = ROOT / "tools/p2/patches/p2llvm-python-overlays.patch"
ARCH_KCONFIG = ROOT / "arch/p2/Kconfig"
DICTIONARY_HOTPATH_PATCH = (
    APPS
    / "interpreters/python/patch/0033-p2-co-locate-dictionary-hot-paths-with-type-init.patch"
)
MODULE_ATTRIBUTE_PATCH = (
    APPS
    / "interpreters/python/patch/0034-p2-co-locate-module-attribute-startup-loop.patch"
)
GC_TRAVERSAL_PATCH = (
    APPS
    / "interpreters/python/patch/0035-p2-co-locate-gc-traversal-working-set.patch"
)
THREADLESS_IMPORTLIB_PATCH = (
    APPS
    / "interpreters/python/patch/0040-allow-importlib-without-thread-module.patch"
)
LOCK_ONLY_THREAD_PATCH = (
    APPS
    / "interpreters/python/patch/0041-add-lock-only-thread-compatibility.patch"
)
IMPORTLIB_STARTUP_HOTPATH_PATCH = (
    APPS
    / "interpreters/python/patch/0042-p2-co-locate-importlib-startup-hot-paths.patch"
)
MAIN_INTERPRETER_TRACE_PATCH = (
    APPS
    / "interpreters/python/patch/0043-p2-trace-main-interpreter-initialization.patch"
)
FILL_TIME_SOFTFLOAT_TRACE_PATCH = (
    APPS
    / "interpreters/python/patch/0044-p2-trace-fill-time-softfloat-boundaries.patch"
)
DEFAULT_NO_SITE_PATCH = (
    APPS
    / "interpreters/python/patch/0045-p2-add-default-no-site-config-hook.patch"
)
FIXED_PATH_CONFIG_PATCH = (
    APPS
    / "interpreters/python/patch/0046-p2-add-fixed-path-config-fast-path.patch"
)
STATIC_UNICODE_HOT_EDGE_PATCH = (
    APPS
    / "interpreters/python/patch/0047-p2-inline-static-unicode-compare-hot-edge.patch"
)
TUPLE_ITERATOR_HOT_EDGE_PATCH = (
    APPS
    / "interpreters/python/patch/0048-p2-inline-exact-tuple-iterator-hot-edge.patch"
)
MAIN_INTERPRETER_SUBPHASE_TRACE_PATCH = (
    APPS
    / "interpreters/python/patch/0049-p2-trace-main-interpreter-subphases.patch"
)
MARSHAL_LIST_APPEND_HOT_EDGE_PATCH = (
    APPS
    / "interpreters/python/patch/0050-p2-inline-marshal-reference-list-append.patch"
)
MARSHAL_ASCII_HOT_EDGE_PATCH = (
    APPS
    / "interpreters/python/patch/0051-p2-use-utf8-decoder-for-marshal-ascii.patch"
)
POSIX_CONSTDEF_RESIDENCY_PATCH = (
    APPS
    / "interpreters/python/patch/0052-p2-pin-posix-constdef-comparator.patch"
)
FROZEN_STARTUP_ENCODINGS_PATCH = (
    APPS
    / "interpreters/python/patch/0053-p2-freeze-startup-encodings.patch"
)
FROZEN_STARTUP_ENCODINGS_PCBUILD_PATCH = (
    APPS
    / "interpreters/python/patch/0054-p2-record-frozen-encoding-pcbuild.patch"
)
PYTHON_HIL_RUNNER = ROOT / "tools/p2/test-python.py"


def added_file_source(patch: str, path: str) -> str:
    """Extract one newly-added file from a mail-format unified diff."""

    start = patch.index("+++ b/{}\n".format(path))
    start = patch.index("@@ ", start)
    start = patch.index("\n", start) + 1
    end = patch.find("\ndiff --git ", start)
    if end < 0:
        end = len(patch)
    return "\n".join(
        line[1:]
        for line in patch[start:end].splitlines()
        if line.startswith("+")
    ) + "\n"


def materialize_patch_preimages(
    root: pathlib.Path, patch_paths: tuple[pathlib.Path, ...]
) -> None:
    """Build minimal old-side source files for a patch-sequence check."""

    images: dict[str, dict[int, str]] = {}
    hunk_re = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
    )

    for patch_path in patch_paths:
        lines = patch_path.read_text(encoding="utf-8").splitlines()
        index = 0
        current_path: str | None = None

        while index < len(lines):
            file_match = re.match(
                r"^diff --git a/(\S+) b/(\S+)$", lines[index]
            )
            if file_match:
                current_path = file_match.group(1)
                self_path = file_match.group(2)
                if current_path != self_path:
                    raise AssertionError(
                        f"renames are unsupported in fixture: {lines[index]}"
                    )
                images.setdefault(current_path, {})
                index += 1
                continue

            hunk_match = hunk_re.match(lines[index])
            if hunk_match and current_path is not None:
                old_line = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2) or "1")
                new_count = int(hunk_match.group(4) or "1")
                old_seen = 0
                new_seen = 0
                index += 1

                while old_seen < old_count or new_seen < new_count:
                    if index >= len(lines):
                        raise AssertionError(
                            f"truncated hunk in {patch_path.name}"
                        )
                    line = lines[index]
                    if line == r"\ No newline at end of file":
                        index += 1
                        continue
                    if not line or line[0] not in " +-":
                        raise AssertionError(
                            f"malformed hunk line in {patch_path.name}: {line!r}"
                        )

                    prefix = line[0]
                    if prefix in " -":
                        line_number = old_line + old_seen
                        previous = images[current_path].get(line_number)
                        if previous is not None and previous != line[1:]:
                            raise AssertionError(
                                f"conflicting preimage for {current_path}:"
                                f"{line_number}"
                            )
                        images[current_path][line_number] = line[1:]
                        old_seen += 1
                    if prefix in " +":
                        new_seen += 1
                    index += 1

                continue

            index += 1

    for relative_path, numbered_lines in images.items():
        if not numbered_lines:
            raise AssertionError(f"patch has no old-side lines: {relative_path}")
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        last_line = max(numbered_lines)
        source = [
            numbered_lines.get(line, f"/* patch fixture line {line} */")
            for line in range(1, last_line + 1)
        ]
        path.write_text("\n".join(source) + "\n", encoding="utf-8")


@unittest.skipUnless(MAKEFILE.is_file(), "set NUTTX_APPS_DIR to the apps tree")
class PythonBuildSourceTests(unittest.TestCase):
    def test_generated_python_staging_does_not_dirty_clean_source(self):
        ignores = (ROOT / ".gitignore").read_text()

        self.assertIn("/p2-overlay.ld\n", ignores)
        self.assertIn("/p2-python-package/\n", ignores)

    def test_final_elf_verifier_uses_configured_python(self) -> None:
        self.assertIn(
            '"$python" ./tools/p2/verify-elf.py nuttx',
            self.build_wrapper,
        )
        self.assertNotIn(
            '\n./tools/p2/verify-elf.py nuttx',
            self.build_wrapper,
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.makefile = MAKEFILE.read_text(encoding="utf-8")
        cls.makedefs = MAKEDEFS.read_text(encoding="utf-8")
        cls.apps_makedefs = APPS_MAKEDEFS.read_text(encoding="utf-8")
        cls.builtin_makefile = BUILTIN_MAKEFILE.read_text(encoding="utf-8")
        cls.zlib_makefile = ZLIB_MAKEFILE.read_text(encoding="utf-8")
        cls.libm_makefile = LIBM_MAKEFILE.read_text(encoding="utf-8")
        cls.build_wrapper = BUILD_WRAPPER.read_text(encoding="utf-8")
        cls.local_bootstrap = LOCAL_BOOTSTRAP.read_text(encoding="utf-8")
        cls.cloud_bootstrap = CLOUD_BOOTSTRAP.read_text(encoding="utf-8")
        cls.overlay_checker = OVERLAY_CHECKER.read_text(encoding="utf-8")
        cls.arch_kconfig = ARCH_KCONFIG.read_text(encoding="utf-8")
        cls.dictionary_hotpath_patch = DICTIONARY_HOTPATH_PATCH.read_text(
            encoding="utf-8"
        )
        cls.module_attribute_patch = MODULE_ATTRIBUTE_PATCH.read_text(
            encoding="utf-8"
        )
        cls.gc_traversal_patch = GC_TRAVERSAL_PATCH.read_text(encoding="utf-8")
        cls.threadless_importlib_patch = THREADLESS_IMPORTLIB_PATCH.read_text(
            encoding="utf-8"
        )
        cls.lock_only_thread_patch = LOCK_ONLY_THREAD_PATCH.read_text(
            encoding="utf-8"
        )
        cls.importlib_startup_hotpath_patch = (
            IMPORTLIB_STARTUP_HOTPATH_PATCH.read_text(encoding="utf-8")
        )
        cls.main_interpreter_trace_patch = (
            MAIN_INTERPRETER_TRACE_PATCH.read_text(encoding="utf-8")
        )
        cls.fill_time_softfloat_trace_patch = (
            FILL_TIME_SOFTFLOAT_TRACE_PATCH.read_text(encoding="utf-8")
        )
        cls.default_no_site_patch = DEFAULT_NO_SITE_PATCH.read_text(
            encoding="utf-8"
        )
        cls.fixed_path_config_patch = FIXED_PATH_CONFIG_PATCH.read_text(
            encoding="utf-8"
        )
        cls.static_unicode_hot_edge_patch = (
            STATIC_UNICODE_HOT_EDGE_PATCH.read_text(encoding="utf-8")
        )
        cls.tuple_iterator_hot_edge_patch = (
            TUPLE_ITERATOR_HOT_EDGE_PATCH.read_text(encoding="utf-8")
        )
        cls.main_interpreter_subphase_trace_patch = (
            MAIN_INTERPRETER_SUBPHASE_TRACE_PATCH.read_text(encoding="utf-8")
        )
        cls.marshal_list_append_hot_edge_patch = (
            MARSHAL_LIST_APPEND_HOT_EDGE_PATCH.read_text(encoding="utf-8")
        )
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
            "-p2-hub-overlay-link-assigned",
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
        self.assertIn('--map-archive "$zlib_archive"', self.build_wrapper)
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

    def test_overlay_translation_units_have_stable_build_domain_identity(self):
        contracts = (
            (
                self.makefile,
                "P2_CPYTHON_CFLAGS += -mllvm -p2-hub-overlay-link-assigned",
                (
                    "P2_CPYTHON_CFLAGS += -mllvm "
                    "-p2-hub-overlay-source-root=$(abspath $(CPYTHON_PATH))",
                    "P2_CPYTHON_CFLAGS += -mllvm "
                    "-p2-hub-overlay-source-namespace=cpython",
                    "P2_CPYTHON_CFLAGS += -mllvm "
                    "-p2-hub-overlay-source-variant=$(CPYTHON_VERSION)-nuttx-p2",
                ),
            ),
            (
                self.zlib_makefile,
                "CFLAGS += -mllvm -p2-hub-overlay-link-assigned",
                (
                    "CFLAGS += -mllvm "
                    "-p2-hub-overlay-source-root=$(abspath $(CURDIR))",
                    "CFLAGS += -mllvm -p2-hub-overlay-source-namespace=zlib",
                    "CFLAGS += -mllvm "
                    "-p2-hub-overlay-source-variant=1.3-nuttx-p2",
                ),
            ),
            (
                self.libm_makefile,
                "P2_LIBM_OVERLAY_FLAGS += -mllvm "
                "-p2-hub-overlay-link-assigned",
                (
                    "P2_LIBM_OVERLAY_FLAGS += -mllvm "
                    "-p2-hub-overlay-source-root=$(abspath $(CURDIR))",
                    "P2_LIBM_OVERLAY_FLAGS += -mllvm "
                    "-p2-hub-overlay-source-namespace=newlib-libm",
                    "P2_LIBM_OVERLAY_FLAGS += -mllvm "
                    "-p2-hub-overlay-source-variant=nuttx-p2",
                ),
            ),
        )
        for source, link_assigned, identity_flags in contracts:
            with self.subTest(namespace=identity_flags[1]):
                previous = source.index(link_assigned)
                for flag in identity_flags:
                    current = source.index(flag, previous)
                    self.assertLess(previous, current)
                    previous = current

    def test_overlay_compiler_patch_and_postcondition_are_hash_locked(self):
        digest = hashlib.sha256(OVERLAY_PATCH.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "2cabd4544cc02e7f9e5ae0e15f937a207e49c83c9a6c4e9569d85570dcd2890e",
        )
        for bootstrap in (self.local_bootstrap, self.cloud_bootstrap):
            self.assertIn(
                "P2LLVM_OVERLAY_PATCH=$ROOT/tools/p2/patches/"
                "p2llvm-python-overlays.patch",
                bootstrap,
            )
            self.assertIn(
                'P2LLVM_PATCHES=("$P2LLVM_PREEMPT_PATCH" '
                '"$P2LLVM_UNIFIED_PATCH" "$P2LLVM_OVERLAY_PATCH")',
                bootstrap,
            )
            self.assertIn('"$P2LLVM_OVERLAY_PATCH"', bootstrap)
            self.assertIn("p2llvm_overlay_patch=", bootstrap)
            self.assertIn("check-hub-overlay-codegen.py", bootstrap)

        for token in (
            '"$ROOT/tools/p2/patches/p2llvm-python-overlays.patch"',
            "check-hub-overlay-codegen.py",
            "hub-overlay-codegen.txt",
            '"$P2LLVM_ROOT/bin/p2-overlay-link.py"',
        ):
            self.assertIn(token, self.build_wrapper)

        for token in (
            "p2-hub-overlay-source-root=",
            "p2-hub-overlay-source-namespace=",
            "p2-hub-overlay-source-variant=",
            "duplicate link-assigned section",
            "LONG(0)",
            "01000000\\s+04000000",
            "TemporaryDirectory",
            "same-known-group call did not target the private body",
            "cross-group call no longer uses its veneer",
            "function pointer no longer names the public veneer",
        ):
            self.assertIn(token, self.overlay_checker)

        overlay_patch = OVERLAY_PATCH.read_text(encoding="utf-8")
        for token in (
            "rewriteKnownSameGroupCalls",
            "getPointerBitCastOrAddrSpaceCast",
            "LLVMContext::MD_prof",
            "LLVMContext::MD_callees",
            "hub-overlay-same-group-addrspace.ll",
            "hub-overlay-same-group-metadata.ll",
        ):
            self.assertIn(token, overlay_patch)

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
        begin = self.wrapper.index('python_overlay_report("BEGIN")', prepare)
        telemetry_ready = self.wrapper.index(
            "python_overlay_telemetry_start()", prepare
        )
        run = self.wrapper.index("ret = py_bytesmain(argc, argv)", begin)
        end = self.wrapper.index('python_overlay_report("END")', run)
        self.assertLess(prepare, run)
        self.assertLess(telemetry_ready, begin)
        self.assertLess(begin, run)
        self.assertLess(run, end)

        no_site = self.wrapper.index("py_p2_set_default_no_site();", begin)
        self.assertLess(begin, no_site)
        self.assertLess(no_site, run)
        self.assertNotIn("argv[", self.wrapper[no_site:run])

        self.assertIn("nxsem_tickwait_uninterruptible", self.launcher)
        self.assertIn("nxsem_wait_uninterruptible", self.launcher)
        self.assertIn("g_python_overlay_telemetry_ready", self.launcher)
        self.assertIn('python_overlay_report("SAMPLE")', self.launcher)
        self.assertIn('python_overlay_report("FINAL")', self.launcher)

    def test_cpython_patch_changes_invalidate_source_and_target_configure(self):
        self.assertIn(
            "CPYTHON_PATCHES = $(wildcard patch$(DELIM)*.patch)",
            self.makefile,
        )
        self.assertIn(
            "$(CPYTHON_PATCH_STAMP): $(CPYTHON_ZIP) $(CPYTHON_PATCHES)",
            self.makefile,
        )
        self.assertIn("$(HOSTPYTHON): $(CPYTHON_PATCH_STAMP)", self.makefile)
        self.assertIn("context:: $(CPYTHON_PATCH_STAMP)", self.makefile)
        self.assertIn("touch $@", self.makefile)
        configure_start = self.makefile.index("$(TARGETBUILD)/Makefile:")
        configure_end = self.makefile.index("BUNDLED_WHEELS_DIR", configure_start)
        configure_rule = self.makefile[configure_start:configure_end]
        self.assertIn("$(CPYTHON_PATCH_STAMP)", configure_rule)
        self.assertIn(
            "0023-p2-co-locate-cpython-startup-overlays.patch",
            self.makefile,
        )
        previous = self.makefile.index(
            "0032-p2-co-locate-unicode-deallocation-with-type-init.patch"
        )
        hotpath = self.makefile.index(
            "0033-p2-co-locate-dictionary-hot-paths-with-type-init.patch"
        )
        module_attributes = self.makefile.index(
            "0034-p2-co-locate-module-attribute-startup-loop.patch"
        )
        gc_traversal = self.makefile.index(
            "0035-p2-co-locate-gc-traversal-working-set.patch"
        )
        self.assertLess(previous, hotpath)
        self.assertLess(hotpath, module_attributes)
        self.assertLess(module_attributes, gc_traversal)

    def test_dictionary_hotpath_patch_matches_measured_overlay_contract(self):
        added = "\n".join(
            line[1:]
            for line in self.dictionary_hotpath_patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn(
            "Post-link residency verification enforces that the final group fits the",
            self.dictionary_hotpath_patch,
        )
        self.assertIn("90,112-byte slot.", self.dictionary_hotpath_patch)
        self.assertNotIn(r"\n\n", self.dictionary_hotpath_patch)
        self.assertIn(
            "slot.\n\nKeep PyObject_IS_GC",
            self.dictionary_hotpath_patch,
        )
        self.assertIn(
            "PyAPI_FUNC(void) _Py_NewReference(PyObject *op) "
            "_Py_P2_HUB_RESIDENT;",
            added,
        )
        self.assertIn(
            "PyAPI_FUNC(int) PyObject_IS_GC(PyObject *obj) "
            "_Py_P2_HUB_RESIDENT;",
            added,
        )
        self.assertNotIn(
            "_Py_NewReferenceNoTotal(PyObject *op) _Py_P2_HUB_RESIDENT",
            added,
        )
        self.assertEqual(
            added.count("#if defined(__NuttX__) && defined(__propeller2__)"),
            2,
        )
        self.assertIn(
            "extern uint64_t\n"
            "_PyDict_NotifyEvent(PyInterpreterState *interp,",
            added,
        )
        self.assertIn(
            "_Py_P2_INIT_OVERLAY uint64_t\n"
            "_PyDict_NotifyEvent(PyInterpreterState *interp,",
            added,
        )
        self.assertEqual(added.count("_PyDict_NotifyEvent("), 2)
        self.assertNotIn("_Py_P2_INIT_OVERLAY static inline", added)
        for function in (
            "dict_merge",
            "_PyDict_NotifyEvent",
            "PyDict_GetItemRef",
            "_PyDict_GetItemRef_KnownHash",
            "PyDict_Pop",
            "_PyDict_Pop_KnownHash",
            "_PyObject_MaterializeManagedDict_LockHeld",
            "make_dict_from_instance_attributes",
            "PyDict_GetItemWithError",
            "PyDict_MergeFromSeq2",
            "PyObject_GenericGetDict",
            "new_dict_with_shared_keys",
            "_PyDictKeys_StringLookup",
            "_PyDict_DetachFromObject",
            "_PyDict_FromKeys",
            "_PyDict_Pop",
            "_PyDict_SetItem_KnownHash_LockHeld",
            "_PyDict_SizeOf",
            "_PyObject_SetManagedDict",
            "PyDict_AddWatcher",
            "PyDict_ClearWatcher",
            "PyDict_ContainsString",
            "PyDict_DelItemString",
            "PyDict_GetItem",
            "dict_getitem",
            "PyDict_GetItemString",
            "PyDict_GetItemStringRef",
            "PyDict_Items",
            "PyDict_Keys",
            "PyDict_Merge",
            "PyDict_Next",
            "PyDict_PopString",
            "PyDict_SetDefault",
            "PyDict_SetItemString",
            "PyDict_Size",
            "PyDict_Unwatch",
            "PyDict_Update",
            "PyDict_Values",
            "PyDict_Watch",
            "PyObject_ClearManagedDict",
            "PyObject_VisitManagedDict",
            "_PyDictKeys_DecRef",
            "_PyDictKeys_GetVersionForCurrentState",
        ):
            with self.subTest(function=function):
                self.assertIn(function, self.dictionary_hotpath_patch)

    def test_module_attribute_patch_matches_measured_overlay_contract(self):
        patch = self.module_attribute_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        patched = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        )

        measured_sizes = {
            "_add_methods_to_object": 320,
            "PyObject_SetAttrString": 248,
            "PyObject_SetAttr": 1276,
            "PyObject_GenericSetAttr": 20,
            "_PyObject_GenericSetAttrWithDict": 1676,
            "_PyObjectDict_SetItem": 244,
            "_PyDict_SetItem_LockHeld": 160,
            "_PyType_LookupRef": 644,
            "assign_version_tag": 412,
            "find_name_in_mro": 836,
        }
        self.assertEqual(sum(measured_sizes.values()), 5836)
        self.assertIn("stub 0x39c", patch)
        self.assertIn("The measured\nlinked bodies total 5,836 bytes", patch)
        self.assertIn("predicted result is 89,816 bytes", patch)
        self.assertIn("90,112-byte slot, leaving 296", patch)
        self.assertIn(
            "post-link residency verifier remains the hard authority", patch
        )
        self.assertEqual(added.count("_Py_P2_INIT_OVERLAY"), 18)
        self.assertNotIn("p2_hub_resident", patch)
        self.assertNotIn("pragma clang attribute", patch)

        definitions = {
            "_add_methods_to_object": "_Py_P2_INIT_OVERLAY static int\n",
            "PyObject_SetAttrString": "_Py_P2_INIT_OVERLAY int\n",
            "PyObject_SetAttr": "_Py_P2_INIT_OVERLAY int\n",
            "PyObject_GenericSetAttr": "_Py_P2_INIT_OVERLAY int\n",
            "_PyObject_GenericSetAttrWithDict": "_Py_P2_INIT_OVERLAY int\n",
            "_PyObjectDict_SetItem": "_Py_P2_INIT_OVERLAY int\n",
            "_PyDict_SetItem_LockHeld": "_Py_P2_INIT_OVERLAY int\n",
            "_PyType_LookupRef": "_Py_P2_INIT_OVERLAY PyObject *\n",
            "assign_version_tag": "_Py_P2_INIT_OVERLAY static int\n",
            "find_name_in_mro": "_Py_P2_INIT_OVERLAY static PyObject *\n",
        }
        for function, prefix in definitions.items():
            with self.subTest(function=function):
                self.assertIn(prefix + function + "(", patched)

        self.assertIn(
            "_Py_P2_INIT_OVERLAY static int assign_version_tag("
            "PyInterpreterState *interp, PyTypeObject *type);",
            added,
        )
        for declaration in (
            "_PyType_LookupRef(PyTypeObject *, PyObject *)\n"
            "    _Py_P2_INIT_OVERLAY;",
            "_PyObject_GenericSetAttrWithDict(PyObject *, PyObject *,\n"
            "                                 PyObject *, PyObject *)\n"
            "    _Py_P2_INIT_OVERLAY;",
            "_PyDict_SetItem_LockHeld(PyDictObject *dict, PyObject *name,\n"
            "                                    PyObject *value) "
            "_Py_P2_INIT_OVERLAY;",
            "_PyObjectDict_SetItem(PyTypeObject *tp, PyObject *obj,\n"
            "                                 PyObject **dictptr, "
            "PyObject *name,\n"
            "                                 PyObject *value) "
            "_Py_P2_INIT_OVERLAY;",
            "PyObject_SetAttrString(PyObject *, const char *, PyObject *) "
            "_Py_P2_INIT_OVERLAY;",
            "PyObject_SetAttr(PyObject *, PyObject *, PyObject *) "
            "_Py_P2_INIT_OVERLAY;",
            "PyObject_GenericSetAttr(PyObject *, PyObject *, PyObject *) "
            "_Py_P2_INIT_OVERLAY;",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, patched)

    def test_gc_traversal_patch_matches_measured_overlay_contract(self):
        patch = self.gc_traversal_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        patched = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        )

        measured_sizes = {
            "visit_decref": 136,
            "visit_reachable": 344,
            "visit_move": 296,
            "type_is_gc": 36,
            "dict_traverse": 548,
            "list_traverse": 156,
            "tupletraverse": 148,
            "set_traverse": 208,
            "type_traverse": 416,
            "subtype_traverse": 524,
            "module_traverse": 180,
            "descr_traverse": 72,
            "meth_traverse": 184,
            "gc_traverse": 72,
        }
        self.assertEqual(sum(measured_sizes.values()), 3320)
        self.assertEqual(4 + sum(measured_sizes.values()), 3324)
        self.assertEqual(90112 - 3324, 86788)
        self.assertIn("44,324 loads in 928.4 seconds", patch)
        self.assertIn("47.74 loads/second", patch)
        self.assertIn("exact linked bodies measured before this\nchange total 3,320", patch)
        self.assertIn("group 8 is\nprojected to occupy 3,324", patch)
        self.assertIn("leaving 86,788 bytes", patch)
        self.assertIn(
            "post-link residency verifier remains the hard authority", patch
        )

        self.assertIn(
            "#  define _Py_P2_GC_OVERLAY "
            "__attribute__((p2_hub_overlay(8)))",
            added,
        )
        self.assertIn("#  define _Py_P2_GC_OVERLAY", added)
        self.assertEqual(added.count("p2_hub_overlay(8)"), 1)
        self.assertEqual(added.count("_Py_P2_GC_OVERLAY static int"), 14)
        self.assertEqual(added.count("_Py_P2_GC_OVERLAY"), 16)
        self.assertNotIn("_Py_P2_INIT_OVERLAY", added)
        self.assertNotIn("p2_hub_resident", added)
        self.assertNotIn("pragma clang attribute", added)

        for function in measured_sizes:
            with self.subTest(function=function):
                self.assertIn(
                    "_Py_P2_GC_OVERLAY static int\n" + function + "(",
                    patched,
                )

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

    def test_threadless_importlib_fallback_is_narrow_and_uses_dummy_lock(self):
        patch = self.threadless_importlib_patch
        previous = self.makefile.index(
            "0039-p2-trace-importlib-bootstrap-steps.patch"
        )
        fallback = self.makefile.index(
            "0040-allow-importlib-without-thread-module.patch"
        )
        self.assertLess(previous, fallback)

        self.assertIn(
            "for builtin_name in ('_thread', '_warnings', '_weakref'):",
            patch,
        )
        self.assertEqual(patch.count("builtin_name == '_thread'"), 1)
        self.assertIn(
            "BuiltinImporter.find_spec(builtin_name) is None", patch
        )
        self.assertIn("+            continue", patch)
        self.assertNotIn("except ImportError", patch)
        native_check = patch.index("builtin_name == '_thread'")
        module_cache_check = patch.index("if builtin_name not in sys.modules:")
        self.assertLess(native_check, module_cache_check)
        self.assertIn("path module preloaded under this name", patch)
        self.assertIn("if _thread is None:", patch)
        self.assertIn("lock = _DummyModuleLock(name)", patch)
        self.assertIn("lock = _ModuleLock(name)", patch)

    def test_lock_only_thread_shim_is_zip_only_and_never_starts_callbacks(self):
        patch = self.lock_only_thread_patch
        previous = self.makefile.index(
            "0040-allow-importlib-without-thread-module.patch"
        )
        shim = self.makefile.index(
            "0041-add-lock-only-thread-compatibility.patch"
        )
        self.assertLess(previous, shim)

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            [
                "Lib/_thread.py",
                "Lib/threading.py",
                "Tools/wasm/wasm_assets.py",
            ],
        )
        for native_surface in (
            "PyInit__thread",
            "_threadmodule.c",
            "p2_hub_resident",
            "p2_hub_overlay",
            ".p2.overlay.stubs",
            "__p2_ovlbody",
        ):
            self.assertNotIn(native_surface, patch)

        source = added_file_source(patch, "Lib/_thread.py")
        self.assertNotRegex(
            source, re.compile(r"^\s*(?:from|import)\s", re.MULTILINE)
        )
        namespace = {"__name__": "_thread"}
        exec(compile(source, "Lib/_thread.py", "exec"), namespace)
        self.assertIs(namespace["error"], RuntimeError)
        self.assertIs(namespace["_NUTTX_LOCK_ONLY"], True)
        self.assertEqual(namespace["get_ident"](), namespace["get_ident"]())
        self.assertNotEqual(namespace["get_ident"](), 0)

        lock = namespace["allocate_lock"]()
        self.assertIs(type(lock), namespace["LockType"])
        self.assertTrue(lock.acquire())
        self.assertFalse(lock.acquire(False))
        with self.assertRaisesRegex(RuntimeError, "only Python task"):
            lock.acquire()
        lock.release()
        with self.assertRaisesRegex(RuntimeError, "release unlocked lock"):
            lock.release()

        rlock = namespace["RLock"]()
        self.assertTrue(rlock.acquire())
        self.assertTrue(rlock.acquire(False))
        self.assertEqual(rlock._recursion_count(), 2)
        rlock.release()
        rlock.release()
        self.assertFalse(rlock.locked())

        called = []
        callback = lambda: called.append(True)
        starters = (
            (namespace["start_new_thread"], (callback, ())),
            (namespace["start_new"], (callback, ())),
            (namespace["start_joinable_thread"], (callback,)),
        )
        for starter, args in starters:
            with self.subTest(starter=starter.__name__):
                with self.assertRaisesRegex(
                    NotImplementedError, "supports one Python task"
                ):
                    starter(*args)
        self.assertEqual(called, [])

        expected_gate = (
            "threading is unavailable: this NuttX P2 profile supports one "
            "Python task and only lock-only _thread compatibility"
        )
        self.assertIn('if getattr(_thread, "_NUTTX_LOCK_ONLY", False):', patch)
        self.assertIn('raise ImportError(', patch)
        self.assertIn(expected_gate, patch.replace('"\n+        "', ""))
        self.assertIn('-    "_pyio.py",', patch)

    def test_lock_only_thread_packaging_rejects_native_or_uncompressed_forms(self):
        wrapper = self.build_wrapper
        self.assertIn(
            '("encodings/__init__.pyc", "_thread.pyc", "_pyio.pyc")',
            wrapper,
        )
        self.assertIn("info.compress_type != zipfile.ZIP_DEFLATED", wrapper)
        self.assertIn('if "_thread.py" in archive.namelist():', wrapper)
        self.assertIn('"$P2LLVM_ROOT/bin/llvm-ar" t "$python_archive"', wrapper)
        self.assertIn("_threadmodule\\.o", wrapper)
        self.assertIn("unexpectedly archives native _threadmodule.o", wrapper)
        self.assertIn("PyInit__thread", wrapper)
        self.assertIn("unexpectedly links _thread", wrapper)

    def test_combined_lock_only_hil_program_runs_against_the_pure_shim(self):
        # Run the exact target program in a clean host process.  The driver
        # substitutes the pure module for the host builtin and models the two
        # target-only facts (no builtin registration and the zipimport origin).
        hil_tree = ast.parse(
            PYTHON_HIL_RUNNER.read_text(encoding="utf-8"),
            filename=str(PYTHON_HIL_RUNNER),
        )
        script_assignment = next(
            node for node in hil_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "LOCK_ONLY_TEST_SCRIPT"
                for target in node.targets
            )
        )
        program = "\n".join(ast.literal_eval(script_assignment.value)) + "\n"
        shim = added_file_source(self.lock_only_thread_patch, "Lib/_thread.py")
        threading_gate = '''\
import _thread

if getattr(_thread, "_NUTTX_LOCK_ONLY", False):
    raise ImportError(
        "threading is unavailable: this NuttX P2 profile supports one "
        "Python task and only lock-only _thread compatibility"
    )

raise AssertionError("lock-only threading gate did not run")
'''
        driver = '''\
import _imp
import importlib._bootstrap as bootstrap
import importlib.util
import pathlib
import sys

root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("_thread", root / "_thread.py")
module = importlib.util.module_from_spec(spec)
sys.modules["_thread"] = module
spec.loader.exec_module(module)
module.__spec__.origin = "/usr/local/lib/python313.zip/_thread.pyc"
bootstrap._thread = None
real_is_builtin = _imp.is_builtin
_imp.is_builtin = lambda name: 0 if name == "_thread" else real_is_builtin(name)
for name in ("functools", "_pyio", "_strptime", "reprlib", "tempfile", "threading"):
    sys.modules.pop(name, None)
sys.path.insert(0, str(root))
exec(compile((root / "lock_only_test.py").read_bytes(),
             "lock_only_test.py", "exec"))
'''

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "_thread.py").write_text(shim, encoding="utf-8")
            (root / "threading.py").write_text(threading_gate, encoding="utf-8")
            (root / "lock_only_test.py").write_text(program, encoding="utf-8")
            (root / "driver.py").write_text(driver, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-S", str(root / "driver.py")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": "/tmp",
                },
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), "P2PYTEST:LOCK_ONLY:PASS")

    def test_importlib_startup_patches_are_adjacent_and_apply_in_order(self):
        patch_commands = re.findall(
            r"< patch\$\(DELIM\)(\d{4}-[^\s]+\.patch)", self.makefile
        )
        expected_tail = [
            "0040-allow-importlib-without-thread-module.patch",
            "0041-add-lock-only-thread-compatibility.patch",
            "0042-p2-co-locate-importlib-startup-hot-paths.patch",
            "0043-p2-trace-main-interpreter-initialization.patch",
            "0044-p2-trace-fill-time-softfloat-boundaries.patch",
            "0045-p2-add-default-no-site-config-hook.patch",
            "0046-p2-add-fixed-path-config-fast-path.patch",
            "0047-p2-inline-static-unicode-compare-hot-edge.patch",
            "0048-p2-inline-exact-tuple-iterator-hot-edge.patch",
            "0049-p2-trace-main-interpreter-subphases.patch",
            "0050-p2-inline-marshal-reference-list-append.patch",
            "0051-p2-use-utf8-decoder-for-marshal-ascii.patch",
            "0052-p2-pin-posix-constdef-comparator.patch",
            "0053-p2-freeze-startup-encodings.patch",
            "0054-p2-record-frozen-encoding-pcbuild.patch",
        ]
        start = patch_commands.index(expected_tail[0])
        self.assertEqual(
            patch_commands[start : start + len(expected_tail)], expected_tail
        )
        pcbuild_patch = FROZEN_STARTUP_ENCODINGS_PCBUILD_PATCH.read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            re.findall(
                r"^diff --git a/(PCbuild/\S+) b/\S+$",
                pcbuild_patch,
                re.MULTILINE,
            ),
            [
                "PCbuild/_freeze_module.vcxproj",
                "PCbuild/_freeze_module.vcxproj.filters",
            ],
        )
        for module in ("encodings", "encodings.aliases", "encodings.utf_8"):
            self.assertIn(f"<ModName>{module}</ModName>", pcbuild_patch)

        patch_paths = (
            IMPORTLIB_STARTUP_HOTPATH_PATCH,
            MAIN_INTERPRETER_TRACE_PATCH,
            FILL_TIME_SOFTFLOAT_TRACE_PATCH,
            DEFAULT_NO_SITE_PATCH,
            FIXED_PATH_CONFIG_PATCH,
            STATIC_UNICODE_HOT_EDGE_PATCH,
            TUPLE_ITERATOR_HOT_EDGE_PATCH,
            MAIN_INTERPRETER_SUBPHASE_TRACE_PATCH,
            MARSHAL_LIST_APPEND_HOT_EDGE_PATCH,
            MARSHAL_ASCII_HOT_EDGE_PATCH,
            POSIX_CONSTDEF_RESIDENCY_PATCH,
            FROZEN_STARTUP_ENCODINGS_PATCH,
        )
        with tempfile.TemporaryDirectory() as temporary:
            fixture = pathlib.Path(temporary)
            materialize_patch_preimages(fixture, patch_paths)

            for patch_path in patch_paths:
                with self.subTest(patch=patch_path.name, operation="dry-run"):
                    dry_run = subprocess.run(
                        [
                            "patch",
                            "-p1",
                            "--batch",
                            "--dry-run",
                            "-i",
                            str(patch_path),
                        ],
                        cwd=fixture,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(dry_run.returncode, 0, dry_run.stdout)

                with self.subTest(patch=patch_path.name, operation="apply"):
                    apply = subprocess.run(
                        [
                            "patch",
                            "-p1",
                            "--batch",
                            "-i",
                            str(patch_path),
                        ],
                        cwd=fixture,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(apply.returncode, 0, apply.stdout)

            self.assertFalse(list(fixture.rglob("*.rej")))
            self.assertIn(
                "#  define _Py_P2_COMPARE_OVERLAY "
                "__attribute__((p2_hub_overlay(10)))",
                (fixture / "Include/pyport.h").read_text(encoding="utf-8"),
            )
            lifecycle = (fixture / "Python/pylifecycle.c").read_text(
                encoding="utf-8"
            )
            self.assertIn('printf("P2PY:MAIN:PASS\\n");', lifecycle)
            self.assertIn('printf("P2PY:MAINSTEP:BEGIN\\n");', lifecycle)
            self.assertIn(
                'printf("P2PY:MAINSTEP:MAIN_MODULE:PASS\\n");',
                lifecycle,
            )
            self.assertIn(
                'p2_fill_time_puts("P2PY:FILLTIME:PYFLOAT:BEGIN\\r\\n");',
                (fixture / "Modules/posixmodule.c").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "p2_marshal_is_ascii(ptr, n)",
                (fixture / "Python/marshal.c").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "_Py_P2_HUB_RESIDENT\nstatic int\ncmp_constdefs",
                (fixture / "Modules/posixmodule.c").read_text(encoding="utf-8"),
            )
            frozen = (fixture / "Python/frozen.c").read_text(encoding="utf-8")
            for module in ("encodings", "encodings.aliases", "encodings.utf_8"):
                symbol = module.replace(".", "_")
                self.assertIn(f'_Py_M__{symbol}', frozen)
            self.assertIn(
                "config.site_import = 0;",
                (fixture / "Modules/main.c").read_text(encoding="utf-8"),
            )
            getpath = (fixture / "Modules/getpath.c").read_text(encoding="utf-8")
            self.assertIn("p2_init_fixed_path_config", getpath)
            self.assertIn('printf("P2PY:PATHCONFIG:PASS\\n");', getpath)
            unicode_source = (fixture / "Objects/unicodeobject.c").read_text(
                encoding="utf-8"
            )
            self.assertIn("p2_unicode_compare_eq_inline", unicode_source)
            self.assertIn(
                "static inline Py_ALWAYS_INLINE int", unicode_source
            )
            self.assertIn(
                "bytes = (size_t)len << (kind >> 1);", unicode_source
            )
            self.assertIn(
                "_Py_P2_COMPARE_OVERLAY static int\n"
                "unicode_compare_eq(PyObject *str1, PyObject *str2)",
                unicode_source,
            )
            tuple_header = (
                fixture / "Include/internal/pycore_tuple.h"
            ).read_text(encoding="utf-8")
            abstract_source = (fixture / "Objects/abstract.c").read_text(
                encoding="utf-8"
            )
            tuple_source = (fixture / "Objects/tupleobject.c").read_text(
                encoding="utf-8"
            )
            self.assertIn("_PyP2_TupleIterNextInline", tuple_header)
            self.assertIn(
                "Py_IS_TYPE(iter, &PyTupleIter_Type)", abstract_source
            )
            self.assertIn(
                "result = (*Py_TYPE(iter)->tp_iternext)(iter);",
                abstract_source,
            )
            self.assertIn(
                "return _PyP2_TupleIterNextInline(it);", tuple_source
            )
            marshal_source = (fixture / "Python/marshal.c").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                marshal_source.count(
                    "#if defined(__NuttX__) && defined(__propeller2__)"
                ),
                5,
            )
            self.assertIn(
                '#  include "pycore_list.h"', marshal_source
            )
            self.assertEqual(
                marshal_source.count("err = _PyList_AppendTakeRef("), 2
            )
            self.assertEqual(
                marshal_source.count("Py_BEGIN_CRITICAL_SECTION(p->refs);"),
                2,
            )
            self.assertEqual(
                marshal_source.count("Py_END_CRITICAL_SECTION();"), 2
            )
            self.assertIn(
                "#else\n"
                "        if (PyList_Append(p->refs, Py_None) < 0)\n"
                "#endif",
                marshal_source,
            )
            self.assertIn(
                "#else\n"
                "    if (PyList_Append(p->refs, o) < 0) {\n"
                "#endif",
                marshal_source,
            )
            self.assertIn(
                "Py_DECREF(o); /* release the new object */", marshal_source
            )

    def test_static_unicode_hot_edge_is_inlined_without_moving_rich_compare(self):
        patch = self.static_unicode_hot_edge_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("at least 1,553 calls", patch)
        self.assertIn("89,840-byte type-initialization group", patch)
        self.assertIn("static inline Py_ALWAYS_INLINE int", added)
        self.assertIn("p2_unicode_compare_eq_inline", added)
        self.assertEqual(
            added.count("return p2_unicode_compare_eq_inline"), 2
        )
        self.assertIn("bytes = (size_t)len << (kind >> 1);", added)
        self.assertNotIn("len * kind", added)
        self.assertNotIn("PyObject_ClearManagedDict", patch)
        self.assertEqual(
            added.count("#if defined(__NuttX__) && defined(__propeller2__)"),
            3,
        )
        self.assertNotIn("_Py_P2_HUB_RESIDENT", added)
        self.assertNotIn(
            "_Py_P2_COMPARE_OVERLAY static int unicode_compare_eq",
            "\n".join(
                line[1:]
                for line in patch.splitlines()
                if line.startswith("-") and not line.startswith("---")
            ),
        )

    def test_exact_tuple_iterator_hot_edge_preserves_generic_dispatch(self):
        patch = self.tuple_iterator_hot_edge_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line != "-- "
        )

        self.assertIn("at least 290 certain transitions", patch)
        self.assertIn("at least 12,821,480 overlay bytes", patch)
        self.assertIn("static inline Py_ALWAYS_INLINE PyObject *", added)
        self.assertEqual(added.count("_PyP2_TupleIterNextInline("), 3)
        self.assertIn("Py_IS_TYPE(iter, &PyTupleIter_Type)", added)
        self.assertIn(
            "result = (*Py_TYPE(iter)->tp_iternext)(iter);", added
        )
        self.assertIn("result = _PyP2_TupleIterNextInline", added)
        self.assertIn("return _PyP2_TupleIterNextInline(it);", added)
        self.assertIn("item = PyTuple_GET_ITEM(seq, it->it_index);", added)
        self.assertIn("++it->it_index;", added)
        self.assertIn("it->it_seq = NULL;", added)
        self.assertIn("Py_DECREF(seq);", added)
        self.assertNotIn("_PyErr_Clear", removed)
        self.assertNotIn("p2_hub_overlay(177)", patch)
        self.assertNotIn("_Py_P2_HUB_RESIDENT", added)

    def test_marshal_list_append_fast_path_preserves_reference_ownership(self):
        patch = self.marshal_list_append_hot_edge_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line not in ("-- ", "--")
        ]

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            ["Python/marshal.c"],
        )
        self.assertEqual(removed, [])
        self.assertIn("at least 468 transitions", patch)
        self.assertIn("at least 25,127,856 overlay bytes", patch)
        self.assertIn("from 0x5468 to 0x5584 (284 bytes)", patch)
        self.assertIn("68,220 bytes of slot headroom", patch)
        self.assertIn("growth-boundary appends can still cross overlays", patch)

        guard = "#if defined(__NuttX__) && defined(__propeller2__)"
        self.assertEqual(added.count(guard), 3)
        self.assertEqual(added.count("#endif"), 3)
        self.assertEqual(added.count("#else"), 2)
        self.assertEqual(added.count('include "pycore_list.h"'), 1)
        self.assertEqual(added.count("err = _PyList_AppendTakeRef("), 2)
        self.assertEqual(
            added.count("Py_BEGIN_CRITICAL_SECTION(p->refs);"), 2
        )
        self.assertEqual(added.count("Py_END_CRITICAL_SECTION();"), 2)
        self.assertEqual(added.count("if (err < 0)"), 2)
        self.assertEqual(added.count("Py_NewRef(Py_None)"), 1)
        self.assertEqual(added.count("Py_NewRef(o)"), 1)
        self.assertNotIn("_PyList_AppendTakeRefListResize(", added)
        self.assertNotIn("Py_DECREF(Py_None)", added)
        self.assertNotIn("p2_hub_overlay", added)
        self.assertNotIn("_Py_P2_HUB_RESIDENT", added)

        reserve_start = patch.index("@@ -953")
        direct_start = patch.index("@@ -992", reserve_start)

        def patched_hunk(text: str) -> str:
            return "\n".join(
                line[1:]
                for line in text.splitlines()
                if line.startswith((" ", "+"))
                and not line.startswith("+++")
            )

        reserve = patched_hunk(patch[reserve_start:direct_start])
        direct = patched_hunk(patch[direct_start:])
        self.assertIn(
            "#else\n"
            "        if (PyList_Append(p->refs, Py_None) < 0)\n"
            "#endif",
            reserve,
        )
        self.assertIn(
            "#else\n"
            "    if (PyList_Append(p->refs, o) < 0) {\n"
            "#endif",
            direct,
        )
        self.assertIn("Py_DECREF(o); /* release the new object */", direct)

        for block, new_ref in (
            (reserve, "Py_NewRef(Py_None)"),
            (direct, "Py_NewRef(o)"),
        ):
            with self.subTest(new_ref=new_ref):
                begin = block.index("Py_BEGIN_CRITICAL_SECTION(p->refs);")
                take = block.index("_PyList_AppendTakeRef(", begin)
                reference = block.index(new_ref, take)
                end = block.index("Py_END_CRITICAL_SECTION();", reference)
                branch = block.index("if (err < 0)", end)
                self.assertLess(begin, take)
                self.assertLess(take, reference)
                self.assertLess(reference, end)
                self.assertLess(end, branch)
                self.assertNotIn("return ", block[begin:end])
                self.assertNotIn("Py_DECREF(o)", block[begin:end])

    def test_default_no_site_hook_is_p2_only_and_sets_pyconfig(self) -> None:
        patch = self.default_no_site_patch
        removed = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line != "-- "
        ]
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertEqual(removed, [])
        self.assertIn("_Py_P2_HUB_RESIDENT void", added)
        self.assertIn("py_p2_set_default_no_site(void)", added)
        self.assertIn('section(".p2.hub.data")', added)
        self.assertIn("config.site_import = 0;", added)
        self.assertNotIn("Py_NoSiteFlag", added)
        self.assertNotIn('"-S"', added)
        self.assertNotIn("argv", added)

        guard = "#if defined(__NuttX__) && defined(__propeller2__)"
        depth = 0
        for line in added.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == guard:
                depth += 1
            elif stripped == "#endif":
                depth -= 1
            else:
                self.assertGreater(depth, 0, stripped)
        self.assertEqual(depth, 0)

    def test_fixed_path_config_bypasses_frozen_getpath_only_when_enabled(
        self,
    ) -> None:
        patch = self.fixed_path_config_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        guard = (
            "defined(CONFIG_INTERPRETERS_CPYTHON_P2_FIXED_PATH_CONFIG)"
        )
        self.assertIn("#if defined(__NuttX__) && defined(__propeller2__)", added)
        self.assertIn(guard, added)
        self.assertIn("py_p2_set_fixed_path_config", added)
        self.assertIn("p2_init_fixed_path_config", added)
        self.assertIn("config->module_search_paths_set = 1;", added)
        self.assertIn('L"/usr/local/lib/python"', added)
        self.assertIn("P2_STDLIB_ZIP", added)
        self.assertIn('printf("P2PY:PATHCONFIG:BEGIN\\n");', added)
        self.assertIn('printf("P2PY:PATHCONFIG:PASS\\n");', added)
        self.assertIn('printf("P2PY:PATHCONFIG:FAIL\\n");', added)
        fast = added.index("if (p2_fixed_pythonpath)")
        frozen = added.find("PyEval_EvalCode", fast)
        self.assertEqual(frozen, -1)

    def test_importlib_hotpath_patch_has_exact_overlay_membership_contract(self):
        patch = self.importlib_startup_hotpath_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line != "-- "
        )
        patched = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        )

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            [
                "Include/cpython/dictobject.h",
                "Include/dictobject.h",
                "Include/object.h",
                "Include/pyport.h",
                "Include/unicodeobject.h",
                "Objects/codeobject.c",
                "Objects/dictobject.c",
                "Objects/object.c",
                "Objects/typeobject.c",
                "Objects/unicodeobject.c",
            ],
        )
        self.assertIn("measured 89,836-byte group 7", patch)
        self.assertIn("89,840 bytes, leaving 272 bytes", patch)
        self.assertIn("2,364-byte upper bound for group 10", patch)
        self.assertIn(
            "post-link residency verifier\nremains the authority", patch
        )
        self.assertEqual(89836 - 1640 - 296 + 1940, 89840)
        self.assertEqual(90112 - 89840, 272)

        self.assertIn(
            "#  define _Py_P2_COMPARE_OVERLAY "
            "__attribute__((p2_hub_overlay(10)))",
            added,
        )
        self.assertEqual(
            [
                line
                for line in added.splitlines()
                if line.startswith("#  define _Py_P2_COMPARE_OVERLAY")
            ],
            [
                "#  define _Py_P2_COMPARE_OVERLAY "
                "__attribute__((p2_hub_overlay(10)))",
                "#  define _Py_P2_COMPARE_OVERLAY",
            ],
        )
        self.assertEqual(added.count("p2_hub_overlay(10)"), 1)
        self.assertEqual(added.count("_Py_P2_COMPARE_OVERLAY"), 10)
        self.assertEqual(added.count("_Py_P2_INIT_OVERLAY"), 2)

        for definition in (
            "_Py_P2_INIT_OVERLAY static int\nintern_constants(",
            "_Py_P2_INIT_OVERLAY static pytype_slotdef *\nupdate_one_slot(",
        ):
            with self.subTest(init_definition=definition):
                self.assertIn(definition, patched)

        for definition in (
            "_Py_P2_COMPARE_OVERLAY PyObject *\nPyObject_RichCompare(",
            "_Py_P2_COMPARE_OVERLAY int\nPyObject_RichCompareBool(",
            "_Py_P2_COMPARE_OVERLAY static int\nunicode_compare_eq(",
            "_Py_P2_COMPARE_OVERLAY PyObject *\nPyUnicode_RichCompare(",
        ):
            with self.subTest(compare_definition=definition):
                self.assertIn(definition, patched)

        evicted = (
            "PyDict_Items",
            "PyDict_Watch",
            "PyDict_Unwatch",
            "PyDict_AddWatcher",
            "PyDict_ClearWatcher",
        )
        for function in evicted:
            with self.subTest(evicted=function):
                self.assertEqual(
                    len(re.findall(rf"\b{function}\(", removed)), 1
                )
                self.assertEqual(
                    len(re.findall(rf"\b{function}\(", added)), 1
                )

        for return_type, function in (
            ("PyObject *", "PyDict_Items"),
            ("int", "PyDict_Watch"),
            ("int", "PyDict_Unwatch"),
            ("int", "PyDict_AddWatcher"),
            ("int", "PyDict_ClearWatcher"),
        ):
            with self.subTest(evicted_body=function):
                self.assertIn(
                    f"-_Py_P2_INIT_OVERLAY {return_type}\n"
                    f"+{return_type}\n {function}(",
                    patch,
                )

        # Five declarations and five bodies leave group 7.  The remaining
        # two removals move unicode_compare_eq's declaration and body to 10.
        self.assertEqual(removed.count("_Py_P2_INIT_OVERLAY"), 12)
        self.assertEqual(removed.count("unicode_compare_eq"), 1)
        self.assertEqual(added.count("unicode_compare_eq"), 1)
        self.assertIn(
            "return Py_NewRef(result ? Py_True : Py_False);", added
        )
        self.assertIn("return PyBool_FromLong(result);", removed)
        self.assertNotIn("p2_hub_resident", added)
        self.assertNotIn("pragma clang attribute", added)

    def test_main_interpreter_completion_marker_is_p2_only_and_compact(self):
        patch = self.main_interpreter_trace_patch
        added_lines = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        removed_lines = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line != "-- "
        ]
        added = "\n".join(added_lines)

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            ["Python/pylifecycle.c"],
        )
        self.assertEqual(removed_lines, [])
        self.assertEqual(
            added.count(
                "#if defined(__NuttX__) && defined(__propeller2__)"
            ),
            1,
        )
        self.assertEqual(added.count("#endif"), 1)

        expected_markers = ["P2PY:MAIN:PASS"]
        self.assertEqual(
            re.findall(r'printf\("(P2PY:MAIN:[A-Z_:]+)\\n"\);', added),
            expected_markers,
        )

        guard_depth = 0
        for line in added_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "#if defined(__NuttX__) && defined(__propeller2__)":
                guard_depth += 1
            elif stripped == "#endif":
                guard_depth -= 1
            else:
                self.assertRegex(stripped, r'^printf\("P2PY:MAIN:')
                self.assertEqual(guard_depth, 1)
        self.assertEqual(guard_depth, 0)

        self.assertNotIn("+        return status;", patch)
        self.assertNotIn("+        return _PyStatus", patch)

    def test_main_interpreter_subphase_trace_is_p2_only_and_ordered(self):
        patch = self.main_interpreter_subphase_trace_patch
        added_lines = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        removed_lines = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-")
            and not line.startswith("---")
            and line != "-- "
        ]
        added = "\n".join(added_lines)
        patched = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        )

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            ["Python/pylifecycle.c"],
        )
        self.assertEqual(removed_lines, [])

        expected_markers = [
            "P2PY:MAINSTEP:BEGIN",
            "P2PY:MAINSTEP:CONFIG:PASS",
            "P2PY:MAINSTEP:EXTERNAL:PASS",
            "P2PY:MAINSTEP:ENCODINGS:PASS",
            "P2PY:MAINSTEP:STREAMS:PASS",
            "P2PY:MAINSTEP:BUILTINS_OPEN:PASS",
            "P2PY:MAINSTEP:MAIN_MODULE:PASS",
        ]
        self.assertEqual(
            re.findall(r'printf\("(P2PY:MAINSTEP:[A-Z_:]+)\\n"\);', added),
            expected_markers,
        )
        self.assertEqual(added.count("P2PY:MAINSTEP:"), 7)
        self.assertNotIn("P2PY:MAIN:PASS", added)

        guard = "#if defined(__NuttX__) && defined(__propeller2__)"
        self.assertEqual(added.count(guard), len(expected_markers))
        self.assertEqual(added.count("#endif"), len(expected_markers))
        guard_depth = 0
        for line in added_lines:
            stripped = line.strip()
            if stripped == guard:
                guard_depth += 1
            elif stripped == "#endif":
                guard_depth -= 1
            else:
                self.assertEqual(guard_depth, 1, stripped)
                self.assertRegex(stripped, r'^printf\("P2PY:MAINSTEP:')
        self.assertEqual(guard_depth, 0)

        for call, marker in (
            (
                "interpreter_update_config(tstate, 1)",
                "P2PY:MAINSTEP:CONFIG:PASS",
            ),
            ("_PyImport_InitExternal(tstate)", "P2PY:MAINSTEP:EXTERNAL:PASS"),
            ("init_set_builtins_open()", "P2PY:MAINSTEP:BUILTINS_OPEN:PASS"),
        ):
            with self.subTest(marker=marker):
                self.assertLess(patched.index(call), patched.index(marker))

        self.assertNotIn("+        return status;", patch)
        self.assertNotIn("+        return _PyStatus", patch)

    def test_fill_time_trace_is_p2_only_and_preserves_the_host_expression(self):
        patch = self.fill_time_softfloat_trace_patch
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertEqual(
            re.findall(r"^diff --git a/(\S+) b/\S+$", patch, re.MULTILINE),
            ["Modules/posixmodule.c"],
        )
        self.assertIn("#if defined(__NuttX__) && defined(__propeller2__)", added)
        self.assertIn("_Static_assert(sizeof(time_t) == 2 * sizeof(uint32_t)", added)
        self.assertEqual(added.count("_Py_P2_HUB_RESIDENT static void"), 3)
        self.assertIn("up_putc((unsigned char)*text++);", added)
        self.assertNotIn("printf(", added)

        expected_markers = [
            "P2PY:FILLTIME:FLOATDIDF:BEGIN",
            "P2PY:FILLTIME:FLOATDIDF:PASS",
            "P2PY:FILLTIME:FLOATUNSIDF:BEGIN",
            "P2PY:FILLTIME:FLOATUNSIDF:PASS",
            "P2PY:FILLTIME:MULDF3:BEGIN",
            "P2PY:FILLTIME:MULDF3:PASS",
            "P2PY:FILLTIME:ADDDF3:BEGIN",
            "P2PY:FILLTIME:ADDDF3:PASS",
            "P2PY:FILLTIME:PYFLOAT:BEGIN",
            "P2PY:FILLTIME:PYFLOAT:PASS",
            "P2PY:FILLTIME:PYFLOAT:FAIL",
        ]
        for marker in expected_markers:
            with self.subTest(marker=marker):
                self.assertEqual(added.count(marker), 1)

        raw = patch.index("P2PY:FILLTIME:RAW:SECLO=")
        signed = patch.index("sec_as_double = (double)sec;", raw)
        unsigned = patch.index("nsec_as_double = (double)nsec;", signed)
        multiply = patch.index("nsec_scaled = 1e-9 * nsec_as_double;", unsigned)
        add = patch.index("time_as_double = sec_as_double + nsec_scaled;", multiply)
        boxed = patch.index("float_s = PyFloat_FromDouble(time_as_double);", add)
        self.assertLess(raw, signed)
        self.assertLess(signed, unsigned)
        self.assertLess(unsigned, multiply)
        self.assertLess(multiply, add)
        self.assertLess(add, boxed)
        self.assertIn(" float_s = PyFloat_FromDouble(sec + 1e-9*nsec);", patch)

    def test_hil_filesystem_and_uart_window_are_build_contracts(self) -> None:
        for setting in (
            "CONFIG_FS_TMPFS=y",
            "CONFIG_P2_HUB_OVERLAY_ZLIB=y",
            "CONFIG_INTERPRETERS_CPYTHON_ROMFS_SECTORSIZE=512",
            "CONFIG_INTERPRETERS_CPYTHON_P2_DEFAULT_NO_SITE=y",
            "CONFIG_INTERPRETERS_CPYTHON_P2_FIXED_PATH_CONFIG=y",
            "CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY=y",
            "CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY_INTERVAL_MS=60000",
            'CONFIG_INTERPRETERS_CPYTHON_PYTHONPATH="/tmp"',
            "CONFIG_P2_UART_RX_RING_SIZE=1024",
            "CONFIG_UART0_BAUD=2000000",
            "CONFIG_UART0_RXBUFSIZE=1280",
            "# CONFIG_NSH_DISABLE_ECHO is not set",
            "CONFIG_NSH_DISABLE_HELP=y",
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
