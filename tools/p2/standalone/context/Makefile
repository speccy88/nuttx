# SPDX-License-Identifier: Apache-2.0

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
NUTTX_ROOT := $(abspath $(THIS_DIR)/../../../..)
WORKSPACE_CACHE := $(abspath $(NUTTX_ROOT)/../.p2-nuttx-cache)
P2_CACHE ?= $(if $(wildcard $(WORKSPACE_CACHE)),$(WORKSPACE_CACHE),$(HOME)/.cache/p2-nuttx)
P2LLVM_ROOT ?= $(P2_CACHE)/p2llvm/install

CC := $(P2LLVM_ROOT)/bin/clang
LD := $(P2LLVM_ROOT)/bin/ld.lld
OBJCOPY := $(P2LLVM_ROOT)/bin/llvm-objcopy
OBJDUMP := $(P2LLVM_ROOT)/bin/llvm-objdump
READELF := $(P2LLVM_ROOT)/bin/llvm-readelf
SIZE := $(P2LLVM_ROOT)/bin/llvm-size
LIBP2 := $(P2LLVM_ROOT)/libp2/lib/libp2.a

BUILD := $(THIS_DIR)/build
TARGET := $(BUILD)/p2context
SOFTARITH := $(NUTTX_ROOT)/arch/p2/src/common/p2_softarith.c

CPPFLAGS := --sysroot=$(P2LLVM_ROOT) -I$(THIS_DIR)/include \
	-I$(NUTTX_ROOT) -I$(NUTTX_ROOT)/include
CFLAGS := --target=p2 -std=gnu11 -Os -ffreestanding -fno-builtin \
	-fno-common -fno-jump-tables -ffunction-sections -fdata-sections \
	-Wall -Wextra -Werror
ASFLAGS := --target=p2 -x assembler-with-cpp -I$(NUTTX_ROOT)
LDFLAGS := -T $(THIS_DIR)/context.ld --gc-sections -Map=$(TARGET).map
OBJECTS := $(BUILD)/context.o $(BUILD)/context_switch.o $(BUILD)/softarith.o

.PHONY: all clean tools verify test

all: $(TARGET).elf $(TARGET).bin verify test
	$(SIZE) $(TARGET).elf

tools:
	@test -x "$(CC)" || { \
		echo "missing pinned P2 Clang: $(CC)" >&2; \
		echo "run tools/p2/bootstrap-local.sh or set P2LLVM_ROOT" >&2; \
		exit 1; \
	}
	@test -x "$(LD)" || { echo "missing P2 ld.lld: $(LD)" >&2; exit 1; }
	@test -f "$(LIBP2)" || { echo "missing pinned libp2: $(LIBP2)" >&2; exit 1; }
	@test -f "$(SOFTARITH)" || { echo "missing P2 soft arithmetic runtime" >&2; exit 1; }
	@test ! -e "$(P2LLVM_ROOT)/libc/lib/libc.a" || { \
		echo "refusing a P2LLVM_ROOT containing p2llvm libc" >&2; \
		exit 1; \
	}

$(BUILD):
	mkdir -p $@

$(BUILD)/context.o: $(THIS_DIR)/context.c \
		$(NUTTX_ROOT)/arch/p2/include/context.h \
		$(THIS_DIR)/include/nuttx/config.h | $(BUILD) tools
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/context_switch.o: $(THIS_DIR)/context_switch.S \
		$(NUTTX_ROOT)/arch/p2/include/context.h | $(BUILD) tools
	$(CC) $(ASFLAGS) -c $< -o $@

$(BUILD)/softarith.o: $(SOFTARITH) \
		$(THIS_DIR)/include/nuttx/config.h | $(BUILD) tools
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(TARGET).elf: $(OBJECTS) $(THIS_DIR)/context.ld | tools
	$(LD) $(LDFLAGS) -o $@ $(OBJECTS) $(LIBP2)

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

verify: $(TARGET).elf $(TARGET).bin
	python3 $(THIS_DIR)/verify.py $(TARGET).elf --readelf $(READELF) \
		--objdump $(OBJDUMP) --binary $(TARGET).bin \
		--assembly $(THIS_DIR)/context_switch.S \
		--source $(THIS_DIR)/context.c \
		--objects $(OBJECTS) $(TARGET).elf

test:
	python3 -m unittest -v $(THIS_DIR)/test_verify.py

clean:
	rm -rf $(BUILD)
