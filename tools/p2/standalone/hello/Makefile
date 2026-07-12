# SPDX-License-Identifier: Apache-2.0

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
NUTTX_ROOT := $(abspath $(THIS_DIR)/../../../..)
WORKSPACE_CACHE := $(abspath $(NUTTX_ROOT)/../.p2-nuttx-cache)
P2_CACHE ?= $(if $(wildcard $(WORKSPACE_CACHE)),$(WORKSPACE_CACHE),$(HOME)/.cache/p2-nuttx)
P2LLVM_ROOT ?= $(P2_CACHE)/p2llvm/install

CC := $(P2LLVM_ROOT)/bin/clang
LD := $(P2LLVM_ROOT)/bin/ld.lld
OBJCOPY := $(P2LLVM_ROOT)/bin/llvm-objcopy
READELF := $(P2LLVM_ROOT)/bin/llvm-readelf
SIZE := $(P2LLVM_ROOT)/bin/llvm-size
LIBP2 := $(P2LLVM_ROOT)/libp2/lib/libp2.a

BUILD := $(THIS_DIR)/build
TARGET := $(BUILD)/p2hello

CPPFLAGS := --sysroot=$(P2LLVM_ROOT)
CFLAGS := --target=p2 -std=gnu11 -Os -ffreestanding -fno-builtin \
	-fno-common -fno-jump-tables -ffunction-sections -fdata-sections \
	-Wall -Wextra -Werror
LDFLAGS := -T $(THIS_DIR)/hello.ld --gc-sections \
	-Map=$(TARGET).map

.PHONY: all clean tools verify

all: $(TARGET).elf $(TARGET).bin verify
	$(SIZE) $(TARGET).elf

tools:
	@test -x "$(CC)" || { \
		echo "missing pinned P2 Clang: $(CC)" >&2; \
		echo "run tools/p2/bootstrap-local.sh or set P2LLVM_ROOT" >&2; \
		exit 1; \
	}
	@test -x "$(LD)" || { echo "missing P2 ld.lld: $(LD)" >&2; exit 1; }
	@test -f "$(LIBP2)" || { echo "missing pinned libp2: $(LIBP2)" >&2; exit 1; }
	@test ! -e "$(P2LLVM_ROOT)/libc/lib/libc.a" || { \
		echo "refusing a P2LLVM_ROOT containing p2llvm libc" >&2; \
		exit 1; \
	}

$(BUILD):
	mkdir -p $@

$(BUILD)/hello.o: $(THIS_DIR)/hello.c | $(BUILD) tools
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(TARGET).elf: $(BUILD)/hello.o $(THIS_DIR)/hello.ld | tools
	$(LD) $(LDFLAGS) -o $@ $< $(LIBP2)

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

verify: $(TARGET).elf
	python3 $(THIS_DIR)/verify.py --readelf $(READELF) $<

clean:
	rm -rf $(BUILD)
