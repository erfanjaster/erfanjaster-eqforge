# EQForge PipeWire SPA filter module
#
# Builds libeqforge-filter.so: an SPA node factory ("eqforge.filter") that
# processes audio through libeqforge. Designed to be hosted by PipeWire via
# libpipewire-module-adapter (see packaging/pipewire/99-eqforge.conf).

CC ?= gcc
PKG_CONFIG ?= pkg-config

CORE_DIR := ../core
CORE_INC := $(CORE_DIR)/include
CORE_LIB := $(CORE_DIR)/build/libeqforge.a

PKGS := libspa-0.2
PW_CFLAGS := $(shell $(PKG_CONFIG) --cflags $(PKGS) | sed "s/-I/-isystem /g")
CFLAGS ?= -O2
WARNINGS := -Wall -Wextra -Wno-unused-parameter

OUT := build/libeqforge-filter.so

.PHONY: all clean install

all: $(OUT)

$(CORE_LIB):
	$(MAKE) -C $(CORE_DIR)

$(OUT): module-eqforge.c $(CORE_LIB)
	mkdir -p build
	$(CC) $(CFLAGS) $(WARNINGS) -fPIC -shared \
	  -I$(CORE_INC) $(PW_CFLAGS) \
	  module-eqforge.c $(CORE_LIB) -lm -o $@

install: $(OUT)
	install -d $(DESTDIR)/usr/lib/$(shell uname -m)-linux-gnu/spa-0.2/eqforge
	install -m 755 $(OUT) $(DESTDIR)/usr/lib/$(shell uname -m)-linux-gnu/spa-0.2/eqforge/

clean:
	rm -rf build
