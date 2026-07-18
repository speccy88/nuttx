# P2 Python container ABI

Status: **HOST-TESTED** only. Target loading remains **HIL-REQUIRED**.

`p2_python_container.py` builds one deterministic container holding the data
that cannot remain in Propeller 2 Hub RAM:

- initialized globals at fixed tagged-PSRAM addresses;
- zero-fill ranges for external `.bss`-like storage;
- Hub-overlay group images;
- the Hub-stub to overlay-group/entry mapping; and
- one standard-library ROMFS image.

The current format does not compress payloads. Codec and flag fields are in
the ABI so a later minor or major format can add compression without silently
changing the meaning of version 1 images.

## Command line

```text
python3 tools/p2/p2_python_container.py pack INPUT.json OUTPUT.p2py
python3 tools/p2/p2_python_container.py verify OUTPUT.p2py
python3 tools/p2/p2_python_container.py list OUTPUT.p2py
```

`pack` writes a temporary file in the output directory, flushes and verifies
it, and only then replaces the destination with `os.replace()`. A rejected
manifest or changed input payload leaves an existing destination untouched.

`verify` validates the complete canonical layout and every payload CRC.
`list` performs that same verification before emitting JSON metadata.

## Packer input

The JSON root has no optional or ignored keys:

```json
{
  "format": "p2-python-container-input-v1",
  "build_fingerprint": "64 lowercase-or-uppercase hexadecimal digits",
  "overlay_slot_size": 65536,
  "initialized_globals": [
    {
      "id": 0,
      "name": "python.runtime.initialized",
      "path": "python-globals.bin",
      "address": "0x10000000",
      "alignment": 16,
      "codec": "none"
    }
  ],
  "zero_fill": [
    {
      "id": 0,
      "name": "python.runtime.zero",
      "address": "0x10010000",
      "size": 65536,
      "alignment": 16
    }
  ],
  "overlay_groups": [
    {
      "id": 1,
      "name": "python.overlay.hot",
      "path": "overlay-hot.bin",
      "load_address": "0x00050000",
      "alignment": 16,
      "codec": "none"
    }
  ],
  "stubs": [
    {
      "id": 0,
      "name": "Py_Initialize",
      "group_id": 1,
      "entry_offset": 0
    }
  ],
  "stdlib_romfs": {
    "id": 0,
    "name": "python.stdlib.romfs",
    "path": "python-stdlib.img",
    "alignment": 16,
    "codec": "none"
  }
}
```

Paths are resolved relative to the JSON file. IDs are explicit. Data-section
and stub IDs are contiguous from zero; overlay-group IDs are contiguous from
one because runtime group zero is reserved for resident Hub code. JSON object
keys, array order, input paths, timestamps, ownership, and modes do not affect
the container bytes.

Names must be unique, non-empty, valid Unicode NFC strings without control
characters. Sections and strings are sorted by encoded values. Duplicate JSON
keys are rejected.

The build fingerprint is an external 32-byte identity, normally SHA-256 over
the final resident ELF/link contract. The packer stores it verbatim and rejects
an all-zero fingerprint. It does not guess which source artifact should define
the runtime compatibility boundary.

Integer values may be JSON integers or strings accepted by Python `int(x, 0)`,
such as `"0x10000000"`.

### Address rules

- Initialized and zero-fill external globals must lie wholly inside tagged
  PSRAM `0x10000000..0x11ffffff`. They may be adjacent but may not overlap.
- Overlay group images must be whole P2 instructions, have one common nonzero
  aligned load address, and lie below the pinned board-loader limit
  `0x0007c000` (which is itself inside the architectural 20-bit PC range).
  Groups intentionally share this one evictable Hub load range. Every decoded
  group must fit `overlay_slot_size`; the complete configured slot must also
  end at or before `0x0007c000`.
- A stub entry offset must be four-byte aligned and name a complete instruction
  inside the referenced group.
- ROMFS has no fixed runtime address and carries zero in that field.

### Section flags

The optional `flags` JSON array uses these names:

