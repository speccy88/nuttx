#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Verify that the P2 Python control and diagnostic path stays resident.

P2LLVM represents an overlaid function with a small resident public stub and
an execution-slot body named ``__p2_ovlbody.<group>.<function>``.  Checking
only the public symbol type would therefore accept exactly the broken layout
this verifier is intended to prevent: ``nm`` reports an executable veneer as
``T`` too.  This audit reads the unfiltered ``nuttx.full`` ELF, recognizes
compiler clones and overlay-body names, requires the exact CPython startup
implementations, and rejects every required symbol whose address or range
intersects the linker-published overlay-stub interval or whose size is no
larger than a four-byte P2LLVM veneer.  The UTF-8 decoder audit separately
requires the two measured out-of-line decoder bodies in one explicit overlay
group.  Three template-local variants may disappear completely when Clang
inlines them; if any survives, the audit requires its complete stub/body pair
in that same group.  This prevents the measured decode/restore ping-pong from
returning without forcing a new transition on the common inline path.  The
type-initialization audit requires all 23 successful-path type-ready
functions, 65 measured success-path helpers, six Unicode-bootstrap bodies,
the exact 38-body dictionary hot set, and the ten-function module-attribute
startup loop in one concrete group-7 section.  The immortal-intern wrapper
and its two refcount helpers plus the narrow code-name interning loop are part
of that measured closure, along with the two measured importlib locality
callers.  The rollback/finalization-only static-type clear helper is excluded.
Two measured cross-group leaves are instead required to be substantive
Hub-resident implementations.  The audit also rejects linked pylifecycle entry
points or any unreviewed body in that group so the broad per-object overlay
cannot silently return.  A separate exact group-8 contract requires the three
cyclic-GC visitors, eleven
measured or closure-required built-in traversal helpers, and all 31 linked
bodies from the measured Python/gc.c collector group.  Group 9 is the exact
two-function frozen-code quickening loop.  Group 10 is the exact four-function
object/Unicode rich-comparison path measured during frozen-importlib startup.
The patch-0047 static-Unicode hot edge is checked separately: group 7 may use
at most 0x16000 bytes, ``hashtable_unicode_compare`` must remain in group 7,
``unicode_compare_eq`` must remain in group 10, and the callback machine code
must not retain a direct CALLA to the comparison overlay stub.
The patch-0048 and patch-0050 audits resolve their automatically assigned
caller and callee groups plus public veneer addresses from the linker map.
They read each caller's exact ELF symbol body, reject a remaining immediate
CALLA from ``PyIter_Next`` to ``tupleiter_next`` or from either marshal
reference helper to ``PyList_Append``, and require each marshal helper to keep
exactly one immediate CALLA to the list-resize slow path.
The selective startup-encoding audit separately requires the three frozen
marshal payloads for ``encodings``, ``encodings.aliases``, and
``encodings.utf_8`` to remain unique, nonempty data objects in one initialized,
allocatable, non-executable external-PSRAM output section.  The ELF output
merges const-designated input ``.p2.xdata.ro`` into unified ``.p2.xdata``, so
the linker map must also prove each object's input-section provenance.  The
merged output is intentionally writable; this audit establishes placement and
initialization, not hardware write protection.  Together with the HIL module-
origin check, it prevents generated freeze-table drift from silently restoring
the ROMFS startup path or consuming scarce Hub memory.
Every concrete section must fit the same execution slot and may not contain
any unreviewed body.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

try:
    from elftools.common.exceptions import ELFError
    from elftools.elf.constants import SH_FLAGS
    from elftools.elf.elffile import ELFFile
except ImportError as exc:  # pragma: no cover - broken host dependency
    raise SystemExit(
        "ERROR: verify-python-residency.py requires pyelftools"
    ) from exc


P2_ELF_MACHINE = 0x12C
SLOT_START_SYMBOL = "__p2_overlay_slot_start"
SLOT_END_SYMBOL = "__p2_overlay_slot_end"
OVERLAY_STUBS_START_SYMBOL = "__p2_overlay_stubs_start"
OVERLAY_STUBS_END_SYMBOL = "__p2_overlay_stubs_end"
OVERLAY_STUB_SECTION = ".p2.overlay.stubs"
P2_XMEM_START = 0x10000000
P2_XMEM_END = 0x12000000
P2_XDATA_OUTPUT_SECTION = ".p2.xdata"
FROZEN_STARTUP_DATA_INPUT_SECTION = ".p2.xdata.ro"
FROZEN_STARTUP_DATA_SYMBOLS: tuple[str, ...] = (
    "_Py_M__encodings",
    "_Py_M__encodings_aliases",
    "_Py_M__encodings_utf_8",
)
UTF8_OVERLAY_GROUP = 6
UTF8_OVERLAY_SECTION = ".p2.overlay.group.00000006"
UTF8_OVERLAY_REQUIRED_FUNCTIONS: tuple[str, ...] = (
    "_Py_DecodeUTF8Ex",
    "ucs4lib_utf8_decode",
)
UTF8_OVERLAY_IF_LINKED_FUNCTIONS: tuple[str, ...] = (
    "asciilib_utf8_decode",
    "ucs1lib_utf8_decode",
    "ucs2lib_utf8_decode",
)
UTF8_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    UTF8_OVERLAY_REQUIRED_FUNCTIONS[:1]
    + UTF8_OVERLAY_IF_LINKED_FUNCTIONS
    + UTF8_OVERLAY_REQUIRED_FUNCTIONS[1:]
)
TYPE_INIT_OVERLAY_GROUP = 7
TYPE_INIT_OVERLAY_SECTION = ".p2.overlay.group.00000007"
TYPE_INIT_OVERLAY_MAX_SIZE = 0x16000
STATIC_UNICODE_CALLBACK = "hashtable_unicode_compare"
TYPE_INIT_OVERLAY_CORE_FUNCTIONS: tuple[str, ...] = (
    "type_ready",
    "mro_implementation_unlocked",
    "mro_internal_unlocked",
    "add_subclass",
    "type_modified_unlocked",
    "init_static_type",
    "type_mro_modified",
    "type_ready_post_checks",
    "type_ready_managed_dict",
    "lookup_maybe_method",
    "is_subtype_with_mro",
    "stop_readying",
    "slotptr",
    "_PyType_GetDict",
    "skip_signature",
    "solid_base",
    "find_signature",
    "class_name",
    "_PyStaticType_InitBuiltin",
    "lookup_method",
    "_PyType_DocWithoutSignature",
    "_PyTypes_InitTypes",
    "PyType_Ready",
)
TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS: tuple[str, ...] = (
    "PyDict_Contains",
    "PyDict_New",
    "PyDict_SetDefaultRef",
    "PyDict_SetItem",
    "_PyDict_NewKeysForClass",
    "_PyDict_SendEvent",
    "_PyDict_SetItem_Take2",
    "_PyObject_InitInlineValues",
    "_Py_dict_lookup",
    "build_indices_unicode",
    "dict_setdefault_ref_lock_held",
    "dictresize",
    "find_empty_slot",
    "free_keys_object",
    "insert_combined_dict",
    "insert_to_emptydict",
    "insertdict",
    "new_dict",
    "new_keys_object",
    "setitem_take2_lock_held",
    "unicodekeys_lookup_unicode",
    "descr_new",
    "PyDescr_NewMethod",
    "PyDescr_NewClassMethod",
    "PyDescr_NewMember",
    "PyDescr_NewGetSet",
    "PyDescr_NewWrapper",
    "PyCMethod_New",
    "PyStaticMethod_New",
    "tuple_alloc",
    "PyTuple_New",
    "PyTuple_Pack",
    "PyUnicode_New",
    "PyUnicode_FromString",
    "ascii_decode",
    "unicode_decode_utf8",
    "PyUnicode_DecodeUTF8Stateful",
    "unicode_hash",
    "intern_common",
    "_PyUnicode_InternMortal",
    "PyUnicode_InternFromString",
    "_Py_HashBytes",
    "fnv",
    "get_basic_refs",
    "insert_weakref",
    "allocate_weakref",
    "get_or_create_weakref",
    "PyWeakref_NewRef",
    "_PyLong_New",
    "PyLong_FromUnsignedLong",
    "PyLong_FromVoidPtr",
    "long_hash",
    "_PyMutex_TryUnlock",
    "PyMutex_Lock",
    "PyMutex_Unlock",
    "PyObject_Hash",
    "_Py_NewReferenceNoTotal",
    "_PyStaticType_GetState",
    "_PyType_AllocNoTrack",
    "PyType_GenericAlloc",
    "_Py_ScheduleGC",
    "_PyObject_GC_Link",
    "gc_alloc",
    "_PyObject_GC_New",
    "_PyObject_GC_NewVar",
)
TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS: tuple[str, ...] = (
    "_PyUnicode_InitGlobalObjects",
    "_PyUnicode_InitStaticStrings",
    "hashtable_unicode_hash",
    "hashtable_unicode_compare",
    "_PyUnicode_ExactDealloc",
    "unicode_dealloc",
)
TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS: tuple[str, ...] = (
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
    "PyDict_ContainsString",
    "PyDict_DelItemString",
    "PyDict_GetItem",
    "dict_getitem",
    "PyDict_GetItemString",
    "PyDict_GetItemStringRef",
    "PyDict_Keys",
    "PyDict_Merge",
    "PyDict_Next",
    "PyDict_PopString",
    "PyDict_SetDefault",
    "PyDict_SetItemString",
    "PyDict_Size",
    "PyDict_Update",
    "PyDict_Values",
    "PyObject_ClearManagedDict",
    "PyObject_VisitManagedDict",
    "_PyDictKeys_DecRef",
    "_PyDictKeys_GetVersionForCurrentState",
)
TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS: tuple[str, ...] = (
    "_add_methods_to_object",
    "PyObject_SetAttrString",
    "PyObject_SetAttr",
    "PyObject_GenericSetAttr",
    "_PyObject_GenericSetAttrWithDict",
    "_PyObjectDict_SetItem",
    "_PyDict_SetItem_LockHeld",
    "_PyType_LookupRef",
    "assign_version_tag",
    "find_name_in_mro",
)
TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS: tuple[str, ...] = (
    "_PyUnicode_InternImmortal",
    "_Py_SetImmortal",
    "_Py_SetImmortalUntracked",
)
TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS: tuple[str, ...] = (
    "intern_strings",
)
TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS: tuple[str, ...] = (
    "update_one_slot",
    "intern_constants",
)
TYPE_INIT_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    TYPE_INIT_OVERLAY_CORE_FUNCTIONS
    + TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
    + TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
    + TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
    + TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS
    + TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS
    + TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS
    + TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS
)

