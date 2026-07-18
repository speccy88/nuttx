/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_python.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/types.h>
#include <sys/statfs.h>

#include <errno.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#include <nuttx/clock.h>
#include <nuttx/drivers/ramdisk.h>
#include <nuttx/irq.h>
#include <nuttx/sched.h>

#include <arch/board/board.h>
#include <arch/board/p2_ec32mb_psram.h>
#include <arch/python_container.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#ifndef CONFIG_INTERPRETERS_CPYTHON_EXTERNAL_ROMFS
#  error "The P2 Python transport is only valid for external CPython ROMFS"
#endif

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
#  error "The P2 Python transport requires unified PSRAM"
#endif

#ifndef CONFIG_P2_HUB_OVERLAYS
#  error "The P2 Python transport requires Hub overlays"
#endif

#if CONFIG_P2_EC32MB_PYTHON_CONTAINER_OFFSET != 3145728
#  error "The P2 Python container ABI requires tagged base 0x10300000"
#endif

#if CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE != 16777216
#  error "The P2 Python container ABI requires a 16-MiB PSRAM reserve"
#endif

#if (CONFIG_P2_EC32MB_PYTHON_CONTAINER_OFFSET & 15) != 0
#  error "The P2 Python container offset must be 16-byte aligned"
#endif

#if CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE <= \
    CONFIG_P2_EC32MB_PYTHON_CONTAINER_OFFSET
#  error "The unified PSRAM reserve leaves no Python container capacity"
#endif

#if CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE > P2_PSRAM_UNIFIED_SIZE
#  error "The unified PSRAM reserve exceeds the physical device"
#endif

#if CONFIG_UART0_BAUD != 230400
#  error "The P2 Python upload pacing contract requires 230400 baud"
#endif

#define P2_PYTHON_UPLOAD_MAGIC_SIZE       8
#define P2_PYTHON_UPLOAD_HEADER_SIZE      24
#define P2_PYTHON_UPLOAD_FRAME_HEADER     12
#define P2_PYTHON_UPLOAD_FRAME_SIZE       1024
#define P2_PYTHON_UPLOAD_RETRANSMISSIONS  3
#define P2_PYTHON_CONTAINER_HEADER_SIZE   192
#define P2_PYTHON_UPLOAD_PROTOCOL         2
#define P2_PYTHON_HEADER_TIMEOUT          SEC2TICK(30)
#define P2_PYTHON_UPLOAD_TIMEOUT          SEC2TICK(1800)
#define P2_PYTHON_FRAME_TIMEOUT           SEC2TICK(30)
#define P2_PYTHON_RX_PURGE_TICKS          2
#define P2_PYTHON_POLL_MSEC               250
#define P2_PYTHON_MARKER_SIZE             224
#define P2_PYTHON_CRC_POLYNOMIAL          UINT32_C(0xedb88320)

#define P2_PYTHON_STATE_EMPTY             0
#define P2_PYTHON_STATE_LOADING           1
#define P2_PYTHON_STATE_READY             2

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_python_source_s
{
  uintptr_t base;
  uint32_t size;
};

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const uint8_t g_p2_python_upload_magic[P2_PYTHON_UPLOAD_MAGIC_SIZE] =
{
  'P', '2', 'P', 'Y', 'U', 'P', 'L', 0
};

/* Give the linker-reserved build fingerprint a real allocatable, read-only,
 * non-executable input section.  The host packager replaces these canonical
 * zero bytes in both the resident ELF and raw image before either may be used.
 */

static const uint8_t g_p2_python_build_fingerprint[32]
  __attribute__((section(".p2.python.fingerprint"), used, aligned(4))) =
{
  0
};

static struct p2_python_container_s g_p2_python_container;
static struct p2_overlay_group_s
  g_p2_python_groups[CONFIG_P2_HUB_OVERLAY_GROUP_COUNT + 1];
static volatile uint32_t g_p2_python_state;

/****************************************************************************
 * Linker Symbols
 ****************************************************************************/

extern const uint8_t __p2_python_fingerprint_start[];
extern const uint8_t __p2_python_fingerprint_end[];
extern const uint8_t __p2_overlay_slot_start[];
extern const uint8_t __p2_overlay_slot_end[];
extern const uint8_t __p2_xdata_start[];
extern const uint8_t __p2_xbss_end[];
extern volatile uint32_t g_p2_uart_rx_dropped;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_python_getle32(FAR const uint8_t *value)
{
  return (uint32_t)value[0] | (uint32_t)value[1] << 8 |
         (uint32_t)value[2] << 16 | (uint32_t)value[3] << 24;
}