| Name | Bit | Meaning |
|---|---:|---|
| `required` | 0 | Loader must reject the container if it cannot use this section. |
| `read-only` | 1 | Runtime contents are not writable. |
| `executable` | 2 | Decoded contents are P2 instructions. |
| `fixed-address` | 3 | `virtual_address` is a required address, not a hint. |

Omitting `flags` selects the canonical flags for the section type. Version 1
requires all sections, fixed addresses for globals and overlays, executable
read-only overlay groups, writable zero-fill storage, and a read-only ROMFS.
Unknown bits and inconsistent combinations are rejected.

## Integer and byte conventions

All integers are unsigned little-endian. All sizes and offsets are bytes.
Every reserved field and every alignment byte must be zero. File offsets are
canonical: each payload starts at the first address at or after the previous
payload that satisfies `max(section_alignment, 16)`.

The maximum version 1 container size is `0xffffffff` bytes. Table counts and
the manifest additionally have conservative host limits to prevent an
untrusted header from causing excessive allocation.

## Fixed header: 192 bytes

| Offset | Size | Field | Version 1 value/meaning |
|---:|---:|---|---|
| `0x00` | 8 | `magic` | `50 32 50 59 43 54 4e 00` (`P2PYCTN\0`) |
| `0x08` | 2 | `version_major` | `1` |
| `0x0a` | 2 | `version_minor` | `0` |
| `0x0c` | 2 | `header_size` | `192` |
| `0x0e` | 2 | `section_entry_size` | `96` |
| `0x10` | 2 | `group_entry_size` | `16` |
| `0x12` | 2 | `stub_entry_size` | `8` |
| `0x14` | 2 | `stub_name_entry_size` | `8` |
| `0x16` | 2 | reserved | zero |
| `0x18` | 4 | `flags` | presence flags described below |
| `0x1c` | 4 | `endian_tag` | `0x01020304` |
| `0x20` | 4 | `section_count` | number of section entries |
| `0x24` | 4 | `group_count` | number of runtime group records |
| `0x28` | 4 | `stub_count` | number of runtime stub records |
| `0x2c` | 4 | reserved | zero |
| `0x30` | 8 | `section_table_offset` | exactly `192` |
| `0x38` | 8 | `group_table_offset` | immediately after section table |
| `0x40` | 8 | `stub_table_offset` | immediately after group table |
| `0x48` | 8 | `stub_name_table_offset` | immediately after stub table |
| `0x50` | 8 | `string_table_offset` | immediately after stub-name table |
| `0x58` | 8 | `string_table_size` | byte count, no terminators |
| `0x60` | 8 | `manifest_size` | first payload boundary, aligned to 16 |
| `0x68` | 8 | `file_size` | must equal the physical file size |
| `0x70` | 32 | `build_fingerprint` | exact resident-build identity |
| `0x90` | 32 | `manifest_sha256` | manifest digest described below |
| `0xb0` | 4 | `overlay_load_address` | common fixed group load address |
| `0xb4` | 4 | `overlay_slot_size` | configured maximum decoded group size |
| `0xb8` | 8 | reserved | zero |

Header presence flags are derived rather than caller-selected:

| Bit | Meaning |
|---:|---|
| 0 | initialized external globals are present |
| 1 | external zero-fill metadata is present |
| 2 | overlay groups are present |
| 3 | stub mappings are present |
| 4 | stdlib ROMFS is present |

## Section entry: 96 bytes

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 2 | `type` |
| `0x02` | 2 | `codec` |
| `0x04` | 4 | `flags` |
| `0x08` | 4 | type-local contiguous `id` (overlay IDs start at one) |
| `0x0c` | 4 | name offset relative to string table |
| `0x10` | 4 | name byte length |
| `0x14` | 4 | power-of-two alignment |
| `0x18` | 4 | reserved, zero |
| `0x1c` | 8 | `virtual_address` |
| `0x24` | 8 | `file_offset` |
| `0x2c` | 8 | `stored_size` |
| `0x34` | 8 | `memory_size` |
| `0x3c` | 8 | `uncompressed_size` |
| `0x44` | 4 | decoded-content IEEE CRC32 |
| `0x48` | 4 | reserved, zero |
| `0x4c` | 20 | reserved, zero |