GC_TRAVERSAL_OVERLAY_GROUP = 8
GC_TRAVERSAL_OVERLAY_SECTION = ".p2.overlay.group.00000008"
GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS: tuple[str, ...] = (
    "visit_decref",
    "visit_reachable",
    "visit_move",
    "type_is_gc",
    "dict_traverse",
    "list_traverse",
    "tupletraverse",
    "set_traverse",
    "type_traverse",
    "subtype_traverse",
    "module_traverse",
    "descr_traverse",
    "meth_traverse",
    "gc_traverse",
)
GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    "_PyGC_InitState",
    "_PyGC_Init",
    "gc_list_merge",
    "append_objects",
    "deduce_unreachable",
    "invoke_gc_callback",
    "gc_collect_main",
    "referrersvisit",
    "_PyGC_GetReferrers",
    "_PyGC_GetObjects",
    "_PyGC_Freeze",
    "_PyGC_Unfreeze",
    "_PyGC_GetFreezeCount",
    "PyGC_Enable",
    "PyGC_Disable",
    "PyGC_IsEnabled",
    "PyGC_Collect",
    "_PyGC_Collect",
    "_PyGC_CollectNoFail",
    "_PyGC_DumpShutdownStats",
    "_PyGC_Fini",
    "_PyGC_Dump",
    "PyObject_GC_Track",
    "PyObject_GC_UnTrack",
    "_Py_RunGC",
    "PyUnstable_Object_GC_NewWithExtraData",
    "_PyObject_GC_Resize",
    "PyObject_GC_Del",
    "PyObject_GC_IsTracked",
    "PyObject_GC_IsFinalized",
    "PyUnstable_GC_VisitObjects",
)
GC_TRAVERSAL_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS
    + GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS
)

# Objects/moduleobject.c and Modules/_datetimemodule.c both contribute a local
# ``module_traverse`` symbol in this CPython configuration.  The explicit
# group-8 execution body disambiguates the reviewed implementation.  Every
# public homonym must still be a valid overlay veneer; a resident or malformed
# homonym fails closed.

GC_TRAVERSAL_OVERLAY_DUPLICATE_PUBLIC_FUNCTIONS: tuple[str, ...] = (
    "module_traverse",
)

CODE_INIT_OVERLAY_GROUP = 9
CODE_INIT_OVERLAY_SECTION = ".p2.overlay.group.00000009"
CODE_INIT_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    "_PyCode_Quicken",
    "_Py_GetBaseOpcode",
)

COMPARE_OVERLAY_GROUP = 10
COMPARE_OVERLAY_SECTION = ".p2.overlay.group.0000000a"
STATIC_UNICODE_COMPARE_TARGET = "unicode_compare_eq"
COMPARE_OVERLAY_FUNCTIONS: tuple[str, ...] = (
    "PyObject_RichCompareBool",
    "PyObject_RichCompare",
    "unicode_compare_eq",
    "PyUnicode_RichCompare",
)

TUPLE_ITER_HOT_EDGE_CALLER = "PyIter_Next"
TUPLE_ITER_HOT_EDGE_TARGET = "tupleiter_next"
MARSHAL_LIST_HOT_EDGE_CALLERS: tuple[str, ...] = (
    "r_ref",
    "r_ref_reserve",
)
MARSHAL_LIST_APPEND_TARGET = "PyList_Append"
MARSHAL_LIST_RESIZE_TARGET = "_PyList_AppendTakeRefListResize"
INLINED_HOT_EDGE_FUNCTIONS: tuple[str, ...] = (
    TUPLE_ITER_HOT_EDGE_CALLER,
    TUPLE_ITER_HOT_EDGE_TARGET,
    *MARSHAL_LIST_HOT_EDGE_CALLERS,
    MARSHAL_LIST_APPEND_TARGET,
    MARSHAL_LIST_RESIZE_TARGET,
)

# A P2 immediate CALLA stores its 20-bit byte address in the low bits.  Ignore
# the four condition bits when recognizing the instruction: patch 0047 must
# reject a direct edge to the overlay stub even if a future optimizer makes
# that CALLA conditional.

P2_CALLA_IMMEDIATE_OPCODE_MASK = 0x0FF00000
P2_CALLA_IMMEDIATE_OPCODE = 0x0DC00000
P2_CALLA_IMMEDIATE_TARGET_MASK = 0x000FFFFF

# Patch 0026 selects individual startup-success functions instead of placing
# the complete Python/pylifecycle.c translation unit in group 7.  These live
# pylifecycle entry points make useful fail-closed sentinels: any one of their
# bodies in the group proves that the broad per-object overlay flag returned.

TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS: tuple[str, ...] = (
    "_PyRuntime_Early_Init",
    "_PyRuntime_Initialize",
    "_Py_PreInitializeFromConfig",
    "Py_InitializeFromConfig",
    "_Py_InitializeMain",
    "pycore_interp_init",
    "pycore_init_types",
    "init_interp_main",
)

# LLVM/GCC both append implementation-detail suffixes to local clones.  Match
# those clones as part of the logical function so that moving only a cold or
# const-propagated body into the slot cannot evade the audit.

CLONE_SUFFIX_RE = re.compile(
    r"(?:"
    r"\.(?:isra|constprop|part|cold|llvm|lto_priv|localalias|clone)"
    r"(?:\.[A-Za-z0-9_$-]+)?"
    r"|\.[0-9]+"
    r")$"
)
OVERLAY_BODY_RE = re.compile(
    r"^(?:__p2_ovlbody|__p2_overlay_body)\.[^.]+\.(.+)$"
)
OVERLAY_BODY_GROUP_RE = re.compile(
    r"^(?:__p2_ovlbody|__p2_overlay_body)\.([^.]+)\.(.+)$"
)


class VerificationError(RuntimeError):
    """The ELF does not prove the Python resident-path invariant."""


@dataclass(frozen=True)
class Requirement:
    """One logical function, optionally known under several public names."""

    category: str
    logical_name: str
    aliases: tuple[str, ...]
    if_linked: bool = False
    allow_trivial_body: bool = False


@dataclass(frozen=True)
class SymbolRecord:
    """The symbol information needed by the residency policy."""

    name: str
    address: int
    size: int
    section: str
    executable: bool
    symbol_type: str = "STT_FUNC"
    section_index: int | None = None
    section_address: int | None = None
    section_size: int | None = None
    section_type: str | None = None
    allocatable: bool | None = None


@dataclass(frozen=True)
class MapSymbolRecord:
    """One concrete symbol address and size published by the linker map."""

    name: str
    address: int
    size: int


@dataclass(frozen=True)
class OverlayFunctionRecord:
    """One map-resolved public veneer and exact overlay implementation."""

    logical_name: str
    group: str
    stub: SymbolRecord
    body: SymbolRecord


# The first entries are the Python launcher plus the complete CPython
# allocator/thread startup surface marked ``p2_hub_resident`` by the P2
# integration patch.  Public raw-allocation wrappers and their underscored
# allocator implementations are separate requirements: one must never mask
# the absence of the other.  Two source-pinned TSS allocation services are
# explicitly checked only when linked because --gc-sections removes them from
# this configuration; every other entry is mandatory.  The overlay runtime
# entries must never depend on loading an overlay themselves.  The remaining
# entries are the linked CONFIG_P2 serial-console path used by
# python_overlay_report(), plus
# The p2_boot_trace C API is linked as __p2_xmem_boot_trace so unified-memory
# lowering cannot insert a tagged load into this fatal-path UART primitive.
# The local __p2_xmem_fault symbol is intentional: this verifier reads .symtab
# (including lowercase ``t`` symbols from nm), not merely exports.

REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        "python",
        "python_main",
        ("python_main", "python_launcher_main"),
    ),
    Requirement("python", "python_worker_main", ("python_worker_main",)),
    Requirement("allocator", "_PyMem_RawMalloc", ("_PyMem_RawMalloc",)),
    Requirement("allocator", "_PyMem_RawCalloc", ("_PyMem_RawCalloc",)),
    Requirement("allocator", "_PyMem_RawRealloc", ("_PyMem_RawRealloc",)),
    Requirement("allocator", "_PyMem_RawFree", ("_PyMem_RawFree",)),
    Requirement(
        "allocator",
        "set_default_allocator_unlocked",
        ("set_default_allocator_unlocked",),
    ),
    Requirement(
        "allocator",
        "_PyMem_SetDefaultAllocator",
        ("_PyMem_SetDefaultAllocator",),
    ),
    Requirement("allocator", "PyMem_SetAllocator", ("PyMem_SetAllocator",)),
    Requirement("allocator", "PyMem_Malloc", ("PyMem_Malloc",)),
    Requirement("allocator", "PyMem_Calloc", ("PyMem_Calloc",)),
    Requirement("allocator", "PyMem_Realloc", ("PyMem_Realloc",)),
    Requirement("allocator", "PyMem_Free", ("PyMem_Free",)),
    Requirement("allocator", "PyObject_Malloc", ("PyObject_Malloc",)),
    Requirement("allocator", "PyObject_Calloc", ("PyObject_Calloc",)),
    Requirement("allocator", "PyObject_Realloc", ("PyObject_Realloc",)),
    Requirement("allocator", "PyObject_Free", ("PyObject_Free",)),
    Requirement("allocator", "PyMem_RawMalloc", ("PyMem_RawMalloc",)),
    Requirement("allocator", "PyMem_RawCalloc", ("PyMem_RawCalloc",)),
    Requirement("allocator", "PyMem_RawRealloc", ("PyMem_RawRealloc",)),
    Requirement("allocator", "PyMem_RawFree", ("PyMem_RawFree",)),
    Requirement("allocator", "_PyMem_RawWcsdup", ("_PyMem_RawWcsdup",)),
    Requirement("allocator", "_PyMem_RawStrdup", ("_PyMem_RawStrdup",)),
    Requirement(
        "thread",
        "PyThread_get_thread_ident_ex",
        ("PyThread_get_thread_ident_ex",),
    ),
    Requirement(
        "thread", "PyThread_get_thread_ident", ("PyThread_get_thread_ident",)
    ),
    Requirement(
        "thread", "PyThread_allocate_lock", ("PyThread_allocate_lock",)
    ),
    Requirement("thread", "PyThread_free_lock", ("PyThread_free_lock",)),
    Requirement(
        "thread",
        "PyThread_tss_alloc",
        ("PyThread_tss_alloc",),
        if_linked=True,
    ),
    Requirement(
        "thread",
        "PyThread_tss_free",
        ("PyThread_tss_free",),
        if_linked=True,
    ),
    Requirement(
        "thread", "PyThread_tss_is_created", ("PyThread_tss_is_created",)
    ),
    Requirement("thread", "PyThread_tss_create", ("PyThread_tss_create",)),
    Requirement("thread", "PyThread_tss_delete", ("PyThread_tss_delete",)),
    Requirement("thread", "PyThread_tss_set", ("PyThread_tss_set",)),
    Requirement("thread", "PyThread_tss_get", ("PyThread_tss_get",)),
    Requirement("startup", "_PyOS_GetOpt", ("_PyOS_GetOpt",)),
    Requirement("startup", "_PyOS_ResetGetOpt", ("_PyOS_ResetGetOpt",)),
    Requirement("startup", "PyOS_snprintf", ("PyOS_snprintf",)),
    Requirement("startup", "PyOS_vsnprintf", ("PyOS_vsnprintf",)),
    Requirement(
        "startup",
        "_PyThreadState_MustExit",
        ("_PyThreadState_MustExit",),
    ),
    Requirement(
        "startup",
        "_PyThreadState_GetCurrent",
        ("_PyThreadState_GetCurrent",),
    ),
    Requirement("startup", "_Py_Dealloc", ("_Py_Dealloc",)),
    Requirement("startup", "PyErr_Occurred", ("PyErr_Occurred",)),
    Requirement(
        "startup",
        "_PyType_InitCache",
        ("_PyType_InitCache",),
        allow_trivial_body=True,
    ),
    Requirement(
        "startup",
        "_PyObject_SetDeferredRefcount",
        ("_PyObject_SetDeferredRefcount",),
        allow_trivial_body=True,
    ),
    Requirement("startup", "_Py_NewReference", ("_Py_NewReference",)),
    Requirement("startup", "PyObject_IS_GC", ("PyObject_IS_GC",)),
    Requirement(
        "startup",
        "_PyUnicode_InternStatic",
        ("_PyUnicode_InternStatic",),
    ),
    Requirement("startup", "_Py_hashtable_get", ("_Py_hashtable_get",)),
    Requirement(
        "startup",
        "_Py_hashtable_get_entry_generic",
        ("_Py_hashtable_get_entry_generic",),
    ),
    Requirement("startup", "_Py_hashtable_set", ("_Py_hashtable_set",)),
    Requirement("startup", "hashtable_rehash", ("hashtable_rehash",)),
    Requirement("telemetry", "python_overlay_report", ("python_overlay_report",)),
    Requirement(
        "telemetry",
        "python_overlay_report_hot",
        ("python_overlay_report_hot",),
    ),
    Requirement(
        "telemetry",
        "python_overlay_telemetry_start",
        ("python_overlay_telemetry_start",),
    ),
    Requirement("overlay", "p2_overlay_get_stats", ("p2_overlay_get_stats",)),
    Requirement(
        "overlay",
        "p2_overlay_get_hot_snapshot",
        ("p2_overlay_get_hot_snapshot",),
    ),
    Requirement("overlay", "p2_hub_crc32_update", ("p2_hub_crc32_update",)),
    Requirement("overlay", "__p2_overlay_enter", ("__p2_overlay_enter",)),
    Requirement(
        "overlay", "p2_overlay_dispatch_enter", ("p2_overlay_dispatch_enter",)
    ),
    Requirement(
        "overlay", "p2_overlay_dispatch_exit", ("p2_overlay_dispatch_exit",)
    ),
    Requirement("overlay", "p2_overlay_fail", ("p2_overlay_fail",)),
    Requirement("xmem", "__p2_xmem_fault", ("__p2_xmem_fault",)),
    Requirement("stdio", "printf", ("printf",)),
    Requirement("stdio", "vfprintf", ("vfprintf",)),
    Requirement("stdio", "lib_get_stream", ("lib_get_stream",)),
    Requirement("stdio", "lib_stdoutstream", ("lib_stdoutstream",)),
    Requirement("stdio", "lib_vsprintf", ("lib_vsprintf",)),
    Requirement("stdio", "vsprintf_internal", ("vsprintf_internal",)),
    Requirement("stdio", "stdoutstream_putc", ("stdoutstream_putc",)),
    Requirement("stdio", "flockfile", ("flockfile",)),
    Requirement("stdio", "funlockfile", ("funlockfile",)),
    Requirement("stdio", "fputc", ("fputc",)),
    Requirement("stdio", "fputc_unlocked", ("fputc_unlocked",)),
    Requirement(
        "stdio", "lib_fwrite_unlocked", ("lib_fwrite_unlocked",)
    ),
    Requirement(
        "stdio", "lib_fflush_unlocked", ("lib_fflush_unlocked",)
    ),
    Requirement("serial", "write", ("write",)),
    Requirement("serial", "file_writev", ("file_writev",)),
    Requirement("serial", "uart_writev", ("uart_writev",)),
    Requirement("serial", "uart_putxmitchar", ("uart_putxmitchar",)),
    Requirement("serial", "p2_uart_send", ("p2_uart_send",)),
    Requirement("serial", "p2_lowputc", ("p2_lowputc",)),
    Requirement(
        "serial", "p2_boot_trace", ("__p2_xmem_boot_trace",)
    ),
    Requirement("serial", "up_putc", ("up_putc",)),
)


def logical_symbol_name(name: str) -> tuple[str, bool]:
    """Return a clone-independent function name and overlay-body flag."""

    overlay_body = False
    match = OVERLAY_BODY_RE.fullmatch(name)
    if match is not None:
        name = match.group(1)
        overlay_body = True

    while True:
        stripped = CLONE_SUFFIX_RE.sub("", name)
        if stripped == name:
            break
        name = stripped

    return name, overlay_body


def requirement_matches(requirement: Requirement, symbol: SymbolRecord) -> bool:
    """Return whether a concrete symbol implements a logical requirement."""

    logical, _ = logical_symbol_name(symbol.name)
    return logical in requirement.aliases


