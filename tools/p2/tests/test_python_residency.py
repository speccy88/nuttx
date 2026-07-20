#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Tests for the P2 Python resident-path ELF verifier."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/p2/verify-python-residency.py"
BUILD_WRAPPER = ROOT / "tools/p2/build.sh"
SPEC = importlib.util.spec_from_file_location("p2_python_residency", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
residency = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = residency
SPEC.loader.exec_module(residency)


class PythonResidencyTests(unittest.TestCase):
    STUBS_START = 0x40000
    STUBS_END = 0x50000
    SLOT_START = 0x66000
    SLOT_END = 0x7C000
    TYPE_INIT_SECTION_SIZE = 0x15EF0
    GC_TRAVERSAL_SECTION_SIZE = 0x374C
    CODE_INIT_SECTION_SIZE = 0x01B8
    COMPARE_SECTION_SIZE = 0x0964
    HOT_EDGE_GROUPS = {
        "PyIter_Next": "113",
        "tupleiter_next": "7",
        "r_ref": "205",
        "r_ref_reserve": "3",
        "PyList_Append": "88",
        "_PyList_AppendTakeRefListResize": "17",
    }

    CPYTHON_RESIDENT_REQUIREMENTS = (
        "_PyMem_RawMalloc",
        "_PyMem_RawCalloc",
        "_PyMem_RawRealloc",
        "_PyMem_RawFree",
        "set_default_allocator_unlocked",
        "_PyMem_SetDefaultAllocator",
        "PyMem_SetAllocator",
        "PyMem_Malloc",
        "PyMem_Calloc",
        "PyMem_Realloc",
        "PyMem_Free",
        "PyObject_Malloc",
        "PyObject_Calloc",
        "PyObject_Realloc",
        "PyObject_Free",
        "PyMem_RawMalloc",
        "PyMem_RawCalloc",
        "PyMem_RawRealloc",
        "PyMem_RawFree",
        "_PyMem_RawWcsdup",
        "_PyMem_RawStrdup",
        "PyThread_get_thread_ident_ex",
        "PyThread_get_thread_ident",
        "PyThread_allocate_lock",
        "PyThread_free_lock",
        "PyThread_tss_alloc",
        "PyThread_tss_free",
        "PyThread_tss_is_created",
        "PyThread_tss_create",
        "PyThread_tss_delete",
        "PyThread_tss_set",
        "PyThread_tss_get",
    )

    ALLOCATOR_FAST_FRONTENDS = (
        "PyMem_Malloc",
        "PyMem_Calloc",
        "PyMem_Realloc",
        "PyMem_Free",
        "PyObject_Malloc",
        "PyObject_Calloc",
        "PyObject_Realloc",
        "PyObject_Free",
    )

    STARTUP_HOT_RESIDENT_REQUIREMENTS = (
        "_PyOS_GetOpt",
        "_PyOS_ResetGetOpt",
        "PyOS_snprintf",
        "PyOS_vsnprintf",
        "_PyThreadState_MustExit",
        "_PyThreadState_GetCurrent",
        "_Py_Dealloc",
        "PyErr_Occurred",
        "_PyType_InitCache",
        "_PyObject_SetDeferredRefcount",
        "_Py_NewReference",
        "PyObject_IS_GC",
        "_PyUnicode_InternStatic",
        "_Py_hashtable_get",
        "_Py_hashtable_get_entry_generic",
        "_Py_hashtable_set",
        "hashtable_rehash",
    )

    TYPE_INIT_OVERLAY_CORE_FUNCTIONS = (
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

    TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS = (
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

    TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS = (
        "_PyUnicode_InitGlobalObjects",
        "_PyUnicode_InitStaticStrings",
        "hashtable_unicode_hash",
        "hashtable_unicode_compare",
        "_PyUnicode_ExactDealloc",
        "unicode_dealloc",
    )

    TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS = (
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

    TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS = (
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

    TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS = (
        "_PyUnicode_InternImmortal",
        "_Py_SetImmortal",
        "_Py_SetImmortalUntracked",
    )

    TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS = (
        "intern_strings",
    )

    TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS = (
        "update_one_slot",
        "intern_constants",
    )

    TYPE_INIT_OVERLAY_FUNCTIONS = (
        TYPE_INIT_OVERLAY_CORE_FUNCTIONS
        + TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
        + TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
        + TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
        + TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS
        + TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS
        + TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS
        + TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS
    )

    GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS = (
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

    GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS = (
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

    GC_TRAVERSAL_OVERLAY_FUNCTIONS = (
        GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS
        + GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS
    )

    CODE_INIT_OVERLAY_FUNCTIONS = (
        "_PyCode_Quicken",
        "_Py_GetBaseOpcode",
    )

    COMPARE_OVERLAY_FUNCTIONS = (
        "PyObject_RichCompareBool",
        "PyObject_RichCompare",
        "unicode_compare_eq",
        "PyUnicode_RichCompare",
    )

    TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS = (
        "_PyRuntime_Early_Init",
        "_PyRuntime_Initialize",
        "_Py_PreInitializeFromConfig",
        "Py_InitializeFromConfig",
        "_Py_InitializeMain",
        "pycore_interp_init",
        "pycore_init_types",
        "init_interp_main",
    )

    def _resident_symbols(self):
        return [
            residency.SymbolRecord(
                name=requirement.aliases[0],
                address=0x1000 + index * 0x100,
                size=0x80,
                section=".text",
                executable=True,
            )
            for index, requirement in enumerate(residency.REQUIREMENTS)
        ]

    def _frozen_startup_data_symbols(self):
        return [
            residency.SymbolRecord(
                name=name,
                address=residency.P2_XMEM_START + 0x1000 + index * 0x1000,
                size=0x100,
                section=residency.P2_XDATA_OUTPUT_SECTION,
                executable=False,
                symbol_type="STT_OBJECT",
                section_type="SHT_PROGBITS",
                allocatable=True,
            )
            for index, name in enumerate(
                residency.FROZEN_STARTUP_DATA_SYMBOLS
            )
        ]

    def _frozen_startup_data_map_symbols(self):
        return (
            residency.MapSymbolRecord(
                name=(
                    "libpython3.13.a(frozen.o):"
                    f"({residency.FROZEN_STARTUP_DATA_INPUT_SECTION})"
                ),
                address=residency.P2_XMEM_START + 0x1000,
                size=0x3000,
            ),
        )

    def _verify_records(self, symbols):
        return residency.verify_resident_records(
            symbols,
            self.SLOT_START,
            self.STUBS_START,
            self.STUBS_END,
        )

    def _type_init_symbols(self):
        symbols = []
        for index, name in enumerate(residency.TYPE_INIT_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                        section_index=7,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.00000007.{name}",
                        address=self.SLOT_START + 4 + index * 0x100,
                        size=0x80,
                        section=residency.TYPE_INIT_OVERLAY_SECTION,
                        executable=True,
                        section_index=19,
                        section_address=self.SLOT_START,
                        section_size=self.TYPE_INIT_SECTION_SIZE,
                    ),
                )
            )
        return symbols

    def _gc_traversal_symbols(self):
        symbols = []
        for index, name in enumerate(residency.GC_TRAVERSAL_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                        section_index=7,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.00000008.{name}",
                        address=self.SLOT_START + 4 + index * 0x80,
                        size=0x40,
                        section=residency.GC_TRAVERSAL_OVERLAY_SECTION,
                        executable=True,
                        section_index=20,
                        section_address=self.SLOT_START,
                        section_size=self.GC_TRAVERSAL_SECTION_SIZE,
                    ),
                )
            )
        return symbols

    def _code_init_symbols(self):
        symbols = []
        for index, name in enumerate(residency.CODE_INIT_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                        section_index=7,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.00000009.{name}",
                        address=self.SLOT_START + 4 + index * 0x100,
                        size=0x80,
                        section=residency.CODE_INIT_OVERLAY_SECTION,
                        executable=True,
                        section_index=21,
                        section_address=self.SLOT_START,
                        section_size=self.CODE_INIT_SECTION_SIZE,
                    ),
                )
            )
        return symbols

    def _compare_symbols(self):
        symbols = []
        for index, name in enumerate(residency.COMPARE_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                        section_index=7,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.0000000a.{name}",
                        address=self.SLOT_START + 4 + index * 0x200,
                        size=0x100,
                        section=residency.COMPARE_OVERLAY_SECTION,
                        executable=True,
                        section_index=22,
                        section_address=self.SLOT_START,
                        section_size=self.COMPARE_SECTION_SIZE,
                    ),
                )
            )
        return symbols

    def _hot_edge_symbols_and_map(self):
        symbols = []
        map_symbols = []
        for index, name in enumerate(residency.INLINED_HOT_EDGE_FUNCTIONS):
            group = self.HOT_EDGE_GROUPS[name]
            stub = residency.SymbolRecord(
                name=name,
                address=self.STUBS_START + 0x400 + index * 4,
                size=4,
                section=residency.OVERLAY_STUB_SECTION,
                executable=True,
                section_index=7,
            )
            body = residency.SymbolRecord(
                name=f"__p2_ovlbody.{group}.{name}",
                address=self.SLOT_START + 0x100 + index * 0x80,
                size=0x40,
                section=f".p2.overlay.group.dynamic-{group}",
                executable=True,
                section_index=30 + index,
                section_address=self.SLOT_START,
                section_size=0x1000,
            )
            symbols.extend((stub, body))
            map_symbols.extend(
                (
                    residency.MapSymbolRecord(
                        name=stub.name,
                        address=stub.address,
                        size=stub.size,
                    ),
                    residency.MapSymbolRecord(
                        name=body.name,
                        address=body.address,
                        size=body.size,
                    ),
                )
            )
        return symbols, map_symbols

    @staticmethod
    def _calla_word(target, condition=0xF):
        return (
            condition << 28
            | residency.P2_CALLA_IMMEDIATE_OPCODE
            | target
        ).to_bytes(4, "little")

    def _hot_edge_code(self, resolved):
        code = {
            caller: bytearray(resolved[caller].body.size)
            for caller in (
                residency.TUPLE_ITER_HOT_EDGE_CALLER,
                *residency.MARSHAL_LIST_HOT_EDGE_CALLERS,
            )
        }
        resize_stub = resolved[residency.MARSHAL_LIST_RESIZE_TARGET].stub
        for caller in residency.MARSHAL_LIST_HOT_EDGE_CALLERS:
            code[caller][8:12] = self._calla_word(resize_stub.address)
        return {name: bytes(body) for name, body in code.items()}

    def _linked_fixture(
        self,
        overlay_python_main=False,
        stubbed_requirement=None,
        utf8_wrong_group=False,
        type_init_wrong_group=False,
        gc_traversal_wrong_group=False,
        compare_wrong_group=False,
        compare_unexpected_body=False,
        static_unicode_cross_call=False,
        xdata_noload=False,
        xdata_allocatable=True,
    ):
        toolchain = pathlib.Path(os.environ.get("P2LLVM_ROOT", ""))
        clang = toolchain / "bin/clang"
        linker = toolchain / "bin/ld.lld"
        if not clang.is_file() or not linker.is_file():
            self.skipTest("P2LLVM_ROOT does not select a complete P2 toolchain")

        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        stubbed_name = (
            stubbed_requirement[0]
            if stubbed_requirement is not None
            else None
        )
        functions = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(requirement.aliases[0])
            for requirement in residency.REQUIREMENTS
            if requirement.aliases[0] != stubbed_name
        )
        utf8_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.UTF8_OVERLAY_FUNCTIONS
        )
        utf8_bodies = "\n".join(
            "  .section .p2.overlay.body.000000{1:02x},\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.00000006.{0}\n"
            "  .type __p2_ovlbody.00000006.{0},@function\n"
            "__p2_ovlbody.00000006.{0}:\n"
            "  nop\n"
            "  nop\n"
            "  .size __p2_ovlbody.00000006.{0}, "
            ".-__p2_ovlbody.00000006.{0}".format(
                name,
                7
                if utf8_wrong_group
                and name == "ucs4lib_utf8_decode"
                else 6,
            )
            for name in residency.UTF8_OVERLAY_FUNCTIONS
        )
        type_init_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.TYPE_INIT_OVERLAY_FUNCTIONS
        )
        type_init_bodies = "\n".join(
            "  .section .p2.overlay.body.000000{1:02x},\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.00000007.{0}\n"
            "  .type __p2_ovlbody.00000007.{0},@function\n"
            "__p2_ovlbody.00000007.{0}:\n"
            "{2}"
            "  .size __p2_ovlbody.00000007.{0}, "
            ".-__p2_ovlbody.00000007.{0}".format(
                name,
                8
                if type_init_wrong_group and name == "PyType_Ready"
                else 7,
                "  calla #\\unicode_compare_eq\n  nop\n"
                if static_unicode_cross_call
                and name == residency.STATIC_UNICODE_CALLBACK
                else "  nop\n  nop\n",
            )
            for name in residency.TYPE_INIT_OVERLAY_FUNCTIONS
        )
        gc_traversal_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.GC_TRAVERSAL_OVERLAY_FUNCTIONS
        )
        gc_traversal_bodies = "\n".join(
            "  .section .p2.overlay.body.000000{1:02x},\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.00000008.{0}\n"
            "  .type __p2_ovlbody.00000008.{0},@function\n"
            "__p2_ovlbody.00000008.{0}:\n"
            "  nop\n"
            "  nop\n"
            "  .size __p2_ovlbody.00000008.{0}, "
            ".-__p2_ovlbody.00000008.{0}".format(
                name,
                9
                if gc_traversal_wrong_group and name == "visit_reachable"
                else 8,
            )
            for name in residency.GC_TRAVERSAL_OVERLAY_FUNCTIONS
        )
        code_init_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.CODE_INIT_OVERLAY_FUNCTIONS
        )
        code_init_bodies = "\n".join(
            "  .section .p2.overlay.body.00000009,\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.00000009.{0}\n"
            "  .type __p2_ovlbody.00000009.{0},@function\n"
            "__p2_ovlbody.00000009.{0}:\n"
            "  nop\n"
            "  nop\n"
            "  .size __p2_ovlbody.00000009.{0}, "
            ".-__p2_ovlbody.00000009.{0}".format(name)
            for name in residency.CODE_INIT_OVERLAY_FUNCTIONS
        )
        compare_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.COMPARE_OVERLAY_FUNCTIONS
        )
        compare_bodies = "\n".join(
            "  .section .p2.overlay.body.000000{1:02x},\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.0000000a.{0}\n"
            "  .type __p2_ovlbody.0000000a.{0},@function\n"
            "__p2_ovlbody.0000000a.{0}:\n"
            "  nop\n"
            "  nop\n"
            "  .size __p2_ovlbody.0000000a.{0}, "
            ".-__p2_ovlbody.0000000a.{0}".format(
                name,
                11
                if compare_wrong_group and name == "PyUnicode_RichCompare"
                else 10,
            )
            for name in residency.COMPARE_OVERLAY_FUNCTIONS
        )
        if compare_unexpected_body:
            compare_bodies += (
                "\n  .section .p2.overlay.body.0000000a,\"ax\",@progbits\n"
                "  .globl __p2_ovlbody.0000000a.unexpected_compare\n"
                "  .type __p2_ovlbody.0000000a.unexpected_compare,@function\n"
                "__p2_ovlbody.0000000a.unexpected_compare:\n"
                "  nop\n"
                "  nop\n"
                "  .size __p2_ovlbody.0000000a.unexpected_compare, "
                ".-__p2_ovlbody.0000000a.unexpected_compare\n"
            )
        linked_hot_groups = {
            name: 41 + index
            for index, name in enumerate(residency.INLINED_HOT_EDGE_FUNCTIONS)
        }
        hot_edge_stubs = "\n".join(
            "  .globl {0}\n"
            "  .type {0},@function\n"
            "{0}:\n"
            "  nop\n"
            "  .size {0}, .-{0}".format(name)
            for name in residency.INLINED_HOT_EDGE_FUNCTIONS
        )
        hot_edge_bodies = "\n".join(
            "  .section .p2.overlay.body.{1:08x},\"ax\",@progbits\n"
            "  .globl __p2_ovlbody.{1}.{0}\n"
            "  .type __p2_ovlbody.{1}.{0},@function\n"
            "__p2_ovlbody.{1}.{0}:\n"
            "{2}"
            "  .size __p2_ovlbody.{1}.{0}, "
            ".-__p2_ovlbody.{1}.{0}".format(
                name,
                linked_hot_groups[name],
                (
                    "  calla #\\_PyList_AppendTakeRefListResize\n"
                    "  nop\n"
                    if name in residency.MARSHAL_LIST_HOT_EDGE_CALLERS
                    else "  nop\n  nop\n"
                ),
            )
            for name in residency.INLINED_HOT_EDGE_FUNCTIONS
        )
        hot_edge_linker_sections = "".join(
            "    .p2.overlay.group.{0:08x}\n"
            "      {{ *(.p2.overlay.body.{0:08x}) }}\n".format(group)
            for group in linked_hot_groups.values()
        )
        functions += (
            '\n  .section .p2.overlay.stubs,"ax",@progbits\n'
            + utf8_stubs
            + "\n"
            + type_init_stubs
            + "\n"
            + gc_traversal_stubs
            + "\n"
            + code_init_stubs
            + "\n"
            + compare_stubs
            + "\n"
            + hot_edge_stubs
            + '\n  .section .p2.overlay.header.00000006,"ax",@progbits\n'
            + "  .long 0\n"
            + '  .section .p2.overlay.header.00000007,"ax",@progbits\n'
            + "  .long 0\n"
            + '  .section .p2.overlay.header.00000008,"ax",@progbits\n'
            + "  .long 0\n"
            + '  .section .p2.overlay.header.00000009,"ax",@progbits\n'
            + "  .long 0\n"
            + '  .section .p2.overlay.header.0000000a,"ax",@progbits\n'
            + "  .long 0\n"
            + "\n"
            + utf8_bodies
            + "\n"
            + type_init_bodies
            + "\n"
            + gc_traversal_bodies
            + "\n"
            + code_init_bodies
            + "\n"
            + compare_bodies
            + "\n"
            + hot_edge_bodies
        )
        functions += (
            '\n  .section .p2.xdata.ro,"{}",@progbits\n'.format(
                "a" if xdata_allocatable else ""
            )
            + "\n".join(
                "  .globl {0}\n"
                "  .type {0},@object\n"
                "{0}:\n"
                "  .byte 0x01\n"
                "  .size {0}, .-{0}".format(name)
                for name in residency.FROZEN_STARTUP_DATA_SYMBOLS
            )
        )
        if stubbed_requirement is not None:
            name, offset = stubbed_requirement
            functions += (
                "\n  .section .misclassified_stubs,\"ax\",@progbits\n"
                f"  .org {offset}\n"
                f"  .globl {name}\n"
                f"  .type {name},@function\n"
                f"{name}:\n"
                "  nop\n"
                f"  .size {name}, .-{name}\n"
            )
        if overlay_python_main:
            functions += (
                "\n  .section .p2.overlay.body.00000007,\"ax\",@progbits\n"
                "  .globl __p2_ovlbody.00000007.python_main\n"
                "  .type __p2_ovlbody.00000007.python_main,@function\n"
                "__p2_ovlbody.00000007.python_main:\n"
                "  nop\n"
                "  .size __p2_ovlbody.00000007.python_main, "
                ".-__p2_ovlbody.00000007.python_main\n"
            )

        source = root / "fixture.S"
        script = root / "fixture.ld"
        obj = root / "fixture.o"
        elf = root / "fixture.elf"
        map_path = root / "fixture.map"
        source.write_text(
            '  .section .text,"ax",@progbits\n' + functions + "\n",
            encoding="utf-8",
        )
        xdata_output = (
            "  .p2.xdata 0x10000000{} : ".format(
                " (NOLOAD)" if xdata_noload else ""
            )
            + "{ *(.p2.xdata.ro) }\n"
        )
        script.write_text(
            "SECTIONS\n"
            "{\n"
            "  .text 0xa00 : { *(.text .text.*) }\n"
            "  __p2_overlay_stubs_start = 0x40000;\n"
            "  .misclassified_stubs 0x40000 : { *(.misclassified_stubs) }\n"
            "  .p2.overlay.stubs 0x48000 : { *(.p2.overlay.stubs) }\n"
            "  __p2_overlay_stubs_end = 0x50000;\n"
            "  __p2_overlay_slot_start = 0x66000;\n"
            "  __p2_overlay_slot_end = 0x7c000;\n"
            + xdata_output
            + "  OVERLAY 0x66000 : AT (0x80000)\n"
            "  {\n"
            "    .p2.overlay.group.00000006\n"
            "      { *(.p2.overlay.header.00000006) "
            "*(.p2.overlay.body.00000006) }\n"
            "    .p2.overlay.group.00000007\n"
            "      { *(.p2.overlay.header.00000007) "
            "*(.p2.overlay.body.00000007) }\n"
            "    .p2.overlay.group.00000008\n"
            "      { *(.p2.overlay.header.00000008) "
            "*(.p2.overlay.body.00000008) }\n"
            "    .p2.overlay.group.00000009\n"
            "      { *(.p2.overlay.header.00000009) "
            "*(.p2.overlay.body.00000009) }\n"
            "    .p2.overlay.group.0000000a\n"
            "      { *(.p2.overlay.header.0000000a) "
            "*(.p2.overlay.body.0000000a) }\n"
            + hot_edge_linker_sections
            + "  }\n"
            + "}\n",
            encoding="utf-8",
        )
        subprocess.run(
            [str(clang), "--target=p2", "-c", str(source), "-o", str(obj)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                str(linker),
                "-T",
                str(script),
                str(obj),
                "-o",
                str(elf),
                f"-Map={map_path}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return temporary, elf

    def test_recognizes_overlay_bodies_and_compiler_clone_suffixes(self):
        cases = (
            ("python_main", "python_main", False),
            ("python_launcher_main.cold.2", "python_launcher_main", False),
            (
                "__p2_ovlbody.0000002a.python_main.constprop.4.cold",
                "python_main",
                True,
            ),
            (
                "__p2_overlay_body.17.p2_uart_send.llvm.ABC123",
                "p2_uart_send",
                True,
            ),
            ("stdoutstream_putc.12", "stdoutstream_putc", False),
        )
        for name, logical, body in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    residency.logical_symbol_name(name), (logical, body)
                )

    def test_inlined_hot_edges_resolve_dynamic_map_groups_and_pass(self):
        symbols, expected_map = self._hot_edge_symbols_and_map()
        map_text = (
            "     VMA      LMA     Size Align Out     In      Symbol\n"
            + "\n".join(
                f"{record.address:x} {record.address:x} {record.size:x} "
                f"1 {record.name}"
                for record in expected_map
            )
            + "\n"
        )
        parsed = residency.parse_link_map_symbols(map_text)
        self.assertEqual(parsed, tuple(expected_map))

        resolved = residency.resolve_inlined_hot_edge_records(
            symbols,
            parsed,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), residency.INLINED_HOT_EDGE_FUNCTIONS)
        self.assertEqual(
            {name: implementation.group for name, implementation in resolved.items()},
            self.HOT_EDGE_GROUPS,
        )
        for record in expected_map[::2]:
            with self.subTest(stub=record.name):
                self.assertEqual(
                    resolved[record.name].stub.address, record.address
                )

        residency.verify_inlined_hot_edge_records(
            resolved, self._hot_edge_code(resolved)
        )

    def test_inlined_tuple_hot_edge_rejects_target_veneer_calla(self):
        symbols, map_symbols = self._hot_edge_symbols_and_map()
        resolved = residency.resolve_inlined_hot_edge_records(
            symbols,
            map_symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        code = self._hot_edge_code(resolved)
        caller = residency.TUPLE_ITER_HOT_EDGE_CALLER
        target = resolved[residency.TUPLE_ITER_HOT_EDGE_TARGET].stub
        bad_body = bytearray(code[caller])
        bad_body[12:16] = self._calla_word(target.address, condition=0x5)
        code[caller] = bytes(bad_body)

        with self.assertRaisesRegex(
            residency.VerificationError,
            r"patch-0048 caller PyIter_Next retains a direct CALLA to "
            r"tupleiter_next overlay veneer .* at body\+0xc",
        ):
            residency.verify_inlined_hot_edge_records(resolved, code)

    def test_inlined_marshal_hot_edges_reject_append_veneer_calla(self):
        symbols, map_symbols = self._hot_edge_symbols_and_map()
        resolved = residency.resolve_inlined_hot_edge_records(
            symbols,
            map_symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        append_stub = resolved[residency.MARSHAL_LIST_APPEND_TARGET].stub

        for caller in residency.MARSHAL_LIST_HOT_EDGE_CALLERS:
            with self.subTest(caller=caller):
                code = self._hot_edge_code(resolved)
                bad_body = bytearray(code[caller])
                bad_body[20:24] = self._calla_word(append_stub.address)
                code[caller] = bytes(bad_body)
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"patch-0050 caller {caller} retains a direct CALLA to "
                    r"PyList_Append overlay veneer .* at body\+0x14",
                ):
                    residency.verify_inlined_hot_edge_records(resolved, code)

    def test_inlined_marshal_hot_edges_require_one_resize_veneer_calla(self):
        symbols, map_symbols = self._hot_edge_symbols_and_map()
        resolved = residency.resolve_inlined_hot_edge_records(
            symbols,
            map_symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        resize_stub = resolved[residency.MARSHAL_LIST_RESIZE_TARGET].stub

        for caller in residency.MARSHAL_LIST_HOT_EDGE_CALLERS:
            for count in (0, 2):
                with self.subTest(caller=caller, count=count):
                    code = self._hot_edge_code(resolved)
                    bad_body = bytearray(code[caller])
                    if count == 0:
                        bad_body[8:12] = b"\0" * 4
                    else:
                        bad_body[20:24] = self._calla_word(
                            resize_stub.address, condition=0x6
                        )
                    code[caller] = bytes(bad_body)
                    with self.assertRaisesRegex(
                        residency.VerificationError,
                        rf"patch-0050 caller {caller} has {count} immediate "
                        r"CALLA instructions to "
                        r"_PyList_AppendTakeRefListResize overlay veneer .*"
                        r"expected exactly one",
                    ):
                        residency.verify_inlined_hot_edge_records(resolved, code)

    def test_inlined_hot_edge_map_resolution_fails_closed_on_duplicate_body(self):
        symbols, map_symbols = self._hot_edge_symbols_and_map()
        caller = residency.TUPLE_ITER_HOT_EDGE_CALLER
        body = next(
            record
            for record in map_symbols
            if residency.logical_symbol_name(record.name) == (caller, True)
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"hot-edge function PyIter_Next has 2 overlay execution bodies "
            r"in the linker map",
        ):
            residency.resolve_inlined_hot_edge_records(
                symbols,
                (*map_symbols, body),
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_accepts_complete_resident_inventory(self):
        resolved = self._verify_records(self._resident_symbols())
        self.assertEqual(set(resolved), {
            requirement.logical_name
            for requirement in residency.REQUIREMENTS
        })
        self.assertIn("python_main", resolved)
        self.assertIn("p2_uart_send", resolved)

    def test_accepts_selective_frozen_startup_data_in_external_psram(self):
        resolved = residency.verify_frozen_startup_data_records(
            self._frozen_startup_data_symbols(),
            self._frozen_startup_data_map_symbols(),
        )
        self.assertEqual(
            tuple(resolved), residency.FROZEN_STARTUP_DATA_SYMBOLS
        )
        for name, symbol in resolved.items():
            with self.subTest(name=name):
                self.assertEqual(symbol.symbol_type, "STT_OBJECT")
                self.assertGreater(symbol.size, 0)
                self.assertEqual(
                    symbol.section, residency.P2_XDATA_OUTPUT_SECTION
                )
                self.assertFalse(symbol.executable)
                self.assertEqual(symbol.section_type, "SHT_PROGBITS")
                self.assertTrue(symbol.allocatable)
                self.assertGreaterEqual(symbol.address, residency.P2_XMEM_START)
                self.assertLessEqual(
                    symbol.address + symbol.size, residency.P2_XMEM_END
                )

    def test_selective_frozen_startup_data_requires_each_unique_object(self):
        symbols = self._frozen_startup_data_symbols()
        for name in residency.FROZEN_STARTUP_DATA_SYMBOLS:
            with self.subTest(name=name, failure="missing"):
                missing = [symbol for symbol in symbols if symbol.name != name]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"{re.escape(name)} has 0 defined symbols; expected "
                    r"exactly one frozen startup data object",
                ):
                    residency.verify_frozen_startup_data_records(
                        missing, self._frozen_startup_data_map_symbols()
                    )

            with self.subTest(name=name, failure="duplicate"):
                duplicate = [
                    *symbols,
                    replace(
                        next(symbol for symbol in symbols if symbol.name == name),
                        address=residency.P2_XMEM_START + 0x8000,
                    ),
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"{re.escape(name)} has 2 defined symbols; expected "
                    r"exactly one frozen startup data object",
                ):
                    residency.verify_frozen_startup_data_records(
                        duplicate, self._frozen_startup_data_map_symbols()
                    )

    def test_rejects_malformed_or_misplaced_frozen_startup_data(self):
        symbols = self._frozen_startup_data_symbols()
        target = symbols[0]
        cases = (
            (
                "empty",
                replace(target, size=0),
                r"_Py_M__encodings is empty; .* must be nonempty",
            ),
            (
                "not-object",
                replace(target, symbol_type="STT_FUNC"),
                r"_Py_M__encodings is STT_FUNC, not STT_OBJECT",
            ),
            (
                "wrong-section",
                replace(target, section=".rodata"),
                r"_Py_M__encodings is in \.rodata, not \.p2\.xdata",
            ),
            (
                "executable-section",
                replace(target, executable=True),
                r"_Py_M__encodings is in executable section \.p2\.xdata",
            ),
            (
                "zero-fill-section",
                replace(target, section_type="SHT_NOBITS"),
                r"_Py_M__encodings is backed by SHT_NOBITS, not initialized "
                r"SHT_PROGBITS",
            ),
            (
                "not-allocatable",
                replace(target, allocatable=False),
                r"_Py_M__encodings section \.p2\.xdata is not SHF_ALLOC",
            ),
            (
                "hub-address",
                replace(target, address=0x1000),
                r"_Py_M__encodings range .* is not wholly within external "
                r"PSRAM range",
            ),
            (
                "crosses-xmem-end",
                replace(
                    target,
                    address=residency.P2_XMEM_END - 0x80,
                    size=0x100,
                ),
                r"_Py_M__encodings range .* is not wholly within external "
                r"PSRAM range",
            ),
        )
        for failure, malformed, error in cases:
            with self.subTest(failure=failure):
                candidate = [malformed, *symbols[1:]]
                with self.assertRaisesRegex(
                    residency.VerificationError, error
                ):
                    residency.verify_frozen_startup_data_records(
                        candidate, self._frozen_startup_data_map_symbols()
                    )

    def test_frozen_startup_data_requires_const_input_provenance(self):
        symbols = self._frozen_startup_data_symbols()
        nonconst_input = (
            replace(
                self._frozen_startup_data_map_symbols()[0],
                name="libpython3.13.a(frozen.o):(.p2.xdata)",
            ),
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"_Py_M__encodings has 0 containing \.p2\.xdata\.ro "
            r"linker-map input ranges; expected exactly one",
        ):
            residency.verify_frozen_startup_data_records(
                symbols, nonconst_input
            )

    def test_fatal_xmem_diagnostic_path_is_mandatory_and_resident(self):
        requirements = tuple(
            requirement
            for requirement in residency.REQUIREMENTS
            if requirement.category == "xmem"
        )
        self.assertEqual(
            tuple(requirement.logical_name for requirement in requirements),
            ("__p2_xmem_fault",),
        )

        symbols = [
            symbol
            for symbol in self._resident_symbols()
            if symbol.name != "__p2_xmem_fault"
        ]
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"missing resident xmem symbol __p2_xmem_fault",
        ):
            self._verify_records(symbols)

        symbols = self._resident_symbols()
        symbols.append(
            residency.SymbolRecord(
                name="__p2_ovlbody.0.__p2_xmem_fault",
                address=self.SLOT_START + 4,
                size=0x40,
                section=".p2.overlay.group.00000007",
                executable=True,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"__p2_xmem_fault has overlay execution body __p2_ovlbody",
        ):
            self._verify_records(symbols)

    def test_cpython_resident_contract_is_exact_and_unaliased(self):
        requirements = tuple(
            requirement
            for requirement in residency.REQUIREMENTS
            if requirement.category in ("allocator", "thread")
        )
        self.assertEqual(
            tuple(requirement.logical_name for requirement in requirements),
            self.CPYTHON_RESIDENT_REQUIREMENTS,
        )
        self.assertTrue(
            all(
                requirement.aliases == (requirement.logical_name,)
                for requirement in requirements
            )
        )
        self.assertEqual(
            {
                requirement.logical_name
                for requirement in requirements
                if requirement.if_linked
            },
            {"PyThread_tss_alloc", "PyThread_tss_free"},
        )
        by_name = {
            requirement.logical_name: requirement
            for requirement in requirements
        }
        self.assertTrue(
            all(
                not by_name[name].if_linked
                for name in self.ALLOCATOR_FAST_FRONTENDS
            )
        )

    def test_startup_hot_resident_contract_is_atomic(self):
        requirements = tuple(
            requirement
            for requirement in residency.REQUIREMENTS
            if requirement.category == "startup"
        )
        self.assertEqual(
            tuple(requirement.logical_name for requirement in requirements),
            self.STARTUP_HOT_RESIDENT_REQUIREMENTS,
        )
        self.assertTrue(all(not requirement.if_linked for requirement in requirements))
        self.assertTrue(
            all(
                requirement.aliases == (requirement.logical_name,)
                for requirement in requirements
            )
        )
        self.assertEqual(
            {
                requirement.logical_name
                for requirement in requirements
                if requirement.allow_trivial_body
            },
            {"_PyType_InitCache", "_PyObject_SetDeferredRefcount"},
        )

    def test_resident_p2_startup_noops_may_be_four_bytes(self):
        for name in ("_PyType_InitCache", "_PyObject_SetDeferredRefcount"):
            with self.subTest(name=name):
                symbols = self._resident_symbols()
                target = next(
                    index
                    for index, symbol in enumerate(symbols)
                    if symbol.name == name
                )
                symbols[target] = residency.SymbolRecord(
                    name=name,
                    address=symbols[target].address,
                    size=4,
                    section=".text",
                    executable=True,
                )
                resolved = self._verify_records(symbols)
                self.assertEqual(resolved[name][0].size, 4)

    def test_each_startup_hot_symbol_is_mandatory_and_resident(self):
        for name in self.STARTUP_HOT_RESIDENT_REQUIREMENTS:
            with self.subTest(name=name, failure="missing"):
                symbols = [
                    symbol
                    for symbol in self._resident_symbols()
                    if symbol.name != name
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"missing resident startup symbol {name}",
                ):
                    self._verify_records(symbols)

            with self.subTest(name=name, failure="overlay body"):
                symbols = self._resident_symbols()
                target = next(
                    index
                    for index, symbol in enumerate(symbols)
                    if symbol.name == name
                )
                symbols[target] = residency.SymbolRecord(
                    name=name,
                    address=self.STUBS_START + 4,
                    size=4,
                    section=residency.OVERLAY_STUB_SECTION,
                    executable=True,
                )
                symbols.append(
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.0.{name}",
                        address=self.SLOT_START + 4,
                        size=0x80,
                        section=".p2.overlay.group.00000007",
                        executable=True,
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"{name} has overlay execution body __p2_ovlbody",
                ):
                    self._verify_records(symbols)

    def test_utf8_decoder_contract_requires_one_explicit_group(self):
        symbols = []
        for index, name in enumerate(residency.UTF8_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.00000006.{name}",
                        address=self.SLOT_START + 4 + index * 0x100,
                        size=0x80,
                        section=residency.UTF8_OVERLAY_SECTION,
                        executable=True,
                    ),
                )
            )

        resolved = residency.verify_utf8_overlay_records(
            symbols,
            self.SLOT_START,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), residency.UTF8_OVERLAY_FUNCTIONS)

        inlined_locals = [
            symbol
            for symbol in symbols
            if residency.logical_symbol_name(symbol.name)[0]
            in residency.UTF8_OVERLAY_REQUIRED_FUNCTIONS
        ]
        resolved = residency.verify_utf8_overlay_records(
            inlined_locals,
            self.SLOT_START,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(
            tuple(resolved), residency.UTF8_OVERLAY_REQUIRED_FUNCTIONS
        )

        partial_optional = list(inlined_locals)
        partial_optional.append(
            residency.SymbolRecord(
                name="asciilib_utf8_decode",
                address=self.STUBS_START + 8,
                size=4,
                section=residency.OVERLAY_STUB_SECTION,
                executable=True,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"asciilib_utf8_decode has 0 execution bodies; expected one",
        ):
            residency.verify_utf8_overlay_records(
                partial_optional,
                self.SLOT_START,
                self.STUBS_START,
                self.STUBS_END,
            )

        wrong_group = list(symbols)
        target = next(
            index
            for index, symbol in enumerate(wrong_group)
            if symbol.name == "__p2_ovlbody.00000006.ucs4lib_utf8_decode"
        )
        wrong_group[target] = residency.SymbolRecord(
            name="__p2_ovlbody.00000007.ucs4lib_utf8_decode",
            address=self.SLOT_START + 4,
            size=0x80,
            section=".p2.overlay.group.00000007",
            executable=True,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"ucs4lib_utf8_decode.*not explicit group 6",
        ):
            residency.verify_utf8_overlay_records(
                wrong_group,
                self.SLOT_START,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_utf8_decoder_contract_rejects_nonfunctions_and_split_sections(self):
        symbols = []
        for index, name in enumerate(residency.UTF8_OVERLAY_FUNCTIONS):
            symbols.extend(
                (
                    residency.SymbolRecord(
                        name=name,
                        address=self.STUBS_START + index * 4,
                        size=4,
                        section=residency.OVERLAY_STUB_SECTION,
                        executable=True,
                        section_index=7,
                    ),
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.{index}.{name}",
                        address=self.SLOT_START + 4 + index * 0x100,
                        size=0x80,
                        section=residency.UTF8_OVERLAY_SECTION,
                        executable=True,
                        section_index=18,
                        section_address=self.SLOT_START,
                        section_size=0x1000,
                    ),
                )
            )

        nonfunction = list(symbols)
        target = next(
            index
            for index, symbol in enumerate(nonfunction)
            if symbol.name == "_Py_DecodeUTF8Ex"
        )
        nonfunction[target] = residency.SymbolRecord(
            name="_Py_DecodeUTF8Ex",
            address=self.STUBS_START,
            size=4,
            section=residency.OVERLAY_STUB_SECTION,
            executable=True,
            symbol_type="STT_OBJECT",
            section_index=7,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"_Py_DecodeUTF8Ex stub is STT_OBJECT, not STT_FUNC",
        ):
            residency.verify_utf8_overlay_records(
                nonfunction,
                self.SLOT_START,
                self.STUBS_START,
                self.STUBS_END,
            )

        split = list(symbols)
        target = next(
            index
            for index, symbol in enumerate(split)
            if symbol.name.endswith("ucs4lib_utf8_decode")
            and symbol.name.startswith("__p2_ovlbody")
        )
        split[target] = residency.SymbolRecord(
            name=split[target].name,
            address=split[target].address,
            size=split[target].size,
            section=split[target].section,
            executable=True,
            section_index=19,
            section_address=self.SLOT_START,
            section_size=0x1000,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"do not share one concrete group-6 section",
        ):
            residency.verify_utf8_overlay_records(
                split,
                self.SLOT_START,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_type_init_contract_requires_complete_explicit_group_7(self):
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_CORE_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS,
            self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_FUNCTIONS,
        )
        self.assertEqual(
            residency.TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS,
            self.TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS,
        )
        self.assertEqual(len(self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS), 23)
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS), 65
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS), 6
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS), 38
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS), 10
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS), 3
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS), 1
        )
        self.assertEqual(
            len(self.TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS), 2
        )
        self.assertEqual(len(self.TYPE_INIT_OVERLAY_FUNCTIONS), 148)
        self.assertEqual(len(set(self.TYPE_INIT_OVERLAY_FUNCTIONS)), 148)
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
                + self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
                + self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
                + self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
                + self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS
            )
        )
        self.assertTrue(
            set(self.TYPE_INIT_OVERLAY_IMPORTLIB_LOCALITY_FUNCTIONS).isdisjoint(
                self.TYPE_INIT_OVERLAY_CORE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_SUCCESS_PATH_HELPERS
                + self.TYPE_INIT_OVERLAY_UNICODE_BOOTSTRAP_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_DICTIONARY_HOT_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_MODULE_ATTRIBUTE_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_IMMORTAL_INTERN_FUNCTIONS
                + self.TYPE_INIT_OVERLAY_CODE_NAME_INTERN_FUNCTIONS
            )
        )
        self.assertNotIn(
            "managed_static_type_state_clear",
            self.TYPE_INIT_OVERLAY_FUNCTIONS,
        )
        symbols = self._type_init_symbols()
        resolved = residency.verify_type_init_overlay_records(
            symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), self.TYPE_INIT_OVERLAY_FUNCTIONS)

        for name in self.TYPE_INIT_OVERLAY_FUNCTIONS:
            with self.subTest(name=name, failure="missing stub"):
                missing_stub = [
                    symbol for symbol in symbols if symbol.name != name
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"type-init overlay function {name} has 0 public symbols",
                ):
                    residency.verify_type_init_overlay_records(
                        missing_stub,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

            with self.subTest(name=name, failure="missing body"):
                missing_body = [
                    symbol
                    for symbol in symbols
                    if residency.logical_symbol_name(symbol.name)
                    != (name, True)
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"type-init overlay function {name} has 0 execution bodies",
                ):
                    residency.verify_type_init_overlay_records(
                        missing_body,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_type_init_contract_rejects_whole_pylifecycle_overlay(self):
        symbols = self._type_init_symbols()
        outside_group = list(symbols)
        outside_group.append(
            residency.SymbolRecord(
                name="__p2_ovlbody.00000008.Py_InitializeFromConfig",
                address=self.SLOT_START + 4,
                size=0x80,
                section=".p2.overlay.group.00000008",
                executable=True,
                section_index=20,
                section_address=self.SLOT_START,
                section_size=0x1000,
            )
        )
        residency.verify_type_init_overlay_records(
            outside_group,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )

        for name in self.TYPE_INIT_OVERLAY_FORBIDDEN_PYLIFECYCLE_FUNCTIONS:
            with self.subTest(name=name):
                forbidden = list(symbols)
                forbidden.append(
                    residency.SymbolRecord(
                        name=f"__p2_ovlbody.999.{name}",
                        address=self.SLOT_START + 0x7F00,
                        size=0x80,
                        section=residency.TYPE_INIT_OVERLAY_SECTION,
                        executable=True,
                        section_index=19,
                        section_address=self.SLOT_START,
                        section_size=self.TYPE_INIT_SECTION_SIZE,
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"forbidden pylifecycle body .*\.{name}",
                ):
                    residency.verify_type_init_overlay_records(
                        forbidden,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_type_init_contract_rejects_duplicate_notify_event_stub(self):
        symbols = self._type_init_symbols()
        symbols.append(
            residency.SymbolRecord(
                name="_PyDict_NotifyEvent",
                address=self.STUBS_END - 4,
                size=4,
                section=residency.OVERLAY_STUB_SECTION,
                executable=True,
                section_index=7,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"type-init overlay function _PyDict_NotifyEvent has 2 public symbols",
        ):
            residency.verify_type_init_overlay_records(
                symbols,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_type_init_contract_rejects_every_unexpected_group_7_body(self):
        for name in (
            "__p2_ovlbody.999.unexpected_helper",
            "unexpected_direct_helper",
        ):
            with self.subTest(name=name):
                symbols = self._type_init_symbols()
                symbols.append(
                    residency.SymbolRecord(
                        name=name,
                        address=self.SLOT_START + 0x7F00,
                        size=0x80,
                        section=residency.TYPE_INIT_OVERLAY_SECTION,
                        executable=True,
                        section_index=19,
                        section_address=self.SLOT_START,
                        section_size=self.TYPE_INIT_SECTION_SIZE,
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"unexpected function body {re.escape(name)} at ",
                ):
                    residency.verify_type_init_overlay_records(
                        symbols,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_type_init_contract_rejects_wrong_group_and_veneers(self):
        symbols = self._type_init_symbols()
        body_index = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "__p2_ovlbody.00000007.PyType_Ready"
        )

        wrong_group = list(symbols)
        wrong_group[body_index] = residency.SymbolRecord(
            name="__p2_ovlbody.00000008.PyType_Ready",
            address=wrong_group[body_index].address,
            size=wrong_group[body_index].size,
            section=".p2.overlay.group.00000008",
            executable=True,
            section_index=20,
            section_address=self.SLOT_START,
            section_size=self.TYPE_INIT_SECTION_SIZE,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"PyType_Ready.*not explicit group 7",
        ):
            residency.verify_type_init_overlay_records(
                wrong_group,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        veneer = list(symbols)
        original = veneer[body_index]
        veneer[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address,
            size=4,
            section=original.section,
            executable=True,
            section_index=original.section_index,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"PyType_Ready.*not a substantive executable implementation",
        ):
            residency.verify_type_init_overlay_records(
                veneer,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_type_init_contract_rejects_split_unaligned_or_oversize_group(self):
        symbols = self._type_init_symbols()
        body_index = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "__p2_ovlbody.00000007.PyType_Ready"
        )
        original = symbols[body_index]

        split = list(symbols)
        split[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address,
            size=original.size,
            section=original.section,
            executable=True,
            section_index=20,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"do not share one concrete group-7 section",
        ):
            residency.verify_type_init_overlay_records(
                split,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        unaligned = list(symbols)
        unaligned[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address + 2,
            size=original.size,
            section=original.section,
            executable=True,
            section_index=original.section_index,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"PyType_Ready.*not P2 instruction aligned",
        ):
            residency.verify_type_init_overlay_records(
                unaligned,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        oversize = [
            residency.SymbolRecord(
                name=symbol.name,
                address=symbol.address,
                size=symbol.size,
                section=symbol.section,
                executable=symbol.executable,
                symbol_type=symbol.symbol_type,
                section_index=symbol.section_index,
                section_address=symbol.section_address,
                section_size=(
                    self.SLOT_END - self.SLOT_START + 4
                    if symbol.section == residency.TYPE_INIT_OVERLAY_SECTION
                    else symbol.section_size
                ),
            )
            for symbol in symbols
        ]
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"section ends at 0x7c004, beyond overlay slot 0x7c000",
        ):
            residency.verify_type_init_overlay_records(
                oversize,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_code_init_contract_requires_exact_explicit_group_9(self):
        self.assertEqual(residency.CODE_INIT_OVERLAY_GROUP, 9)
        self.assertEqual(
            residency.CODE_INIT_OVERLAY_SECTION,
            ".p2.overlay.group.00000009",
        )
        self.assertEqual(
            residency.CODE_INIT_OVERLAY_FUNCTIONS,
            self.CODE_INIT_OVERLAY_FUNCTIONS,
        )
        self.assertEqual(len(self.CODE_INIT_OVERLAY_FUNCTIONS), 2)
        self.assertEqual(self.CODE_INIT_SECTION_SIZE, 4 + 212 + 224)
        self.assertEqual(
            self.SLOT_END - self.SLOT_START - self.CODE_INIT_SECTION_SIZE,
            89672,
        )

        symbols = self._code_init_symbols()
        resolved = residency.verify_code_init_overlay_records(
            symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), self.CODE_INIT_OVERLAY_FUNCTIONS)

        for name in self.CODE_INIT_OVERLAY_FUNCTIONS:
            with self.subTest(name=name, failure="missing stub"):
                missing_stub = [
                    symbol for symbol in symbols if symbol.name != name
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"frozen-code overlay function {name} has 0 public symbols",
                ):
                    residency.verify_code_init_overlay_records(
                        missing_stub,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

            with self.subTest(name=name, failure="missing body"):
                missing_body = [
                    symbol
                    for symbol in symbols
                    if residency.logical_symbol_name(symbol.name)
                    != (name, True)
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"frozen-code overlay function {name} has 0 execution bodies",
                ):
                    residency.verify_code_init_overlay_records(
                        missing_body,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

        unexpected = list(symbols)
        unexpected.append(
            residency.SymbolRecord(
                name="__p2_ovlbody.00000009.unreviewed_quickener",
                address=self.SLOT_START + 0x400,
                size=0x40,
                section=residency.CODE_INIT_OVERLAY_SECTION,
                executable=True,
                section_index=21,
                section_address=self.SLOT_START,
                section_size=self.CODE_INIT_SECTION_SIZE,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"group 9 contains unexpected function body",
        ):
            residency.verify_code_init_overlay_records(
                unexpected,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_static_unicode_hot_edge_requires_groups_and_fixed_budget(self):
        type_init = residency.verify_type_init_overlay_records(
            self._type_init_symbols(),
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        compare = residency.verify_compare_overlay_records(
            self._compare_symbols(),
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        callback_stub, callback_body = type_init[
            residency.STATIC_UNICODE_CALLBACK
        ]
        target_stub, target_body = compare[
            residency.STATIC_UNICODE_COMPARE_TARGET
        ]
        callback_code = bytes(callback_body.size)

        self.assertEqual(residency.TYPE_INIT_OVERLAY_MAX_SIZE, 0x16000)
        self.assertEqual(
            callback_body.section, residency.TYPE_INIT_OVERLAY_SECTION
        )
        self.assertEqual(
            target_body.section, residency.COMPARE_OVERLAY_SECTION
        )
        residency.verify_static_unicode_hot_edge_records(
            type_init, compare, callback_code
        )

        at_limit = dict(type_init)
        at_limit[residency.STATIC_UNICODE_CALLBACK] = (
            callback_stub,
            replace(
                callback_body,
                section_size=residency.TYPE_INIT_OVERLAY_MAX_SIZE,
            ),
        )
        residency.verify_static_unicode_hot_edge_records(
            at_limit, compare, callback_code
        )

        oversized = dict(at_limit)
        oversized[residency.STATIC_UNICODE_CALLBACK] = (
            callback_stub,
            replace(
                callback_body,
                section_size=residency.TYPE_INIT_OVERLAY_MAX_SIZE + 4,
            ),
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"group 7 is 0x16004 bytes; patch 0047 requires at most 0x16000",
        ):
            residency.verify_static_unicode_hot_edge_records(
                oversized, compare, callback_code
            )

        wrong_callback_group = dict(type_init)
        wrong_callback_group[residency.STATIC_UNICODE_CALLBACK] = (
            callback_stub,
            replace(
                callback_body,
                section=residency.GC_TRAVERSAL_OVERLAY_SECTION,
            ),
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"callback hashtable_unicode_compare is in .*not explicit group 7",
        ):
            residency.verify_static_unicode_hot_edge_records(
                wrong_callback_group, compare, callback_code
            )

        wrong_target_group = dict(compare)
        wrong_target_group[residency.STATIC_UNICODE_COMPARE_TARGET] = (
            target_stub,
            replace(target_body, section=residency.CODE_INIT_OVERLAY_SECTION),
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"target unicode_compare_eq is in .*not explicit group 10",
        ):
            residency.verify_static_unicode_hot_edge_records(
                type_init, wrong_target_group, callback_code
            )

    def test_static_unicode_hot_edge_rejects_target_stub_calla(self):
        type_init = residency.verify_type_init_overlay_records(
            self._type_init_symbols(),
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        compare = residency.verify_compare_overlay_records(
            self._compare_symbols(),
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        callback_body = type_init[residency.STATIC_UNICODE_CALLBACK][1]
        target_stub = compare[residency.STATIC_UNICODE_COMPARE_TARGET][0]

        unrelated_code = bytearray(callback_body.size)
        unrelated_stub = compare["PyObject_RichCompare"][0]
        unrelated_word = (
            0xF0000000
            | residency.P2_CALLA_IMMEDIATE_OPCODE
            | unrelated_stub.address
        )
        unrelated_code[4:8] = unrelated_word.to_bytes(4, "little")
        residency.verify_static_unicode_hot_edge_records(
            type_init, compare, bytes(unrelated_code)
        )

        for condition in (0xF, 0x5):
            with self.subTest(condition=condition):
                bad_code = bytearray(callback_body.size)
                call_word = (
                    condition << 28
                    | residency.P2_CALLA_IMMEDIATE_OPCODE
                    | target_stub.address
                )
                bad_code[12:16] = call_word.to_bytes(4, "little")
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    r"hashtable_unicode_compare retains a direct CALLA to "
                    r"unicode_compare_eq overlay stub .* at body\+0xc",
                ):
                    residency.verify_static_unicode_hot_edge_records(
                        type_init, compare, bytes(bad_code)
                    )

    def test_compare_contract_requires_exact_explicit_group_10(self):
        self.assertEqual(residency.COMPARE_OVERLAY_GROUP, 10)
        self.assertEqual(
            residency.COMPARE_OVERLAY_SECTION,
            ".p2.overlay.group.0000000a",
        )
        self.assertEqual(
            residency.COMPARE_OVERLAY_FUNCTIONS,
            self.COMPARE_OVERLAY_FUNCTIONS,
        )
        self.assertEqual(len(self.COMPARE_OVERLAY_FUNCTIONS), 4)
        self.assertEqual(
            self.COMPARE_SECTION_SIZE,
            4 + 292 + 1268 + 296 + 544,
        )
        self.assertEqual(
            self.SLOT_END - self.SLOT_START - self.COMPARE_SECTION_SIZE,
            87708,
        )

        symbols = self._compare_symbols()
        resolved = residency.verify_compare_overlay_records(
            symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), self.COMPARE_OVERLAY_FUNCTIONS)

        for name in self.COMPARE_OVERLAY_FUNCTIONS:
            with self.subTest(name=name, failure="missing stub"):
                missing_stub = [
                    symbol for symbol in symbols if symbol.name != name
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"rich-comparison overlay function {name} has 0 public "
                    r"symbols",
                ):
                    residency.verify_compare_overlay_records(
                        missing_stub,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

            with self.subTest(name=name, failure="missing body"):
                missing_body = [
                    symbol
                    for symbol in symbols
                    if residency.logical_symbol_name(symbol.name)
                    != (name, True)
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"rich-comparison overlay function {name} has 0 "
                    r"execution bodies",
                ):
                    residency.verify_compare_overlay_records(
                        missing_body,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_compare_contract_rejects_wrong_or_unexpected_group_10_body(self):
        symbols = self._compare_symbols()
        body_index = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name
            == "__p2_ovlbody.0000000a.PyUnicode_RichCompare"
        )
        original = symbols[body_index]

        wrong_group = list(symbols)
        wrong_group[body_index] = residency.SymbolRecord(
            name="__p2_ovlbody.00000009.PyUnicode_RichCompare",
            address=original.address,
            size=original.size,
            section=residency.CODE_INIT_OVERLAY_SECTION,
            executable=True,
            section_index=21,
            section_address=self.SLOT_START,
            section_size=self.COMPARE_SECTION_SIZE,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"PyUnicode_RichCompare.*not explicit group 10",
        ):
            residency.verify_compare_overlay_records(
                wrong_group,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        for name in (
            "__p2_ovlbody.0000000a.unexpected_compare",
            "unexpected_direct_compare",
        ):
            with self.subTest(name=name):
                unexpected = list(symbols)
                unexpected.append(
                    residency.SymbolRecord(
                        name=name,
                        address=self.SLOT_START + 0x900,
                        size=0x40,
                        section=residency.COMPARE_OVERLAY_SECTION,
                        executable=True,
                        section_index=22,
                        section_address=self.SLOT_START,
                        section_size=self.COMPARE_SECTION_SIZE,
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"group 10 contains unexpected function body "
                    rf"{re.escape(name)}",
                ):
                    residency.verify_compare_overlay_records(
                        unexpected,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

        split = list(symbols)
        split[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address,
            size=original.size,
            section=original.section,
            executable=True,
            section_index=23,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"do not share one concrete group-10 section",
        ):
            residency.verify_compare_overlay_records(
                split,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_gc_collector_contract_requires_complete_explicit_group_8(self):
        self.assertEqual(residency.GC_TRAVERSAL_OVERLAY_GROUP, 8)
        self.assertEqual(
            residency.GC_TRAVERSAL_OVERLAY_SECTION,
            ".p2.overlay.group.00000008",
        )
        self.assertEqual(
            residency.GC_TRAVERSAL_OVERLAY_FUNCTIONS,
            self.GC_TRAVERSAL_OVERLAY_FUNCTIONS,
        )
        self.assertEqual(
            residency.GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS,
            self.GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS,
        )
        self.assertEqual(
            residency.GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS,
            self.GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS,
        )
        self.assertEqual(
            residency.GC_TRAVERSAL_OVERLAY_DUPLICATE_PUBLIC_FUNCTIONS,
            ("module_traverse",),
        )
        self.assertEqual(len(self.GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS), 14)
        self.assertEqual(len(self.GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS), 31)
        self.assertEqual(len(self.GC_TRAVERSAL_OVERLAY_FUNCTIONS), 45)
        self.assertEqual(len(set(self.GC_TRAVERSAL_OVERLAY_FUNCTIONS)), 45)
        self.assertTrue(
            set(self.GC_TRAVERSAL_OVERLAY_CALLBACK_FUNCTIONS).isdisjoint(
                self.GC_COLLECTOR_CORE_OVERLAY_FUNCTIONS
            )
        )

        # These are projections from the audited r3 map.  The ELF
        # section checks below remain the authority for the rebuilt image.

        self.assertEqual(
            self.TYPE_INIT_SECTION_SIZE,
            83980 + 5836 + 260 - 448 + 208 - 1936 + 1940,
        )
        self.assertEqual(self.SLOT_END - self.SLOT_START, 90112)
        self.assertEqual(
            self.SLOT_END - self.SLOT_START - self.TYPE_INIT_SECTION_SIZE,
            272,
        )
        self.assertEqual(
            self.GC_TRAVERSAL_SECTION_SIZE,
            4 + 3320 + 10832,
        )
        self.assertEqual(
            self.SLOT_END - self.SLOT_START - self.GC_TRAVERSAL_SECTION_SIZE,
            75956,
        )

        symbols = self._gc_traversal_symbols()
        resolved = residency.verify_gc_traversal_overlay_records(
            symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )
        self.assertEqual(tuple(resolved), self.GC_TRAVERSAL_OVERLAY_FUNCTIONS)

        for name in self.GC_TRAVERSAL_OVERLAY_FUNCTIONS:
            with self.subTest(name=name, failure="missing stub"):
                missing_stub = [
                    symbol for symbol in symbols if symbol.name != name
                ]
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"cyclic-GC overlay function {name} has 0 public symbols",
                ):
                    residency.verify_gc_traversal_overlay_records(
                        missing_stub,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

            with self.subTest(name=name, failure="missing body"):
                missing_body = [
                    symbol
                    for symbol in symbols
                    if residency.logical_symbol_name(symbol.name)
                    != (name, True)
                ]
                body_error = (
                    rf"cyclic-GC overlay function {name} has 0 execution "
                    + (
                        r"bodies in explicit group 8"
                        if name == "module_traverse"
                        else r"bodies; expected one"
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    body_error,
                ):
                    residency.verify_gc_traversal_overlay_records(
                        missing_body,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_gc_traversal_contract_handles_duplicate_local_module_symbol(self):
        symbols = self._gc_traversal_symbols()
        symbols.extend(
            (
                residency.SymbolRecord(
                    name="module_traverse",
                    address=self.STUBS_END - 4,
                    size=4,
                    section=residency.OVERLAY_STUB_SECTION,
                    executable=True,
                    section_index=7,
                ),
                residency.SymbolRecord(
                    name="__p2_ovlbody.00000009.module_traverse",
                    address=self.SLOT_START + 4,
                    size=0x40,
                    section=".p2.overlay.group.00000009",
                    executable=True,
                    section_index=21,
                    section_address=self.SLOT_START,
                    section_size=0x1000,
                ),
            )
        )
        residency.verify_gc_traversal_overlay_records(
            symbols,
            self.SLOT_START,
            self.SLOT_END,
            self.STUBS_START,
            self.STUBS_END,
        )

        malformed_homonym = list(symbols)
        malformed_homonym[-2] = residency.SymbolRecord(
            name="module_traverse",
            address=self.STUBS_END - 4,
            size=8,
            section=residency.OVERLAY_STUB_SECTION,
            executable=True,
            section_index=7,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"module_traverse stub must be one executable four-byte",
        ):
            residency.verify_gc_traversal_overlay_records(
                malformed_homonym,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        duplicate_group_body = list(symbols)
        duplicate_group_body.append(
            residency.SymbolRecord(
                name="__p2_ovlbody.00000008.module_traverse.clone.1",
                address=self.SLOT_START + 0x800,
                size=0x40,
                section=residency.GC_TRAVERSAL_OVERLAY_SECTION,
                executable=True,
                section_index=20,
                section_address=self.SLOT_START,
                section_size=self.GC_TRAVERSAL_SECTION_SIZE,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"module_traverse has 2 execution bodies in explicit group 8",
        ):
            residency.verify_gc_traversal_overlay_records(
                duplicate_group_body,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_gc_traversal_contract_rejects_unreviewed_group_8_body(self):
        for name in (
            "__p2_ovlbody.00000008.unexpected_traverser",
            "unexpected_direct_traverser",
        ):
            with self.subTest(name=name):
                symbols = self._gc_traversal_symbols()
                symbols.append(
                    residency.SymbolRecord(
                        name=name,
                        address=self.SLOT_START + 0x800,
                        size=0x40,
                        section=residency.GC_TRAVERSAL_OVERLAY_SECTION,
                        executable=True,
                        section_index=20,
                        section_address=self.SLOT_START,
                        section_size=self.GC_TRAVERSAL_SECTION_SIZE,
                    )
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"unexpected function body {re.escape(name)} at ",
                ):
                    residency.verify_gc_traversal_overlay_records(
                        symbols,
                        self.SLOT_START,
                        self.SLOT_END,
                        self.STUBS_START,
                        self.STUBS_END,
                    )

    def test_gc_traversal_contract_rejects_wrong_group_veneer_and_split(self):
        symbols = self._gc_traversal_symbols()
        body_index = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "__p2_ovlbody.00000008.visit_reachable"
        )
        original = symbols[body_index]

        wrong_group = list(symbols)
        wrong_group[body_index] = residency.SymbolRecord(
            name="__p2_ovlbody.00000009.visit_reachable",
            address=original.address,
            size=original.size,
            section=".p2.overlay.group.00000009",
            executable=True,
            section_index=21,
            section_address=self.SLOT_START,
            section_size=self.GC_TRAVERSAL_SECTION_SIZE,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"visit_reachable.*not explicit group 8",
        ):
            residency.verify_gc_traversal_overlay_records(
                wrong_group,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        veneer = list(symbols)
        veneer[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address,
            size=4,
            section=original.section,
            executable=True,
            section_index=original.section_index,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"visit_reachable.*not a substantive executable implementation",
        ):
            residency.verify_gc_traversal_overlay_records(
                veneer,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        split = list(symbols)
        split[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address,
            size=original.size,
            section=original.section,
            executable=True,
            section_index=21,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"do not share one concrete group-8 section",
        ):
            residency.verify_gc_traversal_overlay_records(
                split,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_gc_traversal_contract_rejects_unaligned_or_oversize_group(self):
        symbols = self._gc_traversal_symbols()
        body_index = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "__p2_ovlbody.00000008.visit_reachable"
        )
        original = symbols[body_index]

        unaligned = list(symbols)
        unaligned[body_index] = residency.SymbolRecord(
            name=original.name,
            address=original.address + 2,
            size=original.size,
            section=original.section,
            executable=True,
            section_index=original.section_index,
            section_address=original.section_address,
            section_size=original.section_size,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"visit_reachable.*not P2 instruction aligned",
        ):
            residency.verify_gc_traversal_overlay_records(
                unaligned,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

        oversize = [
            residency.SymbolRecord(
                name=symbol.name,
                address=symbol.address,
                size=symbol.size,
                section=symbol.section,
                executable=symbol.executable,
                symbol_type=symbol.symbol_type,
                section_index=symbol.section_index,
                section_address=symbol.section_address,
                section_size=(
                    self.SLOT_END - self.SLOT_START + 4
                    if symbol.section == residency.GC_TRAVERSAL_OVERLAY_SECTION
                    else symbol.section_size
                ),
            )
            for symbol in symbols
        ]
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"section ends at 0x7c004, beyond overlay slot 0x7c000",
        ):
            residency.verify_gc_traversal_overlay_records(
                oversize,
                self.SLOT_START,
                self.SLOT_END,
                self.STUBS_START,
                self.STUBS_END,
            )

    def test_gc_discarded_tss_allocation_services_are_explicitly_optional(self):
        symbols = [
            symbol
            for symbol in self._resident_symbols()
            if symbol.name not in ("PyThread_tss_alloc", "PyThread_tss_free")
        ]
        resolved = self._verify_records(symbols)
        self.assertEqual(resolved["PyThread_tss_alloc"], ())
        self.assertEqual(resolved["PyThread_tss_free"], ())

        without_live_tss = [
            symbol
            for symbol in symbols
            if symbol.name != "PyThread_tss_create"
        ]
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"missing resident thread symbol PyThread_tss_create",
        ):
            self._verify_records(without_live_tss)

    def test_reads_real_p2_elf_and_rejects_linked_overlay_body(self):
        temporary, elf = self._linked_fixture()
        with temporary:
            (
                slot_start,
                stubs_start,
                stubs_end,
                resolved,
                frozen_startup_data,
            ) = residency.verify(elf)
            self.assertEqual(slot_start, self.SLOT_START)
            self.assertEqual(stubs_start, self.STUBS_START)
            self.assertEqual(stubs_end, self.STUBS_END)
            self.assertEqual(
                set(resolved),
                {
                    requirement.logical_name
                    for requirement in residency.REQUIREMENTS
                },
            )
            self.assertEqual(
                tuple(frozen_startup_data),
                residency.FROZEN_STARTUP_DATA_SYMBOLS,
            )
            for symbol in frozen_startup_data.values():
                self.assertEqual(symbol.section_type, "SHT_PROGBITS")
                self.assertTrue(symbol.allocatable)

        temporary, elf = self._linked_fixture(overlay_python_main=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"python_main has overlay execution body __p2_ovlbody",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(utf8_wrong_group=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"ucs4lib_utf8_decode.*not explicit group 6",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(type_init_wrong_group=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"PyType_Ready.*not explicit group 7",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(gc_traversal_wrong_group=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"visit_reachable.*not explicit group 8",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(compare_wrong_group=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"PyUnicode_RichCompare.*not explicit group 10",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(compare_unexpected_body=True)
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"group 10 contains unexpected function body .*unexpected_compare",
        ):
            residency.verify(elf)

        temporary, elf = self._linked_fixture(
            static_unicode_cross_call=True
        )
        with temporary, self.assertRaisesRegex(
            residency.VerificationError,
            r"hashtable_unicode_compare retains a direct CALLA to "
            r"unicode_compare_eq overlay stub",
        ):
            residency.verify(elf)

    def test_cli_prints_exact_frozen_startup_data_inventory(self):
        temporary, elf = self._linked_fixture()
        with temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = residency.main([str(elf)])

            self.assertEqual(result, 0)
            lines = output.getvalue().splitlines()
            frozen_lines = [line for line in lines if line.startswith("frozen:")]
            self.assertEqual(
                frozen_lines,
                [
                    "frozen:{}:address=0x{:x}:size=1:section=.p2.xdata".format(
                        name, residency.P2_XMEM_START + index
                    )
                    for index, name in enumerate(
                        residency.FROZEN_STARTUP_DATA_SYMBOLS
                    )
                ],
            )

    def test_real_elf_rejects_nonfilebacked_or_nonallocatable_frozen_data(self):
        cases = (
            (
                {"xdata_noload": True},
                r"_Py_M__encodings is backed by SHT_NOBITS, not initialized "
                r"SHT_PROGBITS",
            ),
            (
                {"xdata_allocatable": False},
                r"_Py_M__encodings section \.p2\.xdata is not SHF_ALLOC",
            ),
        )
        for options, error in cases:
            with self.subTest(options=options):
                temporary, elf = self._linked_fixture(**options)
                with temporary, self.assertRaisesRegex(
                    residency.VerificationError, error
                ):
                    residency.verify(elf)

    def test_real_elf_rejects_raw_allocator_veneers_by_exact_address(self):
        cases = (
            ("_PyMem_RawMalloc", 64 * 4),
            ("_PyMem_RawFree", 67 * 4),
        )
        for name, offset in cases:
            with self.subTest(name=name):
                temporary, elf = self._linked_fixture(
                    stubbed_requirement=(name, offset)
                )
                address = self.STUBS_START + offset
                with temporary, self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"{name} starts at 0x{address:x} inside overlay stub range",
                ):
                    residency.verify(elf)

    def test_each_cpython_contract_symbol_fails_in_stub_interval(self):
        for name in self.CPYTHON_RESIDENT_REQUIREMENTS:
            with self.subTest(name=name):
                symbols = self._resident_symbols()
                target = next(
                    index
                    for index, symbol in enumerate(symbols)
                    if symbol.name == name
                )
                symbols[target] = residency.SymbolRecord(
                    name=name,
                    address=self.STUBS_START + 4,
                    size=4,
                    section=".text",
                    executable=True,
                )
                with self.assertRaisesRegex(
                    residency.VerificationError,
                    rf"{name} starts at 0x{self.STUBS_START + 4:x} inside ",
                ):
                    self._verify_records(symbols)

    def test_rejects_hidden_overlay_body_behind_resident_stub(self):
        symbols = self._resident_symbols()
        python_main = symbols[0]
        symbols[0] = residency.SymbolRecord(
            name=python_main.name,
            address=python_main.address,
            size=4,
            section=residency.OVERLAY_STUB_SECTION,
            executable=True,
        )
        symbols.append(
            residency.SymbolRecord(
                name="__p2_ovlbody.00000007.python_main.isra.0",
                address=self.SLOT_START,
                size=0x120,
                section=".p2.overlay.group.00000007",
                executable=True,
            )
        )

        with self.assertRaisesRegex(
            residency.VerificationError,
            r"python_main has overlay execution body __p2_ovlbody",
        ):
            self._verify_records(symbols)

    def test_rejects_public_overlay_stub_without_named_body(self):
        symbols = self._resident_symbols()
        symbols[0] = residency.SymbolRecord(
            name="python_main",
            address=0x2000,
            size=4,
            section=residency.OVERLAY_STUB_SECTION,
            executable=True,
        )
        with self.assertRaisesRegex(
            residency.VerificationError, r"python_main is only an overlay stub"
        ):
            self._verify_records(symbols)

    def test_rejects_four_byte_veneer_outside_published_stub_interval(self):
        symbols = self._resident_symbols()
        target = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "PyObject_Malloc"
        )
        symbols[target] = residency.SymbolRecord(
            name="PyObject_Malloc",
            address=0x20000,
            size=4,
            section=".text",
            executable=True,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"PyObject_Malloc is 4 bytes; .*four-byte P2LLVM overlay veneer",
        ):
            self._verify_records(symbols)

    def test_rejects_range_that_enters_stub_interval(self):
        symbols = self._resident_symbols()
        target = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "_PyMem_RawCalloc"
        )
        symbols[target] = residency.SymbolRecord(
            name="_PyMem_RawCalloc",
            address=self.STUBS_START - 4,
            size=8,
            section=".text",
            executable=True,
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"_PyMem_RawCalloc range .* overlaps overlay stub range",
        ):
            self._verify_records(symbols)

    def test_stub_interval_is_half_open(self):
        symbols = self._resident_symbols()
        target = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "_PyMem_RawRealloc"
        )
        symbols[target] = residency.SymbolRecord(
            name="_PyMem_RawRealloc",
            address=self.STUBS_END,
            size=0x80,
            section=".text",
            executable=True,
        )
        self._verify_records(symbols)

    def test_public_raw_wrapper_cannot_replace_internal_implementation(self):
        symbols = [
            symbol
            for symbol in self._resident_symbols()
            if symbol.name != "_PyMem_RawMalloc"
        ]
        self.assertTrue(
            any(symbol.name == "PyMem_RawMalloc" for symbol in symbols)
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"missing resident allocator symbol _PyMem_RawMalloc",
        ):
            self._verify_records(symbols)

    def test_rejects_distinct_alternate_implementations(self):
        symbols = self._resident_symbols()
        symbols.append(
            residency.SymbolRecord(
                name="python_launcher_main",
                address=0x8000,
                size=0x80,
                section=".text",
                executable=True,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"python_main has ambiguous implementations",
        ):
            self._verify_records(symbols)

    def test_rejects_duplicate_exact_symbol_at_distinct_addresses(self):
        symbols = self._resident_symbols()
        symbols.append(
            residency.SymbolRecord(
                name="_PyMem_RawMalloc",
                address=0x8000,
                size=0x80,
                section=".text",
                executable=True,
            )
        )
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"_PyMem_RawMalloc has ambiguous implementations",
        ):
            self._verify_records(symbols)

    def test_rejects_clone_that_crosses_slot_boundary(self):
        symbols = self._resident_symbols()
        target = next(
            index
            for index, symbol in enumerate(symbols)
            if symbol.name == "p2_uart_send"
        )
        symbols[target] = residency.SymbolRecord(
            name="p2_uart_send.constprop.3",
            address=self.SLOT_START - 4,
            size=8,
            section=".text",
            executable=True,
        )
        with self.assertRaisesRegex(
            residency.VerificationError, r"p2_uart_send.*crosses overlay slot"
        ):
            self._verify_records(symbols)

    def test_rejects_missing_and_non_executable_symbols(self):
        symbols = self._resident_symbols()
        without_worker = [
            symbol
            for symbol in symbols
            if symbol.name != "python_worker_main"
        ]
        with self.assertRaisesRegex(
            residency.VerificationError,
            r"missing resident python symbol python_worker_main",
        ):
            self._verify_records(without_worker)

        symbols[0] = residency.SymbolRecord(
            name="python_main",
            address=0x2000,
            size=0x40,
            section=".rodata",
            executable=False,
        )
        with self.assertRaisesRegex(
            residency.VerificationError, r"python_main is in non-executable section"
        ):
            self._verify_records(symbols)

    def test_build_audits_unfiltered_elf_and_preserves_report(self):
        wrapper = BUILD_WRAPPER.read_text(encoding="utf-8")
        package = wrapper.index('"$ROOT/tools/p2/p2_python_package.py"')
        verifier = wrapper.index('"$ROOT/tools/p2/verify-python-residency.py"')
        packaged_symbol_check = wrapper.index(
            '"$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx.full"',
            verifier,
        )

        self.assertLess(package, verifier)
        self.assertLess(verifier, packaged_symbol_check)
        self.assertIn('"$ROOT/nuttx.full"', wrapper[verifier : verifier + 300])
        self.assertIn(
            'tee "$python_residency_audit"', wrapper[verifier : verifier + 500]
        )
        self.assertIn("python-residency-audit.txt", wrapper)

    def test_build_locks_allocator_frontend_headroom_budget(self):
        wrapper = BUILD_WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            "startup-hot resident cluster is funded from the 8208-byte",
            wrapper,
        )
        self.assertIn("at least one KiB for the in-Hub user-heap", wrapper)
        self.assertIn("CONFIG_MM_KERNEL_HEAPSIZE=63232", wrapper)
        self.assertNotIn("CONFIG_MM_KERNEL_HEAPSIZE=64512", wrapper)
        self.assertNotIn("CONFIG_MM_KERNEL_HEAPSIZE=65536", wrapper)


if __name__ == "__main__":
    unittest.main()