Section types:

| Value | Name | Payload semantics |
|---:|---|---|
| 1 | `external-init` | Copy decoded bytes to `virtual_address`. |
| 2 | `external-zero` | No file payload; zero `memory_size` bytes. |
| 3 | `overlay-group` | Copy/decode into the Hub overlay load address. |
| 4 | `stdlib-romfs` | Standard-library ROMFS image. |

Codec zero means `none`; for it, `stored_size == uncompressed_size ==
memory_size`. A zero-fill entry has zero `file_offset`, `stored_size`,
`uncompressed_size`, and CRC, with a nonzero `memory_size`.

The CRC is over decoded bytes. This distinction is immaterial for codec zero
but fixes the checksum meaning for future compression codecs.

## Runtime group entry: 16 bytes

This table is deliberately identical to the resident overlay runtime's logical
group record. Group ID is the zero-based table index. Record zero must be all
zero and represents resident Hub code; real overlay records start at one.

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | `source`, a container blob offset |
| `0x04` | 4 | decoded group `size` |
| `0x08` | 4 | decoded-content IEEE `crc32` |
| `0x0c` | 4 | group `flags` |

At load time the target checks `source + size`, copies the pack to tagged
PSRAM, and relocates `source` to `pack_base + source`. Version 1 group flags
are the corresponding overlay section flags. The richer section record is
retained to validate codec, alignment, load address, stored size, and names;
the group record must agree with it exactly.

## Runtime stub entry: 8 bytes

This table is deliberately identical to the resident runtime's
`{uint32_t group, uint32_t offset}` record. Stub ID is the zero-based table
index.

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | overlay `group_id` |
| `0x04` | 4 | byte `entry_offset` within decoded group |

The runtime obtains `stub_id` from the contiguous four-byte Hub stub region,
then uses this table to select a group and entry PC.

## Stub-name entry: 8 bytes

The target runtime does not need names on its dispatch hot path. A parallel
diagnostic table retains them without changing the exact runtime stub ABI.

| Offset | Size | Field |
|---:|---:|---|
| `0x00` | 4 | name offset relative to string table |
| `0x04` | 4 | name byte length |

## Strings, digest, and payloads

The string table is the concatenation of unique UTF-8 name byte strings sorted
lexicographically. It has no length prefix or NUL terminators; entries carry an
offset and length. The bytes between its end and `manifest_size` are zero.

`manifest_sha256` protects every byte from file offset zero through
`manifest_size - 1`. For digest calculation, its own 32-byte field at
`0x90..0xaf` is treated as zero. Thus the digest includes the header, build
fingerprint, section table and CRC values, runtime group table, runtime stub
table, stub-name table, string table, and manifest padding. Payload bytes are
protected independently by per-section CRC32.

File-backed payloads appear in canonical section-table order. Padding before
each payload is zero. The physical file ends immediately after the final
payload; trailing bytes are invalid.

## Fail-closed validation

A version 1 loader or verifier must reject, before copying or executing code:

- unknown version, type, codec, flag, or nonzero reserved data;
- truncated files, trailing bytes, arithmetic overflow, or noncanonical table
  offsets;
- invalid UTF-8/NFC, duplicate IDs/names, or a noncanonical string table;
- payload ranges outside the file, overlaps, wrong alignment/order, or nonzero
  padding;
- external-global overlap or addresses outside the 32-MiB tagged range;
- differing overlay load addresses or code outside the pinned Hub load window;
- missing groups, out-of-range or unaligned stub entries;
- any stub mapped to reserved resident group zero or a decoded group larger
  than the configured Hub slot;
- manifest SHA mismatch or any payload CRC mismatch; and
- a build fingerprint not accepted by the resident firmware.

The host verifier enforces all structural rules even when payload CRC checking
is explicitly disabled by a library caller. The command-line modes always
verify payload CRCs.