def verify_frozen_startup_data_records(
    symbols: Iterable[SymbolRecord],
    map_symbols: Iterable[MapSymbolRecord],
) -> dict[str, SymbolRecord]:
    """Require the selective frozen encodings payloads in external PSRAM."""

    records = tuple(symbols)
    input_suffix = f":({FROZEN_STARTUP_DATA_INPUT_SECTION})"
    input_ranges = tuple(
        record
        for record in map_symbols
        if record.name.endswith(input_suffix) and record.size > 0
    )
    resolved: dict[str, SymbolRecord] = {}
    for name in FROZEN_STARTUP_DATA_SYMBOLS:
        matches = tuple(symbol for symbol in records if symbol.name == name)
        if len(matches) != 1:
            raise VerificationError(
                f"{name} has {len(matches)} defined symbols; expected exactly "
                "one frozen startup data object"
            )

        symbol = matches[0]
        if symbol.symbol_type != "STT_OBJECT":
            raise VerificationError(
                f"{name} is {symbol.symbol_type}, not STT_OBJECT"
            )
        if symbol.size <= 0:
            raise VerificationError(
                f"{name} is empty; frozen startup data must be nonempty"
            )
        if symbol.section != P2_XDATA_OUTPUT_SECTION:
            raise VerificationError(
                f"{name} is in {symbol.section}, not "
                f"{P2_XDATA_OUTPUT_SECTION}"
            )
        if symbol.executable:
            raise VerificationError(
                f"{name} is in executable section {symbol.section}"
            )
        if symbol.section_type != "SHT_PROGBITS":
            raise VerificationError(
                f"{name} is backed by {symbol.section_type}, not initialized "
                "SHT_PROGBITS"
            )
        if symbol.allocatable is not True:
            raise VerificationError(
                f"{name} section {symbol.section} is not SHF_ALLOC"
            )

        end = symbol.address + symbol.size
        if symbol.address < P2_XMEM_START or end > P2_XMEM_END:
            raise VerificationError(
                f"{name} range 0x{symbol.address:x}-0x{end:x} is not wholly "
                "within external PSRAM range "
                f"0x{P2_XMEM_START:x}-0x{P2_XMEM_END:x}"
            )

        provenance = tuple(
            record
            for record in input_ranges
            if record.address <= symbol.address
            and end <= record.address + record.size
        )
        if len(provenance) != 1:
            raise VerificationError(
                f"{name} has {len(provenance)} containing "
                f"{FROZEN_STARTUP_DATA_INPUT_SECTION} linker-map input "
                "ranges; expected exactly one"
            )

        resolved[name] = symbol

    return resolved


def verify_resident_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    stubs_start: int,
    stubs_end: int,
    requirements: Sequence[Requirement] = REQUIREMENTS,
) -> dict[str, tuple[SymbolRecord, ...]]:
    """Verify abstract symbols, returning the matched audit inventory."""

    if slot_start <= 0:
        raise VerificationError(
            f"invalid overlay slot start 0x{slot_start:x}"
        )
    if stubs_start <= 0 or stubs_end <= stubs_start:
        raise VerificationError(
            "invalid overlay stub range "
            f"0x{stubs_start:x}-0x{stubs_end:x}"
        )
    if stubs_end > slot_start:
        raise VerificationError(
            "overlay stub range "
            f"0x{stubs_start:x}-0x{stubs_end:x} extends beyond "
            f"overlay slot 0x{slot_start:x}"
        )

    records = tuple(symbols)
    resolved: dict[str, tuple[SymbolRecord, ...]] = {}
    for requirement in requirements:
        matches = tuple(
            symbol
            for symbol in records
            if requirement_matches(requirement, symbol)
        )
        if not matches:
            if requirement.if_linked:
                resolved[requirement.logical_name] = ()
                continue
            aliases = ", ".join(requirement.aliases)
            raise VerificationError(
                f"missing resident {requirement.category} symbol "
                f"{requirement.logical_name} (accepted names: {aliases})"
            )

        # Aliases in a requirement describe alternate source revisions, not
        # permission to accept two different implementations in one ELF.  A
        # true linker alias at one address is harmless; distinct addresses
        # are ambiguous and fail closed.  Compiler clones retain one logical
        # base name and are all checked below.

        match_details = tuple(
            (symbol, *logical_symbol_name(symbol.name))
            for symbol in matches
        )
        non_body_matches = tuple(
            (symbol, logical)
            for symbol, logical, overlay_body in match_details
            if not overlay_body
        )
        matched_aliases = {
            logical for _symbol, logical in non_body_matches
        }
        alternate_addresses = {
            symbol.address for symbol, _logical in non_body_matches
        }
        exact_implementation_addresses = {
            symbol.address
            for symbol, _logical in non_body_matches
            if symbol.name in requirement.aliases
        }
        if (
            len(matched_aliases) > 1 and len(alternate_addresses) > 1
        ) or len(exact_implementation_addresses) > 1:
            implementations = ", ".join(
                f"{symbol.name}@0x{symbol.address:x}"
                for symbol, _logical in sorted(
                    non_body_matches,
                    key=lambda item: (item[0].address, item[0].name),
                )
            )
            raise VerificationError(
                f"{requirement.logical_name} has ambiguous implementations: "
                + implementations
            )

        # Detect the transformed body before diagnosing its public four-byte
        # stub.  This produces the actionable failure even when both symbols
        # are present, as they are in a normal P2LLVM overlay transformation.

        bodies = [
            symbol
            for symbol, _logical, overlay_body in match_details
            if overlay_body
        ]
        if bodies:
            body = bodies[0]
            raise VerificationError(
                f"{requirement.logical_name} has overlay execution body "
                f"{body.name} at 0x{body.address:x} in {body.section}"
            )

        for symbol in matches:
            if symbol.symbol_type not in ("STT_FUNC", "STT_NOTYPE"):
                raise VerificationError(
                    f"{symbol.name} is {symbol.symbol_type}, not executable code"
                )
            if not symbol.executable:
                raise VerificationError(
                    f"{symbol.name} is in non-executable section {symbol.section}"
                )
            if symbol.section == OVERLAY_STUB_SECTION:
                raise VerificationError(
                    f"{symbol.name} is only an overlay stub in {symbol.section}"
                )
            if stubs_start <= symbol.address < stubs_end:
                raise VerificationError(
                    f"{symbol.name} starts at 0x{symbol.address:x} inside "
                    "overlay stub range "
                    f"0x{stubs_start:x}-0x{stubs_end:x}"
                )
            if symbol.address >= slot_start:
                raise VerificationError(
                    f"{symbol.name} starts at 0x{symbol.address:x}, not below "
                    f"overlay slot 0x{slot_start:x}"
                )
            if symbol.size <= 4 and not requirement.allow_trivial_body:
                raise VerificationError(
                    f"{symbol.name} is {symbol.size} bytes; a resident "
                    "implementation must be larger than the four-byte "
                    "P2LLVM overlay veneer"
                )

            end = symbol.address + symbol.size
            if (
                symbol.size > 0
                and symbol.address < stubs_end
                and end > stubs_start
            ):
                raise VerificationError(
                    f"{symbol.name} range 0x{symbol.address:x}-0x{end:x} "
                    "overlaps overlay stub range "
                    f"0x{stubs_start:x}-0x{stubs_end:x}"
                )
            if symbol.size > 0 and end > slot_start:
                raise VerificationError(
                    f"{symbol.name} range 0x{symbol.address:x}-0x{end:x} "
                    f"crosses overlay slot 0x{slot_start:x}"
                )

        resolved[requirement.logical_name] = tuple(
            sorted(matches, key=lambda symbol: (symbol.address, symbol.name))
        )

    return resolved