static uint16_t p2_python_getle16(FAR const uint8_t *value)
{
  return (uint16_t)((uint16_t)value[0] | (uint16_t)value[1] << 8);
}

static void p2_python_putle32(FAR uint8_t *value, uint32_t word)
{
  value[0] = (uint8_t)word;
  value[1] = (uint8_t)(word >> 8);
  value[2] = (uint8_t)(word >> 16);
  value[3] = (uint8_t)(word >> 24);
}

static uint32_t p2_python_crc32_update(uint32_t crc,
                                       FAR const uint8_t *data,
                                       size_t size)
{
  size_t index;

  for (index = 0; index < size; index++)
    {
      unsigned int bit;

      crc ^= data[index];
      for (bit = 0; bit < 8; bit++)
        {
          crc = crc >> 1 ^
                ((crc & 1) != 0 ? P2_PYTHON_CRC_POLYNOMIAL : 0);
        }
    }

  return crc;
}

static int p2_python_write_all(int fd, FAR const void *buffer, size_t size)
{
  FAR const uint8_t *cursor = buffer;

  while (size > 0)
    {
      ssize_t written = write(fd, cursor, size);

      if (written < 0)
        {
          if (errno == EINTR)
            {
              continue;
            }

          return -errno;
        }

      if (written == 0)
        {
          return -EIO;
        }

      cursor += written;
      size -= written;
    }

  return 0;
}

static int p2_python_purge_input(int fd)
{
  int first_error = 0;

  /* TCFLSH discards the upper-half serial receive buffer.  P2 also has a
   * 256-byte lower RX ring which the 10-ms timer service promotes into that
   * upper buffer.  Flush once, wait two complete service ticks, then flush
   * again while the terminal is still raw.  The stop-and-wait sender has
   * already stopped transmitting before this terminal cleanup begins.
   */

  if (tcflush(fd, TCIFLUSH) < 0)
    {
      first_error = -errno;
    }

  nxsched_usleep(P2_PYTHON_RX_PURGE_TICKS * USEC_PER_TICK);

  if (tcflush(fd, TCIFLUSH) < 0 && first_error == 0)
    {
      first_error = -errno;
    }

  return first_error;
}

static void p2_python_marker(FAR const char *format, ...)
{
  char buffer[P2_PYTHON_MARKER_SIZE];
  va_list ap;
  int length;

  va_start(ap, format);
  length = vsnprintf(buffer, sizeof(buffer), format, ap);
  va_end(ap);

  if (length <= 0)
    {
      return;
    }

  if ((size_t)length >= sizeof(buffer))
    {
      length = sizeof(buffer) - 1;
    }

  p2_python_write_all(STDOUT_FILENO, buffer, (size_t)length);
}

