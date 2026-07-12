include $(TOPDIR)/tools/Make.defs
include chip/Make.defs
ARCH_SUBDIR = p2x8c4m64p
ARCH_SRCDIR = $(TOPDIR)$(DELIM)arch$(DELIM)p2$(DELIM)src
INCLUDES += ${INCDIR_PREFIX}$(ARCH_SRCDIR)$(DELIM)common
INCLUDES += ${INCDIR_PREFIX}$(ARCH_SRCDIR)$(DELIM)$(ARCH_SUBDIR)
INCLUDES += ${INCDIR_PREFIX}$(TOPDIR)$(DELIM)sched
CPPFLAGS += $(INCLUDES)
CFLAGS += $(INCLUDES)
AFLAGS += $(INCLUDES)
VPATH += common $(ARCH_SUBDIR)
include common/Make.defs
include $(ARCH_SUBDIR)/Make.defs
OBJS = $(CSRCS:.c=$(OBJEXT))
BIN = libarch$(LIBEXT)
all: $(BIN)
$(OBJS): %$(OBJEXT): %.c
	$(call COMPILE, $<, $@)
$(BIN): $(OBJS)
	$(call ARCHIVE, $@, $(OBJS))
clean:
	$(call DELFILE, $(BIN)); $(call CLEAN)