def _verify_overlay_group_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
    *,
    group: int,
    section: str,
    functions: Sequence[str],
    if_linked_functions: Sequence[str] = (),
    duplicate_public_functions: Sequence[str] = (),
    contract: str,
    require_concrete_section: bool = False,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require exact overlay stub/body pairs in one explicit group."""

    if slot_start <= 0:
        raise VerificationError(
            f"invalid overlay slot start 0x{slot_start:x}"
        )
    if slot_end <= slot_start:
        raise VerificationError(
            "invalid overlay slot range "
            f"0x{slot_start:x}-0x{slot_end:x}"
        )
    if stubs_start <= 0 or stubs_end <= stubs_start:
        raise VerificationError(
            "invalid overlay stub range "
            f"0x{stubs_start:x}-0x{stubs_end:x}"
        )
    if stubs_end > slot_start:
        raise VerificationError(
            "overlay stub range "
            f"0x{stubs_start:x}-0x{stubs_end:x} extends beyond "
            f"overlay slot 0x{slot_start:x}"
        )

    records = tuple(symbols)
    duplicate_public = set(duplicate_public_functions)
    resolved: dict[str, tuple[SymbolRecord, SymbolRecord]] = {}
    for function in functions:
        matches = tuple(
            (symbol, overlay_body)
            for symbol in records
            for logical, overlay_body in (logical_symbol_name(symbol.name),)
            if logical == function
        )
        public = tuple(
            symbol for symbol, overlay_body in matches if not overlay_body
        )
        all_bodies = tuple(
            symbol for symbol, overlay_body in matches if overlay_body
        )
        if (
            function in if_linked_functions
            and not public
            and not all_bodies
        ):
            continue
        if function in duplicate_public:
            # Static functions from different translation units can have the
            # same local ELF symbol name.  Select the reviewed implementation
            # by its explicit output section, but require every public
            # homonym to remain a well-formed overlay veneer.

            bodies = tuple(
                body for body in all_bodies if body.section == section
            )
            if not public:
                raise VerificationError(
                    f"{contract} overlay function {function} has 0 public "
                    "symbols; expected at least one four-byte stub"
                )
            if len(bodies) != 1:
                raise VerificationError(
                    f"{contract} overlay function {function} has "
                    f"{len(bodies)} execution bodies in explicit group "
                    f"{group}; expected one"
                )
            stubs = public
        else:
            bodies = all_bodies
            if len(public) != 1:
                raise VerificationError(
                    f"{contract} overlay function {function} has "
                    f"{len(public)} public symbols; expected one four-byte "
                    "stub"
                )
            if len(bodies) != 1:
                raise VerificationError(
                    f"{contract} overlay function {function} has "
                    f"{len(bodies)} execution bodies; expected one"
                )
            stubs = public

        body = bodies[0]
        for stub in stubs:
            if stub.section != OVERLAY_STUB_SECTION:
                raise VerificationError(
                    f"{contract} overlay function {function} public symbol "
                    f"is in {stub.section}, not {OVERLAY_STUB_SECTION}"
                )
            if not stubs_start <= stub.address < stubs_end:
                raise VerificationError(
                    f"{contract} overlay function {function} stub starts at "
                    f"0x{stub.address:x}, outside overlay stub range "
                    f"0x{stubs_start:x}-0x{stubs_end:x}"
                )
            if stub.symbol_type != "STT_FUNC":
                raise VerificationError(
                    f"{contract} overlay function {function} stub is "
                    f"{stub.symbol_type}, not STT_FUNC"
                )
            if not stub.executable or stub.size != 4:
                raise VerificationError(
                    f"{contract} overlay function {function} stub must be one "
                    "executable four-byte P2 instruction"
                )
            if stub.address & 3:
                raise VerificationError(
                    f"{contract} overlay function {function} stub is not P2 "
                    "instruction aligned"
                )
            if stub.address + stub.size > stubs_end:
                raise VerificationError(
                    f"{contract} overlay function {function} stub range "
                    f"crosses overlay stub end 0x{stubs_end:x}"
                )
        if body.section != section:
            raise VerificationError(
                f"{contract} overlay body {body.name} is in {body.section}, not "
                f"explicit group {group} ({section})"
            )
        if body.symbol_type != "STT_FUNC":
            raise VerificationError(
                f"{contract} overlay body {body.name} is {body.symbol_type}, "
                "not STT_FUNC"
            )
        if not body.executable or body.size <= 4:
            raise VerificationError(
                f"{contract} overlay body {body.name} is not a substantive "
                "executable implementation"
            )
        if body.address & 3 or body.size & 3:
            raise VerificationError(
                f"{contract} overlay body {body.name} is not P2 instruction "
                "aligned"
            )
        if body.address < slot_start + 4:
            raise VerificationError(
                f"{contract} overlay body {body.name} starts before the group "
                f"payload at 0x{slot_start + 4:x}"
            )
        body_end = body.address + body.size
        if body_end > slot_end:
            raise VerificationError(
                f"{contract} overlay body {body.name} range "
                f"0x{body.address:x}-0x{body_end:x} escapes overlay slot "
                f"ending at 0x{slot_end:x}"
            )
        if body.section_address is not None:
            if body.section_address != slot_start:
                raise VerificationError(
                    f"{contract} overlay body {body.name} section starts at "
                    f"0x{body.section_address:x}, not overlay slot "
                    f"0x{slot_start:x}"
                )
            if body.section_size is None or body.section_size <= 4:
                raise VerificationError(
                    f"{contract} overlay body {body.name} section lacks a "
                    f"substantive group-{group} payload"
                )
            section_end = body.section_address + body.section_size
            if section_end > slot_end:
                raise VerificationError(
                    f"{contract} overlay body {body.name} section ends at "
                    f"0x{section_end:x}, beyond overlay slot "
                    f"0x{slot_end:x}"
                )
            if body.address < body.section_address or body_end > section_end:
                raise VerificationError(
                    f"{contract} overlay body {body.name} range "
                    f"0x{body.address:x}-0x{body_end:x} escapes group-{group} "
                    f"section ending at 0x{section_end:x}"
                )
        resolved[function] = (stubs[0], body)

    # A malformed ELF may contain multiple output sections with the same
    # spelling.  Section names alone would then not prove co-location.  Real
    # ELF records carry their section indexes, so require every linked body to
    # resolve to the same concrete output section.

    body_section_indexes = {
        body.section_index
        for _stub, body in resolved.values()
        if body.section_index is not None
    }
    missing_section_index = any(
        body.section_index is None for _stub, body in resolved.values()
    )
    if (
        require_concrete_section
        and (len(body_section_indexes) != 1 or missing_section_index)
    ) or (
        body_section_indexes
        and (len(body_section_indexes) != 1 or missing_section_index)
    ):
        rendered = ", ".join(
            "unknown" if index is None else str(index)
            for index in sorted(
                (body.section_index for _stub, body in resolved.values()),
                key=lambda index: -1 if index is None else index,
            )
        )
        raise VerificationError(
            f"{contract} overlay bodies do not share one concrete group-{group} "
            f"section (indexes: {rendered})"
        )

    return resolved


def verify_utf8_overlay_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    stubs_start: int,
    stubs_end: int,
    slot_end: int | None = None,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require every linked UTF-8 decoder stub/body pair in group 6."""

    records = tuple(symbols)
    if slot_end is None:
        slot_end = max(
            (
                symbol.section_address + symbol.section_size
                for symbol in records
                if symbol.section == UTF8_OVERLAY_SECTION
                and symbol.section_address is not None
                and symbol.section_size is not None
            ),
            default=slot_start + (1 << 32),
        )
    return _verify_overlay_group_records(
        records,
        slot_start,
        slot_end,
        stubs_start,
        stubs_end,
        group=UTF8_OVERLAY_GROUP,
        section=UTF8_OVERLAY_SECTION,
        functions=UTF8_OVERLAY_FUNCTIONS,
        if_linked_functions=UTF8_OVERLAY_IF_LINKED_FUNCTIONS,
        contract="UTF-8",
    )


def verify_type_init_overlay_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require the complete CPython type-init/startup loop in group 7."""

    records = tuple(symbols)
    resolved = _verify_overlay_group_records(
        records,
        slot_start,
        slot_end,
        stubs_start,
        stubs_end,
        group=TYPE_INIT_OVERLAY_GROUP,
        section=TYPE_INIT_OVERLAY_SECTION,
        functions=TYPE_INIT_OVERLAY_FUNCTIONS,
        contract="type-init",
        require_concrete_section=True,
    )
    forbidden = set(TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS)
    for symbol in records:
        logical, overlay_body = logical_symbol_name(symbol.name)
        if (
            overlay_body
            and symbol.section == TYPE_INIT_OVERLAY_SECTION
            and logical in forbidden
        ):
            raise VerificationError(
                "type-init overlay group 7 contains forbidden pylifecycle "
                f"body {symbol.name}; select individual success-path "
                "functions instead of the complete pylifecycle translation "
                "unit"
            )

    # Group 7 is a deliberately measured closure, not a general placement
    # bucket.  Requiring the known bodies is insufficient: an unrelated
    # function could otherwise enter the same section, consume the very tight
    # slot budget, and reintroduce startup coupling without failing this
    # audit.  Compare concrete ELF function symbols so aliases, unrecognized
    # body spellings, and future compiler output all fail closed until they
    # are reviewed and explicitly added to the contract.

    expected_bodies = {body.name for _stub, body in resolved.values()}
    unexpected_bodies = sorted(
        (
            symbol
            for symbol in records
            if symbol.section == TYPE_INIT_OVERLAY_SECTION
            and symbol.symbol_type == "STT_FUNC"
            and symbol.name not in expected_bodies
        ),
        key=lambda symbol: (symbol.address, symbol.name),
    )
    if unexpected_bodies:
        symbol = unexpected_bodies[0]
        raise VerificationError(
            "type-init overlay group 7 contains unexpected function body "
            f"{symbol.name} at 0x{symbol.address:x}-"
            f"0x{symbol.address + symbol.size:x}; the group must equal the "
            "reviewed type-init closure exactly"
        )

    return resolved


def verify_gc_traversal_overlay_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require the exact cyclic-GC collector working set in group 8."""

    records = tuple(symbols)
    resolved = _verify_overlay_group_records(
        records,
        slot_start,
        slot_end,
        stubs_start,
        stubs_end,
        group=GC_TRAVERSAL_OVERLAY_GROUP,
        section=GC_TRAVERSAL_OVERLAY_SECTION,
        functions=GC_TRAVERSAL_OVERLAY_FUNCTIONS,
        duplicate_public_functions=(
            GC_TRAVERSAL_OVERLAY_DUPLICATE_PUBLIC_FUNCTIONS
        ),
        contract="cyclic-GC",
        require_concrete_section=True,
    )

    # Like group 7, group 8 is a measured closure rather than a general
    # placement bucket.  Its projected headroom is useful only if every linked
    # function body is one of the reviewed collector or traversal bodies.

    expected_bodies = {body.name for _stub, body in resolved.values()}
    unexpected_bodies = sorted(
        (
            symbol
            for symbol in records
            if symbol.section == GC_TRAVERSAL_OVERLAY_SECTION
            and symbol.symbol_type == "STT_FUNC"
            and symbol.name not in expected_bodies
        ),
        key=lambda symbol: (symbol.address, symbol.name),
    )
    if unexpected_bodies:
        symbol = unexpected_bodies[0]
        raise VerificationError(
            "cyclic-GC overlay group 8 contains unexpected function body "
            f"{symbol.name} at 0x{symbol.address:x}-"
            f"0x{symbol.address + symbol.size:x}; the group must equal the "
            "reviewed collector closure exactly"
        )

    return resolved