static int p2_python_read_exact(int fd, FAR void *buffer, size_t size,
                                clock_t started, clock_t timeout)
{
  FAR uint8_t *cursor = buffer;

  while (size > 0)
    {
      struct pollfd descriptor;
      clock_t elapsed = clock_systime_ticks() - started;
      int ret;

      if (elapsed >= timeout)
        {
          return -ETIMEDOUT;
        }

      descriptor.fd = fd;
      descriptor.events = POLLIN;
      descriptor.revents = 0;
      ret = poll(&descriptor, 1, P2_PYTHON_POLL_MSEC);
      if (ret < 0)
        {
          if (errno == EINTR)
            {
              continue;
            }

          return -errno;
        }

      if (ret == 0)
        {
          continue;
        }

      if ((descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0)
        {
          return -EIO;
        }

      if ((descriptor.revents & POLLIN) == 0)
        {
          continue;
        }

      ret = read(fd, cursor, size);
      if (ret < 0)
        {
          if (errno == EINTR || errno == EAGAIN)
            {
              continue;
            }

          return -errno;
        }

      if (ret == 0)
        {
          return -EPIPE;
        }

      cursor += ret;
      size -= ret;
    }

  return 0;
}

static int p2_python_psram_transfer(enum p2_psram_operation_e operation,
                                    uint64_t address, FAR void *buffer,
                                    size_t size)
{
  uint64_t offset;

  if (address < P2_PSRAM_UNIFIED_BASE)
    {
      return -ERANGE;
    }

  offset = address - P2_PSRAM_UNIFIED_BASE;
  if (offset > P2_PSRAM_UNIFIED_SIZE ||
      size > P2_PSRAM_UNIFIED_SIZE - offset)
    {
      return -ERANGE;
    }

  while (size > 0)
    {
      uint32_t chunk = size > CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST ?
                       CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST : size;
      int ret;

      ret = p2_psram_unified_transfer(operation, (uint32_t)offset,
                                      buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      offset += chunk;
      buffer = (FAR uint8_t *)buffer + chunk;
      size -= chunk;
    }

  return 0;
}

static int p2_python_source_read(FAR void *arg, uint64_t address,
                                 FAR void *buffer, size_t size)
{
  FAR struct p2_python_source_s *source = arg;

  if (source == NULL || address > source->size ||
      size > source->size - address)
    {
      return -ERANGE;
    }

  return p2_python_psram_transfer(P2_PSRAM_OPERATION_READ,
                                  source->base + address, buffer, size);
}

static int p2_python_target_read(FAR void *arg, uint64_t address,
                                 FAR void *buffer, size_t size)
{
  UNUSED(arg);
  return p2_python_psram_transfer(P2_PSRAM_OPERATION_READ, address,
                                  buffer, size);
}

static int p2_python_target_write(FAR void *arg, uint64_t address,
                                  FAR const void *buffer, size_t size)
{
  UNUSED(arg);
  return p2_python_psram_transfer(P2_PSRAM_OPERATION_WRITE, address,
                                  (FAR void *)buffer, size);
}

static int p2_python_target_zero(FAR void *arg, uint64_t address,
                                 size_t size)
{
  uint8_t zeroes[P2_PYTHON_UPLOAD_FRAME_SIZE];

  UNUSED(arg);
  memset(zeroes, 0, sizeof(zeroes));
  while (size > 0)
    {
      size_t chunk = size > sizeof(zeroes) ? sizeof(zeroes) : size;
      int ret = p2_python_psram_transfer(P2_PSRAM_OPERATION_WRITE,
                                         address, zeroes, chunk);

      if (ret < 0)
        {
          return ret;
        }

      address += chunk;
      size -= chunk;
    }

  return 0;
}

static bool p2_python_fingerprint_is_zero(FAR const uint8_t *fingerprint)
{
  size_t index;

  for (index = 0; index < P2_PYTHON_CONTAINER_FINGERPRINT_SIZE; index++)
    {
      if (fingerprint[index] != 0)
        {
          return false;
        }
    }

  return true;
}

static int p2_python_contract(
  FAR struct p2_python_container_contract_s *contract)
{
  uintptr_t fingerprint_start =
    (uintptr_t)__p2_python_fingerprint_start;
  uintptr_t fingerprint_end = (uintptr_t)__p2_python_fingerprint_end;
  uintptr_t slot_start = (uintptr_t)__p2_overlay_slot_start;
  uintptr_t slot_end = (uintptr_t)__p2_overlay_slot_end;
  uintptr_t xdata_start = (uintptr_t)__p2_xdata_start;
  uintptr_t xbss_end = (uintptr_t)__p2_xbss_end;

  if (fingerprint_end - fingerprint_start !=
      P2_PYTHON_CONTAINER_FINGERPRINT_SIZE ||
      p2_python_fingerprint_is_zero(__p2_python_fingerprint_start) ||
      slot_start >= slot_end || slot_end > BOARD_P2_HUB_USABLE_END ||
      xdata_start < P2_PSRAM_UNIFIED_BASE ||
      xbss_end < xdata_start ||
      xbss_end > BOARD_P2_PYTHON_CONTAINER_BASE ||
      BOARD_P2_PYTHON_CONTAINER_BASE +
        BOARD_P2_PYTHON_CONTAINER_CAPACITY >
        P2_PSRAM_UNIFIED_BASE +
          CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE)
    {
      return -ENOEXEC;
    }

  memcpy(contract->build_fingerprint,
         __p2_python_fingerprint_start,
         sizeof(contract->build_fingerprint));
  contract->overlay_load_address = slot_start;
  contract->overlay_slot_size = slot_end - slot_start;
  return 0;
}

static int p2_python_claim(void)
{
  irqstate_t flags = up_irq_save();
  int ret;

  if (g_p2_python_state == P2_PYTHON_STATE_READY)
    {
      ret = 1;
    }
  else if (g_p2_python_state == P2_PYTHON_STATE_LOADING)
    {
      ret = -EBUSY;
    }
  else
    {
      g_p2_python_state = P2_PYTHON_STATE_LOADING;
      ret = 0;
    }

  up_irq_restore(flags);
  return ret;
}

static void p2_python_publish(bool ready)
{
  irqstate_t flags = up_irq_save();

  g_p2_python_state = ready ? P2_PYTHON_STATE_READY :
                              P2_PYTHON_STATE_EMPTY;
  up_irq_restore(flags);
}

static int p2_python_receive(int fd, uint32_t size, uint32_t expected_crc)
{
  uint8_t frame_header[P2_PYTHON_UPLOAD_FRAME_HEADER];
  uint8_t buffer[P2_PYTHON_UPLOAD_FRAME_SIZE];
  uint8_t ack[8] =
  {
    'P', '2', 'A', 'K', 0, 0, 0, 0
  };
  uint8_t nack[8] =
  {
    'P', '2', 'N', 'K', 0, 0, 0, 0
  };

  uint32_t received = 0;
  uint32_t crc = UINT32_C(0xffffffff);
  clock_t started = clock_systime_ticks();

  while (received < size)
    {
      uint32_t expected_size = size - received;
      unsigned int retransmissions;

      if (expected_size > sizeof(buffer))
        {
          expected_size = sizeof(buffer);
        }

      /* Stop-and-wait is deliberately asymmetric: only an explicit NACK at
       * this committed offset authorizes the host to retransmit.  A missing
       * response is terminal because a valid frame advances received before
       * its ACK is emitted and duplicate committed frames are not accepted.
       */

      for (retransmissions = 0;
           retransmissions <= P2_PYTHON_UPLOAD_RETRANSMISSIONS;
           retransmissions++)
        {
          clock_t elapsed;
          clock_t frame_started;
          clock_t frame_timeout;
          uint32_t frame_offset;
          uint32_t frame_size;
          uint32_t frame_crc;
          uint32_t calculated_crc;
          bool valid_header;
          int ret;

          elapsed = clock_systime_ticks() - started;
          if (elapsed >= P2_PYTHON_UPLOAD_TIMEOUT)
            {
              return -ETIMEDOUT;
            }

          /* A retry receives a whole new frame under a fresh per-frame
           * deadline.  Cap that deadline at the remaining overall upload
           * time so retries cannot extend the destructive transaction.
           */

          frame_started = clock_systime_ticks();
          elapsed = frame_started - started;
          if (elapsed >= P2_PYTHON_UPLOAD_TIMEOUT)
            {
              return -ETIMEDOUT;
            }

          frame_timeout = P2_PYTHON_FRAME_TIMEOUT;
          if (frame_timeout > P2_PYTHON_UPLOAD_TIMEOUT - elapsed)
            {
              frame_timeout = P2_PYTHON_UPLOAD_TIMEOUT - elapsed;
            }

          ret = p2_python_read_exact(fd, frame_header,
                                     sizeof(frame_header), frame_started,
                                     frame_timeout);
          if (ret < 0)
            {
              return ret;
            }

          /* Never use an untrusted frame_size to consume the stream.  The
           * protocol has one known payload size for this offset, including
           * the possibly-short final frame.  Reading it before inspecting
           * the header drains a substitution-corrupted frame that retained
           * the expected wire length.  Byte insertion, deletion, or a
           * partial-frame timeout cannot be realigned safely and eventually
           * fails the bounded transfer.
           */

          ret = p2_python_read_exact(fd, buffer, expected_size,
                                     frame_started, frame_timeout);
          if (ret < 0)
            {
              return ret;
            }

          frame_offset = p2_python_getle32(frame_header);
          frame_size = p2_python_getle32(frame_header + 4);
          frame_crc = p2_python_getle32(frame_header + 8);
          calculated_crc =
            p2_python_crc32_update(UINT32_C(0xffffffff), buffer,
                                   expected_size) ^ UINT32_C(0xffffffff);
          valid_header = frame_offset == received &&
                         frame_size == expected_size;

          if (!valid_header || calculated_crc != frame_crc)
            {
              int frame_error = valid_header ? -EBADMSG : -EPROTO;

              p2_python_putle32(nack + 4, received);
              ret = p2_python_write_all(STDOUT_FILENO, nack, sizeof(nack));
              if (ret < 0)
                {
                  return ret;
                }

              if (retransmissions == P2_PYTHON_UPLOAD_RETRANSMISSIONS)
                {
                  return frame_error;
                }

              continue;
            }

          /* The committed offset changes only after validation and a
           * successful PSRAM write.  An ACK therefore never advertises data
           * that was rejected or could not be stored.
           */

          ret = p2_python_target_write(
            NULL, BOARD_P2_PYTHON_CONTAINER_BASE + received,
            buffer, expected_size);
          if (ret < 0)
            {
              return ret;
            }

          crc = p2_python_crc32_update(crc, buffer, expected_size);
          received += expected_size;
          p2_python_putle32(ack + 4, received);
          ret = p2_python_write_all(STDOUT_FILENO, ack, sizeof(ack));
          if (ret < 0)
            {
              return ret;
            }

          break;
        }
    }

  return (crc ^ UINT32_C(0xffffffff)) == expected_crc ? 0 : -EBADMSG;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int board_cpython_runtime_prepare(int fd)
{
  struct p2_python_container_config_s config;
  struct p2_python_source_s source;
  struct termios saved_termios;
  struct termios raw_termios;
  uint8_t header[P2_PYTHON_UPLOAD_HEADER_SIZE];
  FAR const char *stage = "CLAIM";
  bool input_purged = false;
  bool raw_console = false;
  uint32_t file_size;
  uint32_t file_crc;
  uint16_t protocol;
  uint16_t header_size;
  clock_t started;
  int ret;

  ret = p2_python_claim();
  if (ret > 0)
    {
      return 0;
    }

  if (ret < 0)
    {
      return ret;
    }

  memset(&config, 0, sizeof(config));
  stage = "CONTRACT";
  ret = p2_python_contract(&config.contract);
  if (ret < 0)
    {
      goto fail;
    }

  stage = "TERMIOS_RAW";
  if (tcgetattr(fd, &saved_termios) < 0)
    {
      ret = -errno;
      goto fail;
    }

  raw_termios = saved_termios;
  cfmakeraw(&raw_termios);
  if (tcsetattr(fd, TCSANOW, &raw_termios) < 0)
    {
      ret = -errno;
      goto fail;
    }

  raw_console = true;
  stage = "RX_BASELINE";
  if (g_p2_uart_rx_dropped != 0)
    {
      ret = -EOVERFLOW;
      goto fail;
    }

  p2_python_marker("P2PY:UPLOAD:READY:PROTO=%u:BASE=%08lX:MAX=%lu:"
                   "FRAME=%u:BAUD=%u\r\n",
                   P2_PYTHON_UPLOAD_PROTOCOL,
                   (unsigned long)BOARD_P2_PYTHON_CONTAINER_BASE,
                   (unsigned long)BOARD_P2_PYTHON_CONTAINER_CAPACITY,
                   P2_PYTHON_UPLOAD_FRAME_SIZE, CONFIG_UART0_BAUD);

  stage = "HEADER";
  started = clock_systime_ticks();
  ret = p2_python_read_exact(fd, header, sizeof(header), started,
                             P2_PYTHON_HEADER_TIMEOUT);
  if (ret < 0)
    {
      goto fail;
    }

  protocol = p2_python_getle16(header + 8);
  header_size = p2_python_getle16(header + 10);
  file_size = p2_python_getle32(header + 12);
  file_crc = p2_python_getle32(header + 16);
  if (memcmp(header, g_p2_python_upload_magic,
             sizeof(g_p2_python_upload_magic)) != 0 ||
      protocol != P2_PYTHON_UPLOAD_PROTOCOL ||
      header_size != sizeof(header) || p2_python_getle32(header + 20) != 0 ||
      file_size < P2_PYTHON_CONTAINER_HEADER_SIZE ||
      file_size > BOARD_P2_PYTHON_CONTAINER_CAPACITY)
    {
      ret = -EPROTO;
      goto fail;
    }

  p2_python_marker("P2PY:UPLOAD:ACCEPT:SIZE=%lu:CRC=%08lX\r\n",
                   (unsigned long)file_size, (unsigned long)file_crc);

  stage = "TRANSFER";
  ret = p2_python_receive(fd, file_size, file_crc);
  if (ret < 0)
    {
      goto fail;
    }

  stage = "RX_DROPS";
  if (g_p2_uart_rx_dropped != 0)
    {
      ret = -EOVERFLOW;
      goto fail;
    }

  stage = "INPUT_PURGE";
  ret = p2_python_purge_input(fd);
  if (ret < 0)
    {
      goto fail;
    }

  input_purged = true;
  stage = "TERMIOS_RESTORE";
  if (tcsetattr(fd, TCSANOW, &saved_termios) < 0)
    {
      ret = -errno;
      goto fail;
    }

  raw_console = false;

  source.base = BOARD_P2_PYTHON_CONTAINER_BASE;
  source.size = file_size;
  config.source.read = p2_python_source_read;
  config.source.arg = &source;
  config.source.size = file_size;
  config.target.read = p2_python_target_read;
  config.target.write = p2_python_target_write;
  config.target.zero = p2_python_target_zero;
  config.backing_address = BOARD_P2_PYTHON_CONTAINER_BASE;
  config.backing_capacity = BOARD_P2_PYTHON_CONTAINER_CAPACITY;
  config.group_workspace = g_p2_python_groups;
  config.group_workspace_count = CONFIG_P2_HUB_OVERLAY_GROUP_COUNT + 1;

  stage = "INITIALIZE";
  ret = p2_python_container_initialize(&g_p2_python_container, &config);
  if (ret < 0)
    {
      goto fail;
    }

  p2_python_publish(true);
  p2_python_marker("P2PY:UPLOAD:PASS:SIZE=%lu:CRC=%08lX:RXDROPS=0\r\n",
                   (unsigned long)file_size, (unsigned long)file_crc);
  p2_python_marker("P2PY:RUNTIME:READY:ROMFS=%lu:GROUPS=%lu:"
                   "SLOT=%08lX+%lu\r\n",
                   (unsigned long)g_p2_python_container.stdlib_romfs_size,
                   (unsigned long)g_p2_python_container.group_count,
                   (unsigned long)g_p2_python_container.overlay_load_address,
                   (unsigned long)g_p2_python_container.overlay_slot_size);
  return 0;

fail:
  if (raw_console)
    {
      int restore;

      if (!input_purged)
        {
          int purge = p2_python_purge_input(fd);

          if (purge < 0 && ret >= 0)
            {
              stage = "INPUT_PURGE";
              ret = purge;
            }
        }

      restore = tcsetattr(fd, TCSANOW, &saved_termios);

      if (restore < 0 && ret >= 0)
        {
          stage = "TERMIOS_RESTORE";
          ret = -errno;
        }
    }

  p2_python_publish(false);
  p2_python_marker("P2PY:UPLOAD:FAIL:STAGE=%s:CODE=%d\r\n", stage, ret);
  return ret;
}

int board_cpython_tmpfs_validate(void)
{
  struct statfs filesystem;

  if (statfs(CONFIG_LIBC_TMPDIR, &filesystem) < 0)
    {
      return -errno;
    }

  if (filesystem.f_type != TMPFS_MAGIC)
    {
      return -ENODEV;
    }

  return 0;
}

int board_cpython_romfs_image(FAR const uint8_t **image,
                              FAR size_t *length)
{
  if (g_p2_python_state != P2_PYTHON_STATE_READY)
    {
      return -ENODEV;
    }

  return p2_python_container_get_stdlib(&g_p2_python_container,
                                        (FAR const void **)image, length);
}

int board_cpython_romdisk_register(int minor, FAR const uint8_t *image,
                                   uint32_t nsectors, uint16_t sectsize)
{
  uint64_t size = (uint64_t)nsectors * sectsize;
  uintptr_t start = (uintptr_t)image;

  /* A unified-PSRAM tag is a valid data pointer only through p2llvm's
   * lowered accesses.  It is not a CPU-addressable XIP mapping.  Register a
   * normal buffered RAM disk and explicitly suppress BIOC_XIPBASE so ROMFS
   * reads sectors through rd_read() and the unified memcpy helper.
   */

  if (minor < 0 || minor > UINT8_MAX || image == NULL || nsectors == 0 ||
      sectsize == 0 || start < BOARD_P2_PYTHON_CONTAINER_BASE ||
      start >= BOARD_P2_PYTHON_CONTAINER_BASE +
                 BOARD_P2_PYTHON_CONTAINER_CAPACITY ||
      size > BOARD_P2_PYTHON_CONTAINER_BASE +
               BOARD_P2_PYTHON_CONTAINER_CAPACITY - start)
    {
      return -EINVAL;
    }

  return ramdisk_register(minor, (FAR uint8_t *)image, nsectors, sectsize,
                          RDFLAG_NO_XIP);
}