def verify_code_init_overlay_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require the exact frozen-code quickening loop in group 9."""

    records = tuple(symbols)
    resolved = _verify_overlay_group_records(
        records,
        slot_start,
        slot_end,
        stubs_start,
        stubs_end,
        group=CODE_INIT_OVERLAY_GROUP,
        section=CODE_INIT_OVERLAY_SECTION,
        functions=CODE_INIT_OVERLAY_FUNCTIONS,
        contract="frozen-code",
        require_concrete_section=True,
    )

    expected_bodies = {body.name for _stub, body in resolved.values()}
    unexpected_bodies = sorted(
        (
            symbol
            for symbol in records
            if symbol.section == CODE_INIT_OVERLAY_SECTION
            and symbol.symbol_type == "STT_FUNC"
            and symbol.name not in expected_bodies
        ),
        key=lambda symbol: (symbol.address, symbol.name),
    )
    if unexpected_bodies:
        symbol = unexpected_bodies[0]
        raise VerificationError(
            "frozen-code overlay group 9 contains unexpected function body "
            f"{symbol.name} at 0x{symbol.address:x}-"
            f"0x{symbol.address + symbol.size:x}; the group must equal the "
            "reviewed quickening loop exactly"
        )

    return resolved


def verify_compare_overlay_records(
    symbols: Iterable[SymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> dict[str, tuple[SymbolRecord, SymbolRecord]]:
    """Require the exact importlib rich-comparison path in group 10."""

    records = tuple(symbols)
    resolved = _verify_overlay_group_records(
        records,
        slot_start,
        slot_end,
        stubs_start,
        stubs_end,
        group=COMPARE_OVERLAY_GROUP,
        section=COMPARE_OVERLAY_SECTION,
        functions=COMPARE_OVERLAY_FUNCTIONS,
        contract="rich-comparison",
        require_concrete_section=True,
    )

    expected_bodies = {body.name for _stub, body in resolved.values()}
    unexpected_bodies = sorted(
        (
            symbol
            for symbol in records
            if symbol.section == COMPARE_OVERLAY_SECTION
            and symbol.symbol_type == "STT_FUNC"
            and symbol.name not in expected_bodies
        ),
        key=lambda symbol: (symbol.address, symbol.name),
    )
    if unexpected_bodies:
        symbol = unexpected_bodies[0]
        raise VerificationError(
            "rich-comparison overlay group 10 contains unexpected function "
            f"body {symbol.name} at 0x{symbol.address:x}-"
            f"0x{symbol.address + symbol.size:x}; the group must equal the "
            "reviewed comparison path exactly"
        )

    return resolved


def _verify_type_init_overlay_budget(section_size: int) -> None:
    """Enforce the fixed patch-0047 group-7 execution-slot budget."""

    if section_size > TYPE_INIT_OVERLAY_MAX_SIZE:
        raise VerificationError(
            "type-init overlay group 7 is "
            f"0x{section_size:x} bytes; patch 0047 requires at most "
            f"0x{TYPE_INIT_OVERLAY_MAX_SIZE:x}"
        )


def _p2_immediate_calla_offsets(code: bytes, target: int) -> tuple[int, ...]:
    """Return aligned CALLA offsets whose immediate target is ``target``."""

    return tuple(
        offset
        for offset in range(0, len(code), 4)
        for word in (int.from_bytes(code[offset : offset + 4], "little"),)
        if (
            word & P2_CALLA_IMMEDIATE_OPCODE_MASK
            == P2_CALLA_IMMEDIATE_OPCODE
            and word & P2_CALLA_IMMEDIATE_TARGET_MASK == target
        )
    )


def parse_link_map_symbols(text: str) -> tuple[MapSymbolRecord, ...]:
    """Parse concrete symbol rows from an LLD linker map."""

    records: list[MapSymbolRecord] = []
    hex_field = re.compile(r"^[0-9A-Fa-f]+$")
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 5:
            continue
        vma, lma, size, _align, name = fields
        if not all(hex_field.fullmatch(field) for field in (vma, lma, size)):
            continue
        records.append(
            MapSymbolRecord(
                name=name,
                address=int(vma, 16),
                size=int(size, 16),
            )
        )

    if not records:
        raise VerificationError("linker map has no parseable symbol rows")
    return tuple(records)


def _link_map_symbols(path: pathlib.Path) -> tuple[MapSymbolRecord, ...]:
    """Read the linker map required by auto-group hot-edge audits."""

    if not path.is_file() or path.stat().st_size == 0:
        raise VerificationError(f"linker map is missing or empty: {path}")
    return parse_link_map_symbols(path.read_text(encoding="utf-8"))


def resolve_inlined_hot_edge_records(
    symbols: Iterable[SymbolRecord],
    map_symbols: Iterable[MapSymbolRecord],
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> dict[str, OverlayFunctionRecord]:
    """Resolve hot-edge veneers and auto-group bodies from the linker map."""

    elf_records = tuple(symbols)
    map_records = tuple(map_symbols)
    resolved: dict[str, OverlayFunctionRecord] = {}

    def elf_match(record: MapSymbolRecord, role: str) -> SymbolRecord:
        matches = tuple(
            symbol
            for symbol in elf_records
            if symbol.name == record.name
            and symbol.address == record.address
            and symbol.size == record.size
        )
        if len(matches) != 1:
            raise VerificationError(
                f"{role} map symbol {record.name}=0x{record.address:x}+"
                f"0x{record.size:x} has {len(matches)} exact ELF matches; "
                "expected one"
            )
        return matches[0]

    for name in INLINED_HOT_EDGE_FUNCTIONS:
        map_stubs = tuple(record for record in map_records if record.name == name)
        if len(map_stubs) != 1:
            raise VerificationError(
                f"hot-edge function {name} has {len(map_stubs)} exact public "
                "symbols in the linker map; expected one overlay veneer"
            )
        map_stub = map_stubs[0]
        if map_stub.size != 4 or not (
            stubs_start
            <= map_stub.address
            < map_stub.address + map_stub.size
            <= stubs_end
        ):
            raise VerificationError(
                f"hot-edge function {name} map symbol "
                f"0x{map_stub.address:x}-"
                f"0x{map_stub.address + map_stub.size:x} is not one "
                f"four-byte veneer inside 0x{stubs_start:x}-0x{stubs_end:x}"
            )

        map_bodies = tuple(
            record
            for record in map_records
            if logical_symbol_name(record.name) == (name, True)
        )
        if len(map_bodies) != 1:
            raise VerificationError(
                f"hot-edge function {name} has {len(map_bodies)} overlay "
                "execution bodies in the linker map; expected one exact body"
            )
        map_body = map_bodies[0]
        group_match = OVERLAY_BODY_GROUP_RE.fullmatch(map_body.name)
        if group_match is None:
            raise VerificationError(
                f"hot-edge body {map_body.name} does not publish its group"
            )
        group = group_match.group(1)

        stub = elf_match(map_stub, f"hot-edge {name} veneer")
        body = elf_match(map_body, f"hot-edge {name} body")
        if (
            stub.symbol_type != "STT_FUNC"
            or not stub.executable
            or stub.section != OVERLAY_STUB_SECTION
        ):
            raise VerificationError(
                f"hot-edge function {name} veneer is not an executable "
                f"function in {OVERLAY_STUB_SECTION}"
            )
        if body.symbol_type != "STT_FUNC" or not body.executable:
            raise VerificationError(
                f"hot-edge function {name} body {body.name} is not an "
                "executable function"
            )
        if not body.section.startswith(".p2.overlay.group."):
            raise VerificationError(
                f"hot-edge function {name} body {body.name} is in "
                f"{body.section}, not a concrete overlay group"
            )
        if body.size <= 4 or body.size & 3 or body.address & 3:
            raise VerificationError(
                f"hot-edge function {name} body {body.name} is not a "
                "substantive P2-instruction-aligned implementation"
            )
        if not (
            slot_start
            <= body.address
            < body.address + body.size
            <= slot_end
        ):
            raise VerificationError(
                f"hot-edge function {name} body {body.name} range "
                f"0x{body.address:x}-0x{body.address + body.size:x} escapes "
                f"overlay slot 0x{slot_start:x}-0x{slot_end:x}"
            )

        resolved[name] = OverlayFunctionRecord(
            logical_name=name,
            group=group,
            stub=stub,
            body=body,
        )

    return resolved


def verify_inlined_hot_edge_records(
    resolved: Mapping[str, OverlayFunctionRecord],
    body_code: Mapping[str, bytes],
) -> None:
    """Prove patches 0048 and 0050 removed only their common hot edges."""

    missing = tuple(
        name for name in INLINED_HOT_EDGE_FUNCTIONS if name not in resolved
    )
    if missing:
        raise VerificationError(
            "inlined hot-edge audit is missing map-resolved functions: "
            + ", ".join(missing)
        )

    def exact_code(name: str) -> bytes:
        implementation = resolved[name]
        if name not in body_code:
            raise VerificationError(
                f"inlined hot-edge audit has no exact code for {name} "
                f"group {implementation.group} body {implementation.body.name}"
            )
        code = body_code[name]
        if len(code) != implementation.body.size:
            raise VerificationError(
                f"inlined hot-edge body code for {name} is {len(code)} bytes; "
                f"{implementation.body.name} is {implementation.body.size} bytes"
            )
        if len(code) & 3:
            raise VerificationError(
                f"inlined hot-edge body code for {name} is not P2 "
                "instruction aligned"
            )
        return code

    def call_offsets(caller: str, target: str) -> tuple[int, ...]:
        target_stub = resolved[target].stub
        if not 0 <= target_stub.address <= P2_CALLA_IMMEDIATE_TARGET_MASK:
            raise VerificationError(
                f"hot-edge target {target} veneer address "
                f"0x{target_stub.address:x} does not fit an immediate P2 CALLA"
            )
        return _p2_immediate_calla_offsets(
            exact_code(caller), target_stub.address
        )

    tuple_offsets = call_offsets(
        TUPLE_ITER_HOT_EDGE_CALLER, TUPLE_ITER_HOT_EDGE_TARGET
    )
    if tuple_offsets:
        rendered = ", ".join(
            f"body+0x{offset:x}" for offset in tuple_offsets
        )
        target_stub = resolved[TUPLE_ITER_HOT_EDGE_TARGET].stub
        raise VerificationError(
            f"patch-0048 caller {TUPLE_ITER_HOT_EDGE_CALLER} retains a "
            f"direct CALLA to {TUPLE_ITER_HOT_EDGE_TARGET} overlay veneer "
            f"0x{target_stub.address:x} at {rendered}"
        )

    for caller in MARSHAL_LIST_HOT_EDGE_CALLERS:
        append_offsets = call_offsets(caller, MARSHAL_LIST_APPEND_TARGET)
        if append_offsets:
            rendered = ", ".join(
                f"body+0x{offset:x}" for offset in append_offsets
            )
            target_stub = resolved[MARSHAL_LIST_APPEND_TARGET].stub
            raise VerificationError(
                f"patch-0050 caller {caller} retains a direct CALLA to "
                f"{MARSHAL_LIST_APPEND_TARGET} overlay veneer "
                f"0x{target_stub.address:x} at {rendered}"
            )

        resize_offsets = call_offsets(caller, MARSHAL_LIST_RESIZE_TARGET)
        if len(resize_offsets) != 1:
            rendered = (
                ", ".join(f"body+0x{offset:x}" for offset in resize_offsets)
                if resize_offsets
                else "none"
            )
            target_stub = resolved[MARSHAL_LIST_RESIZE_TARGET].stub
            raise VerificationError(
                f"patch-0050 caller {caller} has {len(resize_offsets)} "
                f"immediate CALLA instructions to "
                f"{MARSHAL_LIST_RESIZE_TARGET} overlay veneer "
                f"0x{target_stub.address:x} at {rendered}; expected exactly one"
            )


def verify_static_unicode_hot_edge_records(
    type_init_resolved: Mapping[
        str, tuple[SymbolRecord, SymbolRecord]
    ],
    compare_resolved: Mapping[str, tuple[SymbolRecord, SymbolRecord]],
    callback_code: bytes,
) -> None:
    """Prove patch 0047 removed the group-7 to group-10 callback edge."""

    if STATIC_UNICODE_CALLBACK not in type_init_resolved:
        raise VerificationError(
            "patch-0047 hot-edge audit is missing type-init callback "
            f"{STATIC_UNICODE_CALLBACK}"
        )
    if STATIC_UNICODE_COMPARE_TARGET not in compare_resolved:
        raise VerificationError(
            "patch-0047 hot-edge audit is missing comparison target "
            f"{STATIC_UNICODE_COMPARE_TARGET}"
        )

    _callback_stub, callback_body = type_init_resolved[
        STATIC_UNICODE_CALLBACK
    ]
    target_stub, target_body = compare_resolved[
        STATIC_UNICODE_COMPARE_TARGET
    ]
    if callback_body.section != TYPE_INIT_OVERLAY_SECTION:
        raise VerificationError(
            f"patch-0047 callback {STATIC_UNICODE_CALLBACK} is in "
            f"{callback_body.section}, not explicit group "
            f"{TYPE_INIT_OVERLAY_GROUP} ({TYPE_INIT_OVERLAY_SECTION})"
        )
    if target_body.section != COMPARE_OVERLAY_SECTION:
        raise VerificationError(
            f"patch-0047 comparison target {STATIC_UNICODE_COMPARE_TARGET} "
            f"is in {target_body.section}, not explicit group "
            f"{COMPARE_OVERLAY_GROUP} ({COMPARE_OVERLAY_SECTION})"
        )
    if callback_body.section_size is None:
        raise VerificationError(
            "patch-0047 callback does not identify the concrete group-7 "
            "section size"
        )
    _verify_type_init_overlay_budget(callback_body.section_size)

    if len(callback_code) != callback_body.size:
        raise VerificationError(
            f"patch-0047 callback code is {len(callback_code)} bytes; "
            f"{callback_body.name} is {callback_body.size} bytes"
        )
    if len(callback_code) & 3:
        raise VerificationError(
            "patch-0047 callback code is not P2 instruction aligned"
        )
    if not 0 <= target_stub.address <= P2_CALLA_IMMEDIATE_TARGET_MASK:
        raise VerificationError(
            f"{STATIC_UNICODE_COMPARE_TARGET} overlay stub address "
            f"0x{target_stub.address:x} does not fit an immediate P2 CALLA"
        )

    offsets = _p2_immediate_calla_offsets(
        callback_code, target_stub.address
    )
    if offsets:
        rendered = ", ".join(f"body+0x{offset:x}" for offset in offsets)
        raise VerificationError(
            f"patch-0047 callback {STATIC_UNICODE_CALLBACK} retains a "
            f"direct CALLA to {STATIC_UNICODE_COMPARE_TARGET} overlay stub "
            f"0x{target_stub.address:x} at {rendered}"
        )


def _symbol_code(elf: ELFFile, symbol: SymbolRecord) -> bytes:
    """Read one verified function body's exact linked instruction bytes."""

    if symbol.section_index is None:
        raise VerificationError(
            f"{symbol.name} has no concrete ELF section index"
        )
    section = elf.get_section(symbol.section_index)
    if section.name != symbol.section:
        raise VerificationError(
            f"{symbol.name} section index names {section.name}, not "
            f"{symbol.section}"
        )
    if str(section["sh_type"]) != "SHT_PROGBITS":
        raise VerificationError(
            f"{symbol.name} is not backed by a PROGBITS section"
        )

    section_address = int(section["sh_addr"])
    offset = symbol.address - section_address
    end = offset + symbol.size
    data = section.data()
    if offset < 0 or end > len(data):
        raise VerificationError(
            f"{symbol.name} range 0x{symbol.address:x}-"
            f"0x{symbol.address + symbol.size:x} escapes concrete section "
            f"{section.name}"
        )
    return data[offset:end]


def _symbol_records(
    elf: ELFFile,
) -> tuple[list[SymbolRecord], int, int, int, int]:
    symtab = elf.get_section_by_name(".symtab")
    if symtab is None:
        raise VerificationError("missing .symtab; audit the unstripped nuttx.full ELF")

    records: list[SymbolRecord] = []
    layout_values: dict[str, list[int]] = {
        SLOT_START_SYMBOL: [],
        SLOT_END_SYMBOL: [],
        OVERLAY_STUBS_START_SYMBOL: [],
        OVERLAY_STUBS_END_SYMBOL: [],
    }
    for symbol in symtab.iter_symbols():
        name = symbol.name
        if not name or symbol["st_shndx"] == "SHN_UNDEF":
            continue

        if name in layout_values:
            layout_values[name].append(int(symbol["st_value"]))

        section_name = str(symbol["st_shndx"])
        executable = False
        section_address: int | None = None
        section_size: int | None = None
        section_type: str | None = None
        allocatable: bool | None = None
        section_index = symbol["st_shndx"]
        if isinstance(section_index, int):
            section = elf.get_section(section_index)
            section_name = section.name
            section_address = int(section["sh_addr"])
            section_size = int(section["sh_size"])
            section_type = str(section["sh_type"])
            section_flags = int(section["sh_flags"])
            executable = bool(
                section_flags & SH_FLAGS.SHF_EXECINSTR
            )
            allocatable = bool(section_flags & SH_FLAGS.SHF_ALLOC)

        records.append(
            SymbolRecord(
                name=name,
                address=int(symbol["st_value"]),
                size=int(symbol["st_size"]),
                section=section_name,
                executable=executable,
                symbol_type=str(symbol["st_info"]["type"]),
                section_index=(
                    section_index if isinstance(section_index, int) else None
                ),
                section_address=section_address,
                section_size=section_size,
                section_type=section_type,
                allocatable=allocatable,
            )
        )

    for name, values in layout_values.items():
        if len(values) != 1:
            raise VerificationError(
                f"{name} has {len(values)} defined symbols; expected one"
            )

    return (
        records,
        layout_values[SLOT_START_SYMBOL][0],
        layout_values[SLOT_END_SYMBOL][0],
        layout_values[OVERLAY_STUBS_START_SYMBOL][0],
        layout_values[OVERLAY_STUBS_END_SYMBOL][0],
    )


def _verify_overlay_sections(
    elf: ELFFile,
    slot_start: int,
    slot_end: int,
    stubs_start: int,
    stubs_end: int,
) -> None:
    """Verify the unique concrete output sections used by overlay audits."""

    by_name = {
        name: [section for section in elf.iter_sections() if section.name == name]
        for name in (
            OVERLAY_STUB_SECTION,
            UTF8_OVERLAY_SECTION,
            TYPE_INIT_OVERLAY_SECTION,
            GC_TRAVERSAL_OVERLAY_SECTION,
            CODE_INIT_OVERLAY_SECTION,
            COMPARE_OVERLAY_SECTION,
        )
    }
    for name, sections in by_name.items():
        if len(sections) != 1:
            raise VerificationError(
                f"ELF has {len(sections)} {name} output sections; expected one"
            )

    stub_section = by_name[OVERLAY_STUB_SECTION][0]
    stub_address = int(stub_section["sh_addr"])
    stub_size = int(stub_section["sh_size"])
    stub_end = stub_address + stub_size
    if str(stub_section["sh_type"]) != "SHT_PROGBITS":
        raise VerificationError(
            f"{OVERLAY_STUB_SECTION} is not a PROGBITS output section"
        )
    if not int(stub_section["sh_flags"]) & SH_FLAGS.SHF_EXECINSTR:
        raise VerificationError(
            f"{OVERLAY_STUB_SECTION} output section is not executable"
        )
    if stub_size == 0 or not (
        stubs_start <= stub_address < stub_end <= stubs_end
    ):
        raise VerificationError(
            f"{OVERLAY_STUB_SECTION} output range "
            f"0x{stub_address:x}-0x{stub_end:x} is outside published stub "
            f"range 0x{stubs_start:x}-0x{stubs_end:x}"
        )

    if slot_end <= slot_start:
        raise VerificationError(
            "invalid overlay slot range "
            f"0x{slot_start:x}-0x{slot_end:x}"
        )
    for group, name in (
        (UTF8_OVERLAY_GROUP, UTF8_OVERLAY_SECTION),
        (TYPE_INIT_OVERLAY_GROUP, TYPE_INIT_OVERLAY_SECTION),
        (GC_TRAVERSAL_OVERLAY_GROUP, GC_TRAVERSAL_OVERLAY_SECTION),
        (CODE_INIT_OVERLAY_GROUP, CODE_INIT_OVERLAY_SECTION),
        (COMPARE_OVERLAY_GROUP, COMPARE_OVERLAY_SECTION),
    ):
        group_section = by_name[name][0]
        group_address = int(group_section["sh_addr"])
        group_size = int(group_section["sh_size"])
        group_end = group_address + group_size
        if str(group_section["sh_type"]) != "SHT_PROGBITS":
            raise VerificationError(
                f"{name} is not a PROGBITS output section"
            )
        if not int(group_section["sh_flags"]) & SH_FLAGS.SHF_EXECINSTR:
            raise VerificationError(
                f"{name} output section is not executable"
            )
        if group_address != slot_start:
            raise VerificationError(
                f"{name} starts at 0x{group_address:x}, not "
                f"overlay slot 0x{slot_start:x}"
            )
        if group_size <= 4:
            raise VerificationError(
                f"{name} is {group_size} bytes; group {group} has no "
                "substantive payload"
            )
        if group == TYPE_INIT_OVERLAY_GROUP:
            _verify_type_init_overlay_budget(group_size)
        if group_end > slot_end:
            raise VerificationError(
                f"{name} ends at 0x{group_end:x}, beyond overlay slot "
                f"0x{slot_end:x}"
            )
        if group_address & 3 or group_size & 3:
            raise VerificationError(
                f"{name} is not P2 instruction aligned"
            )


def verify(
    path: pathlib.Path,
    map_path: pathlib.Path | None = None,
) -> tuple[
    int,
    int,
    int,
    dict[str, tuple[SymbolRecord, ...]],
    dict[str, SymbolRecord],
]:
    """Verify one full P2 ELF plus map and return its audited inventories."""

    if not path.is_file() or path.stat().st_size == 0:
        raise VerificationError(f"ELF is missing or empty: {path}")
    if map_path is None:
        map_path = path.with_suffix(".map")
    map_symbols = _link_map_symbols(map_path)

    with path.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.header["e_type"] != "ET_EXEC":
            raise VerificationError("P2 Python residency input must be ET_EXEC")
        if elf.elfclass != 32 or not elf.little_endian:
            raise VerificationError("P2 ELF must be 32-bit little-endian")
        if int(elf.header["e_machine"]) != P2_ELF_MACHINE:
            raise VerificationError(
                f"ELF machine is {int(elf.header['e_machine'])}; "
                f"expected P2 machine {P2_ELF_MACHINE}"
            )

        records, slot_start, slot_end, stubs_start, stubs_end = _symbol_records(
            elf
        )
        frozen_startup_data = verify_frozen_startup_data_records(
            records, map_symbols
        )
        _verify_overlay_sections(
            elf, slot_start, slot_end, stubs_start, stubs_end
        )
        resolved = verify_resident_records(
            records, slot_start, stubs_start, stubs_end
        )
        verify_utf8_overlay_records(
            records, slot_start, stubs_start, stubs_end, slot_end
        )
        type_init_resolved = verify_type_init_overlay_records(
            records, slot_start, slot_end, stubs_start, stubs_end
        )
        verify_gc_traversal_overlay_records(
            records, slot_start, slot_end, stubs_start, stubs_end
        )
        verify_code_init_overlay_records(
            records, slot_start, slot_end, stubs_start, stubs_end
        )
        compare_resolved = verify_compare_overlay_records(
            records, slot_start, slot_end, stubs_start, stubs_end
        )
        callback_body = type_init_resolved[STATIC_UNICODE_CALLBACK][1]
        verify_static_unicode_hot_edge_records(
            type_init_resolved,
            compare_resolved,
            _symbol_code(elf, callback_body),
        )
        hot_edge_resolved = resolve_inlined_hot_edge_records(
            records,
            map_symbols,
            slot_start,
            slot_end,
            stubs_start,
            stubs_end,
        )
        verify_inlined_hot_edge_records(
            hot_edge_resolved,
            {
                caller: _symbol_code(
                    elf, hot_edge_resolved[caller].body
                )
                for caller in (
                    TUPLE_ITER_HOT_EDGE_CALLER,
                    *MARSHAL_LIST_HOT_EDGE_CALLERS,
                )
            },
        )

    return slot_start, stubs_start, stubs_end, resolved, frozen_startup_data


def _format_symbol(symbol: SymbolRecord) -> str:
    end = symbol.address + symbol.size
    return f"{symbol.name}=0x{symbol.address:x}-0x{end:x}@{symbol.section}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "elf",
        type=pathlib.Path,
        help="unstripped pre-filter P2 ELF (normally nuttx.full)",
    )
    parser.add_argument(
        "--map",
        dest="map_path",
        type=pathlib.Path,
        help="LLD linker map (defaults to nuttx.map beside the ELF)",
    )
    args = parser.parse_args(argv)

    try:
        (
            slot_start,
            stubs_start,
            stubs_end,
            resolved,
            frozen_startup_data,
        ) = verify(args.elf, args.map_path)
    except (ELFError, OSError, VerificationError, ValueError) as exc:
        print(f"ERROR: P2 Python residency verification failed: {exc}", file=sys.stderr)
        return 1

    print("P2 Python residency verification: PASS")
    print(f"overlay_slot_start=0x{slot_start:x}")
    print(f"overlay_stubs=0x{stubs_start:x}-0x{stubs_end:x}")
    for name in FROZEN_STARTUP_DATA_SYMBOLS:
        symbol = frozen_startup_data[name]
        print(
            f"frozen:{name}:address=0x{symbol.address:x}:"
            f"size={symbol.size}:section={symbol.section}"
        )
    for requirement in REQUIREMENTS:
        symbols = resolved[requirement.logical_name]
        inventory = (
            ",".join(_format_symbol(symbol) for symbol in symbols)
            if symbols
            else "N/A:not-linked"
        )
        print(f"{requirement.category}:{requirement.logical_name}:{inventory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
