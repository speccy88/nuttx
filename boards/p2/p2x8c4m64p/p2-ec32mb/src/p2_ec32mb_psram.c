/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_psram.c
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

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <nuttx/arch.h>
#include <nuttx/clock.h>
#include <nuttx/compiler.h>
#include <nuttx/fs/fs.h>
#include <nuttx/irq.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>

#include <arch/board/board.h>
#include <arch/board/p2_ec32mb_psram.h>
#include <arch/chip/chip.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_psram_logic.h"
#include "p2_ec32mb_psram_wire.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#ifndef CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE
#  define CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE 3072
#endif

#ifndef CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST
#  define CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST 65536
#endif

#ifndef CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS
#  define CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS 500
#endif

#ifndef CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS
#  define CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS 100
#endif

#define P2_PSRAM_STACK_GUARD       UINT32_C(0x51ac0bad)
#define P2_PSRAM_STACK_WORDS       \
  (CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE / sizeof(uint32_t))
#define P2_PSRAM_STACK_ARRAY_WORDS (P2_PSRAM_STACK_WORDS + 2)
#define P2_PSRAM_READY_WAIT_TICKS  SEC2TICK(2)
#define P2_PSRAM_POLL_USEC         1000
#define P2_PSRAM_COG_NONE          UINT32_MAX
#define P2_PSRAM_PIN_COUNT         \
  (P2_PSRAM_CE_PIN - P2_PSRAM_DATA_FIRST_PIN + 1)

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
#  define P2_PSRAM_UNIFIED_TIMEOUT_CYCLES \
  (UINT32_C(2) * CONFIG_P2_SYSCLK_HZ)
#  define P2_PSRAM_UNIFIED_GRACE_CYCLES \
  (CONFIG_P2_SYSCLK_HZ / UINT32_C(10))
#endif

#if CONFIG_P2_SYSCLK_HZ != 180000000
#  error "P2 PSRAM timing leaf is qualified only at 180 MHz"
#endif

#if BOARD_PSRAM_FIRST_PIN != P2_PSRAM_DATA_FIRST_PIN || \
    BOARD_PSRAM_LAST_PIN != P2_PSRAM_CE_PIN
#  error "P2 PSRAM board pins do not match the fixed timing leaf"
#endif

#if CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE < 2048 || \
    CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE > 8192 || \
    CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE % 4 != 0
#  error "P2 PSRAM service stack must be a 2-8 KiB long-aligned region"
#endif

#if CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST < 4096 || \
    CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST > 65536
#  error "P2 PSRAM request bound must be between 4 KiB and 64 KiB"
#endif

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
#  ifdef CONFIG_SMP
#    error "P2 unified PSRAM requires a uniprocessor NuttX build"
#  endif
#  ifndef CONFIG_BUILD_FLAT
#    error "P2 unified PSRAM currently requires CONFIG_BUILD_FLAT"
#  endif
#  ifndef CONFIG_MM_KERNEL_HEAP
#    error "P2 unified PSRAM requires a dedicated Hub kernel heap"
#  endif
#  if CONFIG_MM_REGIONS != 2
#    error "P2 unified PSRAM requires exactly two user-heap regions"
#  endif
#  ifndef CONFIG_ARCH_HAVE_EXTRA_HEAPS
#    error "P2 unified PSRAM requires the extra-heaps initialization hook"
#  endif
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_psram_wire_s
{
  volatile uint32_t operation;
  volatile uint32_t address;
  volatile uint32_t tx_lanes;
  volatile uint32_t rx_lanes;
  volatile int32_t status;
  volatile uint32_t ce_cycles;
};

struct p2_psram_service_s
{
  mutex_t mutex;
  struct p2_psram_request_s request;
  volatile uint32_t cancel_sequence;
  volatile int32_t ready;
  volatile uint32_t service_cog;
  volatile uint32_t max_ce_cycles;
  volatile uint32_t start_allowed;
  uint32_t next_sequence;
  int hardware_lock;
  bool registered;
  bool failed;
#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
  volatile uint32_t inject_raw_lock_stall;
#endif
};

struct p2_psram_worker_request_s
{
  uint32_t sequence;
  uint32_t operation;
  uint32_t address;
  FAR uint8_t *buffer;
  uint32_t length;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
static ssize_t p2_psram_read(FAR struct file *filep, FAR char *buffer,
                             size_t buflen);
static ssize_t p2_psram_write(FAR struct file *filep,
                              FAR const char *buffer, size_t buflen);
static off_t p2_psram_seek(FAR struct file *filep, off_t offset,
                           int whence);
#endif

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int p2_psram_cog_start(void);
void p2_psram_timing_leaf(void);
void p2_psram_service_worker(void);

/****************************************************************************
 * Public Data
 ****************************************************************************/

/* These symbols are consumed by p2_ec32mb_psram_service.S.  The first and
 * last longs are guards; the assembly sets PTRA to the second long because
 * the p2llvm stack grows upward.
 */

volatile struct p2_psram_wire_s g_p2_psram_wire;
uint32_t g_p2_psram_service_stack[P2_PSRAM_STACK_ARRAY_WORDS]
  aligned_data(16);

/****************************************************************************
 * Private Data
 ****************************************************************************/

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
static const struct file_operations g_p2_psram_fops =
{
  .open = NULL,
  .close = NULL,
  .read = p2_psram_read,
  .write = p2_psram_write,
  .seek = p2_psram_seek,
  .ioctl = NULL,
  .mmap = NULL,
  .truncate = NULL,
  .poll = NULL,
};
#endif

static struct p2_psram_service_s g_p2_psram =
{
  .mutex = NXMUTEX_INITIALIZER,
  .service_cog = P2_PSRAM_COG_NONE,
  .hardware_lock = -1,
};

static_assert(sizeof(struct p2_psram_wire_s) == P2_PSRAM_WIRE_SIZE,
              "PSRAM C/PASM wire structure size drifted");
static_assert(offsetof(struct p2_psram_wire_s, operation) ==
              P2_PSRAM_WIRE_OPERATION_OFFSET,
              "PSRAM wire operation offset drifted");
static_assert(offsetof(struct p2_psram_wire_s, address) ==
              P2_PSRAM_WIRE_ADDRESS_OFFSET,
              "PSRAM wire address offset drifted");
static_assert(offsetof(struct p2_psram_wire_s, tx_lanes) ==
              P2_PSRAM_WIRE_TX_LANES_OFFSET,
              "PSRAM wire TX offset drifted");
static_assert(offsetof(struct p2_psram_wire_s, rx_lanes) ==
              P2_PSRAM_WIRE_RX_LANES_OFFSET,
              "PSRAM wire RX offset drifted");
static_assert(offsetof(struct p2_psram_wire_s, status) ==
              P2_PSRAM_WIRE_STATUS_OFFSET,
              "PSRAM wire status offset drifted");
static_assert(offsetof(struct p2_psram_wire_s, ce_cycles) ==
              P2_PSRAM_WIRE_CE_CYCLES_OFFSET,
              "PSRAM wire CE timing offset drifted");
static_assert(offsetof(struct p2_psram_service_s, cancel_sequence) %
              sizeof(uint32_t) == 0,
              "PSRAM cancellation word must remain long-aligned");

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline void p2_psram_compiler_barrier(void)
{
  __asm__ __volatile__("" : : : "memory");
}

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
static inline uint32_t p2_psram_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}
#endif

static inline bool p2_psram_raw_trylock(void)
{
  unsigned int acquired;

  __asm__ __volatile__("locktry %1 wc\n\twrc %0"
                       : "=r" (acquired)
                       : "r" (g_p2_psram.hardware_lock));
  if (acquired != 0)
    {
      p2_psram_compiler_barrier();
    }

  return acquired != 0;
}

static inline void p2_psram_raw_lock(void)
{
  while (!p2_psram_raw_trylock())
    {
    }
}

static inline void p2_psram_raw_unlock(void)
{
  p2_psram_compiler_barrier();
  __asm__ __volatile__("lockrel %0"
                       :
                       : "r" (g_p2_psram.hardware_lock)
                       : "memory");
}

static irqstate_t p2_psram_task_lock(void)
{
  irqstate_t flags = up_irq_save();

  p2_psram_raw_lock();
  return flags;
}

static void p2_psram_task_unlock(irqstate_t flags)
{
  p2_psram_raw_unlock();
  up_irq_restore(flags);
}

static int p2_psram_locknew(void)
{
  unsigned int id;
  unsigned int failed;

  __asm__ __volatile__("locknew %0 wc\n\twrc %1"
                       : "=r" (id), "=r" (failed));
  return failed != 0 ? -ENOSPC : (int)id;
}

static void p2_psram_lockfree(void)
{
  int id = g_p2_psram.hardware_lock;

  if (id >= 0)
    {
      /* COGSTOP does not return or clear locks allocated by the stopped cog.
       * Clear a possibly-held lock before returning its ID to the pool.
       */

      __asm__ __volatile__("lockrel %0\n\tlockret %0"
                           :
                           : "r" (id)
                           : "memory");
      g_p2_psram.hardware_lock = -1;
    }
}

static bool p2_psram_stack_valid(void)
{
  return g_p2_psram_service_stack[0] == P2_PSRAM_STACK_GUARD &&
         g_p2_psram_service_stack[P2_PSRAM_STACK_ARRAY_WORDS - 1] ==
         P2_PSRAM_STACK_GUARD;
}

static void p2_psram_park_failed_cog(void) noreturn_function;
static void p2_psram_park_failed_cog(void)
{
  /* Keep the allocated cog alive until the parent stops its known ID.  A
   * failed worker must not self-stop and let that ID be reused underneath a
   * delayed COGSTOP in the parent.
   */

  for (; ; )
    {
      __asm__ __volatile__("waitx #200");
    }
}

static void p2_psram_force_safe(void)
{
  unsigned int pin;

  /* A stopped cog releases its DIR contribution.  Hold CE high and CLK low
   * from the NuttX cog briefly, float every data pin, then float controls.
   * The board's 100-Kohm CE pull-up preserves standby afterwards.
   */

  for (pin = P2_PSRAM_DATA_FIRST_PIN; pin <= P2_PSRAM_CE_PIN; pin++)
    {
      __asm__ __volatile__("dirl %0" : : "r" (pin));
      __asm__ __volatile__("wrpin #0, %0" : : "r" (pin));
    }

  __asm__ __volatile__("outl %0\n\tdirh %0"
                       :
                       : "r" (P2_PSRAM_CLOCK_PIN));
  __asm__ __volatile__("outh %0\n\tdirh %0"
                       :
                       : "r" (P2_PSRAM_CE_PIN));
  up_udelay(1);
  __asm__ __volatile__("dirl %0" : : "r" (P2_PSRAM_CLOCK_PIN));
  __asm__ __volatile__("dirl %0" : : "r" (P2_PSRAM_CE_PIN));
}

static void p2_psram_stop_failed_cog(void)
{
  irqstate_t flags;
  uint32_t cog;

  /* A failed service cog may be parked while it owns hardware_lock.
   * Recovery must therefore never acquire that lock before COGSTOP.  Exclude
   * every NuttX-side observer first, stop and forget the exact known cog
   * under the independent pin-manager transaction, and only then
   * release/return the orphaned hardware lock and publish terminal state.
   */

  flags = up_irq_save();
  cog = g_p2_psram.service_cog;
  if (cog < P2_PIN_COG_COUNT)
    {
      int released;

      /* The pin-manager lock covers COGSTOP, electrical safety, and metadata
       * cleanup.  No new generation of this cog ID can acquire a stale PSRAM
       * claim in the middle of the transaction.
       */

      released = p2_pin_stop_and_forget_cog(cog, P2_PIN_OWNER_PSRAM,
                                             p2_psram_force_safe);
      if (released < 0)
        {
          /* Invalid pin-manager state must not leave a failed service cog
           * running or driving the memory bus.
           */

          __asm__ __volatile__("cogstop %0" : : "r" (cog));
          p2_psram_force_safe();
        }
    }
  else
    {
      p2_psram_force_safe();
    }

  p2_psram_lockfree();
  g_p2_psram.service_cog = P2_PSRAM_COG_NONE;
  g_p2_psram.start_allowed = 0;
  g_p2_psram.cancel_sequence = 0;
  g_p2_psram.failed = true;
  if (g_p2_psram.ready >= 0)
    {
      g_p2_psram.ready = -ETIMEDOUT;
    }

  up_irq_restore(flags);
}

static int p2_psram_track_pin(unsigned int pin,
                              enum p2_pin_direction_e direction,
                              enum p2_pin_safe_e safe)
{
  struct p2_pin_config_s config;

  config.direction = direction;
  config.drive = direction == P2_PIN_DIRECTION_INPUT ?
                 P2_PIN_DRIVE_FLOAT : P2_PIN_DRIVE_PUSH_PULL;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = safe;
  config.smartpin_mode = P2_SMARTPIN_MODE_DISABLED;
  return p2_pin_configure(pin, P2_PIN_OWNER_PSRAM, &config);
}

static int p2_psram_claim_pins(void)
{
  unsigned int claimed = 0;
  unsigned int pin;
  int ret;

  for (pin = P2_PSRAM_DATA_FIRST_PIN; pin <= P2_PSRAM_CE_PIN; pin++)
    {
      enum p2_pin_direction_e direction =
        pin <= P2_PSRAM_DATA_LAST_PIN ?
        P2_PIN_DIRECTION_BIDIRECTIONAL : P2_PIN_DIRECTION_OUTPUT;
      enum p2_pin_safe_e safe = pin == P2_PSRAM_CE_PIN ?
        P2_PIN_SAFE_HIGH : (pin == P2_PSRAM_CLOCK_PIN ?
                            P2_PIN_SAFE_LOW : P2_PIN_SAFE_FLOAT);

      ret = p2_pin_claim(pin, P2_PIN_OWNER_PSRAM);
      if (ret < 0)
        {
          goto errout;
        }

      claimed++;
      ret = p2_psram_track_pin(pin, direction, safe);
      if (ret < 0)
        {
          goto errout;
        }
    }

  return 0;

errout:
  while (claimed > 0)
    {
      claimed--;
      p2_pin_release(P2_PSRAM_DATA_FIRST_PIN + claimed,
                     P2_PIN_OWNER_PSRAM);
    }

  return ret;
}

static void p2_psram_release_pins(void)
{
  unsigned int pin;

  for (pin = P2_PSRAM_CE_PIN + 1; pin > P2_PSRAM_DATA_FIRST_PIN; )
    {
      pin--;
      p2_pin_release(pin, P2_PIN_OWNER_PSRAM);
    }
}

static int p2_psram_wire_operation(uint32_t operation, uint32_t address,
                                   uint32_t tx_lanes,
                                   FAR uint32_t *rx_lanes)
{
  uint32_t ce_cycles;
  int ret;

  g_p2_psram_wire.operation = operation;
  g_p2_psram_wire.address = address;
  g_p2_psram_wire.tx_lanes = tx_lanes;
  g_p2_psram_wire.rx_lanes = 0;
  g_p2_psram_wire.status = -EINPROGRESS;
  g_p2_psram_wire.ce_cycles = 0;
  p2_psram_compiler_barrier();

  p2_psram_timing_leaf();

  p2_psram_compiler_barrier();
  ret = g_p2_psram_wire.status;
  ce_cycles = g_p2_psram_wire.ce_cycles;

  if (ce_cycles > g_p2_psram.max_ce_cycles)
    {
      p2_psram_raw_lock();
      if (ce_cycles > g_p2_psram.max_ce_cycles)
        {
          g_p2_psram.max_ce_cycles = ce_cycles;
        }

      p2_psram_raw_unlock();
    }

  if (ce_cycles > P2_PSRAM_CE_LOW_LIMIT_CYCLES)
    {
      return -EIO;
    }

  if (ret >= 0 && rx_lanes != NULL)
    {
      *rx_lanes = g_p2_psram_wire.rx_lanes;
    }

  return ret;
}

static int p2_psram_read_word(uint32_t chip_address, uint8_t bytes[4])
{
  uint32_t lanes;
  int ret;

  ret = p2_psram_wire_operation(P2_PSRAM_WIRE_READ_WORD,
                                chip_address, 0, &lanes);
  if (ret >= 0)
    {
      p2_psram_unpack_lanes(lanes, bytes);
    }

  return ret;
}

static int p2_psram_write_word(uint32_t chip_address,
                               const uint8_t bytes[4])
{
  return p2_psram_wire_operation(P2_PSRAM_WIRE_WRITE_WORD,
                                 chip_address,
                                 p2_psram_pack_lanes(bytes), NULL);
}

static bool p2_psram_cancelled(uint32_t sequence)
{
  bool cancelled;

  /* cancel_sequence is one aligned volatile Hub long.  P2 Hub longs are
   * coherent between cogs, so the worker can sample cancellation without
   * contending for the descriptor lock once per four-byte wire word.  The
   * NuttX cog still publishes and clears the value under that lock.
   */

  p2_psram_compiler_barrier();
  cancelled = g_p2_psram.cancel_sequence == sequence;
  p2_psram_compiler_barrier();
  return cancelled;
}

static int p2_psram_execute(FAR const struct p2_psram_worker_request_s *req)
{
  uint8_t word[4];
  FAR uint8_t *buffer = req->buffer;
  uint32_t address = req->address;
  uint32_t remaining = req->length;
  int ret;

  while (remaining > 0)
    {
      uint32_t aligned = address & ~UINT32_C(3);
      unsigned int first = p2_psram_chip_index(address);
      unsigned int count = 4u - first;

      if (count > remaining)
        {
          count = remaining;
        }

      if (!p2_psram_stack_valid())
        {
          return -EOVERFLOW;
        }

      if (p2_psram_cancelled(req->sequence))
        {
          return -ECANCELED;
        }

      if (req->operation == P2_PSRAM_OPERATION_READ ||
          first != 0 || count != sizeof(word))
        {
          ret = p2_psram_read_word(p2_psram_chip_address(aligned), word);
          if (ret < 0)
            {
              return ret;
            }
        }

      if (req->operation == P2_PSRAM_OPERATION_READ)
        {
          memcpy(buffer, &word[first], count);
        }
      else if (req->operation == P2_PSRAM_OPERATION_WRITE)
        {
          memcpy(&word[first], buffer, count);
          ret = p2_psram_write_word(p2_psram_chip_address(aligned), word);
          if (ret < 0)
            {
              return ret;
            }
        }
      else
        {
          return -EINVAL;
        }

      address += count;
      buffer += count;
      remaining -= count;
    }

  return 0;
}

static bool p2_psram_take_request(
  FAR struct p2_psram_worker_request_s *worker)
{
  FAR struct p2_psram_request_s *request = &g_p2_psram.request;
  bool available = false;

  p2_psram_raw_lock();
  if (request->completion == P2_PSRAM_COMPLETION_SUBMITTED)
    {
      worker->sequence = request->sequence;
      worker->operation = request->operation;
      worker->address = request->external_address;
      worker->buffer = (FAR uint8_t *)request->hub_buffer;
      worker->length = request->length;
      request->completion = P2_PSRAM_COMPLETION_ACTIVE;
      available = true;
#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
      if (g_p2_psram.inject_raw_lock_stall != 0)
        {
          /* Deliberately retain hardware_lock.  The parent timeout path must
           * remain independent of this lock, COGSTOP this worker, and
           * reclaim the orphaned lock before it reports terminal failure.
           */

          g_p2_psram.inject_raw_lock_stall = 0;
          p2_psram_compiler_barrier();
          p2_psram_park_failed_cog();
        }
#endif
    }

  p2_psram_raw_unlock();
  return available;
}

static void p2_psram_complete(uint32_t sequence, int status)
{
  FAR struct p2_psram_request_s *request = &g_p2_psram.request;

  p2_psram_raw_lock();
  if (request->sequence == sequence)
    {
      request->status = status;
      request->completion = P2_PSRAM_COMPLETION_DONE;
      request->completion_sequence = sequence;
      if (g_p2_psram.cancel_sequence == sequence)
        {
          g_p2_psram.cancel_sequence = 0;
        }
    }

  p2_psram_raw_unlock();
}

static uint32_t p2_psram_next_sequence(void)
{
  g_p2_psram.next_sequence++;
  if (g_p2_psram.next_sequence == 0)
    {
      g_p2_psram.next_sequence++;
    }

  return g_p2_psram.next_sequence;
}

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
static int p2_psram_wait(uint32_t sequence, uint32_t timeout_ticks)
{
  FAR struct p2_psram_request_s *request = &g_p2_psram.request;
  clock_t deadline;
  clock_t grace_deadline = 0;
  bool timed_out = false;
  int status = -EINPROGRESS;

  deadline = clock_systime_ticks() + timeout_ticks;
  for (; ; )
    {
      clock_t now = clock_systime_ticks();
      irqstate_t flags;
      bool complete = false;
      bool locked;

      /* Completion and the transition to cancellation share one critical
       * section.  A completion which wins this lock cannot be followed by a
       * stale late cancel for the same sequence.  Never wait for this lock:
       * the service cog is one possible owner, so a wedged owner must not
       * stop the timeout clock or prevent the parent from issuing COGSTOP.
       */

      flags = up_irq_save();
      locked = p2_psram_raw_trylock();
      if (locked)
        {
          if (request->completion_sequence == sequence &&
              request->completion == P2_PSRAM_COMPLETION_DONE)
            {
              status = request->status;
              if (g_p2_psram.cancel_sequence == sequence)
                {
                  g_p2_psram.cancel_sequence = 0;
                }

              complete = true;
            }
          else if (!timed_out && clock_compare(deadline, now))
            {
              g_p2_psram.cancel_sequence = sequence;
              timed_out = true;
              grace_deadline = now +
                CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS;
            }
          else if (timed_out &&
                   g_p2_psram.cancel_sequence != sequence)
            {
              g_p2_psram.cancel_sequence = sequence;
            }

          p2_psram_raw_unlock();
        }

      up_irq_restore(flags);

      if (complete)
        {
          return timed_out ? -ETIMEDOUT : status;
        }

      if (!timed_out && clock_compare(deadline, now))
        {
          /* The raw lock was unavailable at the deadline.  Start the bounded
           * grace interval anyway and publish cancellation on a later
           * successful try-lock, if one occurs.
           */

          timed_out = true;
          grace_deadline = now +
            CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS;
        }

      if (timed_out && clock_compare(grace_deadline, now))
        {
          p2_psram_stop_failed_cog();
          return -ETIMEDOUT;
        }

      nxsig_usleep(P2_PSRAM_POLL_USEC);
    }
}

static ssize_t p2_psram_file_transfer(FAR struct file *filep,
                                      FAR void *buffer, size_t buflen,
                                      enum p2_psram_operation_e operation)
{
  uint32_t address;
  size_t transferred = 0;

  if (filep->f_pos < 0 || (uint64_t)filep->f_pos > P2_PSRAM_SIZE_BYTES)
    {
      return -EINVAL;
    }

  address = (uint32_t)filep->f_pos;
  if (buflen > P2_PSRAM_SIZE_BYTES - address)
    {
      buflen = P2_PSRAM_SIZE_BYTES - address;
    }

  while (transferred < buflen)
    {
      size_t chunk = buflen - transferred;
      ssize_t ret;

      if (chunk > CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST)
        {
          chunk = CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST;
        }

      ret = p2_psram_transfer(operation, address + transferred,
                              (FAR uint8_t *)buffer + transferred,
                              chunk,
                              CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS);
      if (ret < 0)
        {
          return transferred == 0 ? ret : (ssize_t)transferred;
        }

      transferred += ret;
    }

  filep->f_pos += transferred;
  return transferred;
}

static ssize_t p2_psram_read(FAR struct file *filep, FAR char *buffer,
                             size_t buflen)
{
  if (buffer == NULL && buflen != 0)
    {
      return -EINVAL;
    }

  return p2_psram_file_transfer(filep, buffer, buflen,
                                P2_PSRAM_OPERATION_READ);
}

static ssize_t p2_psram_write(FAR struct file *filep,
                              FAR const char *buffer, size_t buflen)
{
  if (buffer == NULL && buflen != 0)
    {
      return -EINVAL;
    }

  return p2_psram_file_transfer(filep, (FAR void *)buffer, buflen,
                                P2_PSRAM_OPERATION_WRITE);
}

static off_t p2_psram_seek(FAR struct file *filep, off_t offset, int whence)
{
  int64_t position;

  switch (whence)
    {
      case SEEK_SET:
        position = offset;
        break;

      case SEEK_CUR:
        position = (int64_t)filep->f_pos + offset;
        break;

      case SEEK_END:
        position = (int64_t)P2_PSRAM_SIZE_BYTES + offset;
        break;

      default:
        return -EINVAL;
    }

  if (position < 0 || position > P2_PSRAM_SIZE_BYTES)
    {
      return -EINVAL;
    }

  filep->f_pos = (off_t)position;
  return filep->f_pos;
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void p2_psram_service_worker(void)
{
  struct p2_psram_worker_request_s request;
  bool start_allowed;
  int ret;

  /* The NuttX cog owns and configures the pin claims before COGINIT, then
   * transfers them to this exact cog ID.  Do not touch either the pins or
   * the pin-manager lock until that transfer has completed.
   */

  do
    {
      p2_psram_raw_lock();
      start_allowed = g_p2_psram.start_allowed != 0;
      p2_psram_raw_unlock();
      if (!start_allowed)
        {
          __asm__ __volatile__("waitx #200");
        }
    }
  while (!start_allowed);

  ret = p2_psram_wire_operation(P2_PSRAM_WIRE_RECOVER, 0, 0, NULL);

  if (ret < 0)
    {
      /* Do not acquire the pin-manager lock on a failure path which the
       * parent may have to kill.  Publish the error and remain alive; the
       * parent owns the atomic stop/safe/forget transaction.
       */

      p2_psram_raw_lock();
      g_p2_psram.ready = ret;
      p2_psram_raw_unlock();
      p2_psram_park_failed_cog();
    }

  p2_psram_raw_lock();
  g_p2_psram.ready = 1;
  p2_psram_raw_unlock();

  for (; ; )
    {
      if (!p2_psram_take_request(&request))
        {
          /* Do not hammer the shared hardware lock from this dedicated cog.
           * A short idle backoff gives the NuttX cog a deterministic window
           * in which to publish the next descriptor.
           */

          __asm__ __volatile__("waitx #200");
          continue;
        }

      if (request.operation == P2_PSRAM_OPERATION_STOP)
        {
          p2_psram_complete(request.sequence, 0);
          break;
        }

      ret = p2_psram_execute(&request);
      if (ret == -EIO)
        {
          int recover = p2_psram_wire_operation(P2_PSRAM_WIRE_RECOVER,
                                                 0, 0, NULL);

          if (recover < 0)
            {
              ret = recover;
            }
        }

      p2_psram_complete(request.sequence, ret);
    }

  p2_psram_wire_operation(P2_PSRAM_WIRE_SAFE, 0, 0, NULL);
  p2_psram_release_pins();
}

int p2_psram_initialize(void)
{
#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
  clock_t deadline;
#else
  uint32_t waited;
#endif
  irqstate_t flags;
  int ready;
  int ret;

  ret = nxmutex_lock(&g_p2_psram.mutex);
  if (ret < 0)
    {
      return ret;
    }

  if (g_p2_psram.failed)
    {
      ret = -EIO;
      goto out_unlock;
    }

  if (g_p2_psram.registered)
    {
      ret = 0;
      goto out_unlock;
    }

  ret = p2_pin_initialize();
  if (ret < 0)
    {
      goto out_unlock;
    }

  ret = p2_psram_locknew();
  if (ret < 0)
    {
      goto out_unlock;
    }

  memset((FAR void *)&g_p2_psram.request, 0,
         sizeof(g_p2_psram.request));
  g_p2_psram.hardware_lock = ret;
  g_p2_psram.cancel_sequence = 0;
  g_p2_psram.ready = 0;
  g_p2_psram.service_cog = P2_PSRAM_COG_NONE;
  g_p2_psram.max_ce_cycles = 0;
  g_p2_psram.start_allowed = 0;
  g_p2_psram.next_sequence = 0;
#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
  g_p2_psram.inject_raw_lock_stall = 0;
#endif
  g_p2_psram_service_stack[0] = P2_PSRAM_STACK_GUARD;
  g_p2_psram_service_stack[P2_PSRAM_STACK_ARRAY_WORDS - 1] =
    P2_PSRAM_STACK_GUARD;

  ret = p2_psram_claim_pins();
  if (ret < 0)
    {
      g_p2_psram.failed = true;
      p2_psram_force_safe();
      p2_psram_lockfree();
      goto out_unlock;
    }

  ret = p2_psram_cog_start();
  if (ret < 0 || ret >= P2_PIN_COG_COUNT)
    {
      g_p2_psram.failed = true;
      p2_psram_release_pins();
      p2_psram_force_safe();
      p2_psram_lockfree();
      ret = -ENOSPC;
      goto out_unlock;
    }

  flags = p2_psram_task_lock();
  g_p2_psram.service_cog = (uint32_t)ret;
  p2_psram_task_unlock(flags);

  ret = p2_pin_transfer_claims(P2_PIN_OWNER_PSRAM,
                               g_p2_psram.service_cog,
                               P2_PSRAM_PIN_COUNT);
  if (ret != P2_PSRAM_PIN_COUNT)
    {
      ret = ret < 0 ? ret : -EIO;
      p2_psram_stop_failed_cog();
      p2_psram_release_pins();
      p2_psram_force_safe();
      goto out_unlock;
    }

  flags = p2_psram_task_lock();
  g_p2_psram.start_allowed = 1;
  p2_psram_task_unlock(flags);

  ready = 0;
#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
  deadline = clock_systime_ticks() + P2_PSRAM_READY_WAIT_TICKS;
  for (; ; )
    {
      flags = up_irq_save();
      if (p2_psram_raw_trylock())
        {
          ready = g_p2_psram.ready;
          p2_psram_raw_unlock();
        }

      up_irq_restore(flags);
      if (ready != 0 ||
          clock_compare(deadline, clock_systime_ticks()))
        {
          break;
        }

      nxsig_usleep(P2_PSRAM_POLL_USEC);
    }
#else
  /* The extra-heaps hook runs before up_initialize() has installed the P2
   * timer interrupt.  Wait with the free-running hardware counter instead of
   * depending on scheduler ticks or a signal sleep.
   */

  for (waited = 0; waited < 2000000; waited += P2_PSRAM_POLL_USEC)
    {
      flags = up_irq_save();
      if (p2_psram_raw_trylock())
        {
          ready = g_p2_psram.ready;
          p2_psram_raw_unlock();
        }

      up_irq_restore(flags);
      if (ready != 0)
        {
          break;
        }

      up_udelay(P2_PSRAM_POLL_USEC);
    }
#endif

  if (ready <= 0)
    {
      ret = ready < 0 ? ready : -ETIMEDOUT;
      p2_psram_stop_failed_cog();
      goto out_unlock;
    }

  if (!p2_psram_stack_valid())
    {
      p2_psram_stop_failed_cog();
      ret = -EOVERFLOW;
      goto out_unlock;
    }

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
  ret = register_driver(P2_PSRAM_DEVICE_PATH, &g_p2_psram_fops,
                        0660, NULL);
  if (ret < 0)
    {
      p2_psram_stop_failed_cog();
      goto out_unlock;
    }
#endif

  g_p2_psram.registered = true;
  ret = 0;

out_unlock:
  nxmutex_unlock(&g_p2_psram.mutex);
  return ret;
}

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
#  ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
int p2_psram_unified_arm_raw_lock_stall(void)
{
  irqstate_t flags;
  int ret = 0;

  flags = p2_psram_task_lock();
  if (!g_p2_psram.registered || g_p2_psram.failed ||
      g_p2_psram.ready != 1)
    {
      ret = -ENODEV;
    }
  else
    {
      g_p2_psram.inject_raw_lock_stall = 1;
    }

  p2_psram_task_unlock(flags);
  return ret;
}
#  endif

int p2_psram_unified_transfer(enum p2_psram_operation_e operation,
                              uint32_t external_address,
                              FAR void *hub_buffer, uint32_t length)
{
  FAR struct p2_psram_request_s *request = &g_p2_psram.request;
  uint32_t grace_deadline = 0;
  uint32_t sequence;
  uint32_t deadline;
  irqstate_t flags;
  bool timed_out = false;
  int status;

  if ((operation != P2_PSRAM_OPERATION_READ &&
       operation != P2_PSRAM_OPERATION_WRITE) ||
      (hub_buffer == NULL && length != 0) ||
      !p2_psram_range_valid(external_address, length) ||
      length > CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST)
    {
      return -EINVAL;
    }

  if (length == 0)
    {
      return 0;
    }

  if ((uintptr_t)hub_buffer >= BOARD_P2_HUB_USABLE_END ||
      length > BOARD_P2_HUB_USABLE_END - (uintptr_t)hub_buffer)
    {
      return -EINVAL;
    }

  /* On the supported UP port, masking interrupts excludes task switches and
   * nested IRQ accesses on the sole NuttX cog.  The service cog remains live
   * and consumes the Hub descriptor while this caller spins.  No semaphore,
   * mutex, scheduler tick, or signal service is used on this path.
   */

  flags = up_irq_save();
  if (!g_p2_psram.registered || g_p2_psram.failed ||
      g_p2_psram.ready != 1)
    {
      up_irq_restore(flags);
      return -ENODEV;
    }

  sequence = p2_psram_next_sequence();
  deadline = p2_psram_counter() + P2_PSRAM_UNIFIED_TIMEOUT_CYCLES;
  while (!p2_psram_raw_trylock())
    {
      if ((int32_t)(p2_psram_counter() - deadline) >= 0)
        {
          p2_psram_stop_failed_cog();
          up_irq_restore(flags);
          return -ETIMEDOUT;
        }
    }

  if (request->completion == P2_PSRAM_COMPLETION_SUBMITTED ||
      request->completion == P2_PSRAM_COMPLETION_ACTIVE)
    {
      p2_psram_raw_unlock();
      up_irq_restore(flags);
      return -EBUSY;
    }

  request->completion_sequence = 0;
  g_p2_psram.cancel_sequence = 0;
  request->sequence = sequence;
  request->operation = operation;
  request->external_address = external_address;
  request->hub_buffer = (uintptr_t)hub_buffer;
  request->length = length;
  request->status = -EINPROGRESS;
  request->timeout_ticks = 0;
  request->completion = P2_PSRAM_COMPLETION_SUBMITTED;
  p2_psram_raw_unlock();

  deadline = p2_psram_counter() + P2_PSRAM_UNIFIED_TIMEOUT_CYCLES;
  for (; ; )
    {
      uint32_t now = p2_psram_counter();
      bool complete = false;

      if (p2_psram_raw_trylock())
        {
          complete = request->completion_sequence == sequence &&
                     request->completion == P2_PSRAM_COMPLETION_DONE;
          status = complete ? request->status : -EINPROGRESS;
          if (!complete && !timed_out &&
              (int32_t)(now - deadline) >= 0)
            {
              g_p2_psram.cancel_sequence = sequence;
              grace_deadline = now + P2_PSRAM_UNIFIED_GRACE_CYCLES;
              timed_out = true;
            }
          else if (!complete && timed_out &&
                   g_p2_psram.cancel_sequence != sequence)
            {
              g_p2_psram.cancel_sequence = sequence;
            }

          p2_psram_raw_unlock();
        }

      if (complete)
        {
          up_irq_restore(flags);
          return timed_out ? -ETIMEDOUT : status;
        }

      if (!timed_out && (int32_t)(now - deadline) >= 0)
        {
          /* The service lock was unavailable at the deadline.  Advance to a
           * bounded grace period without it; cancellation is published on
           * the next successful try-lock, if the service makes progress.
           */

          grace_deadline = now + P2_PSRAM_UNIFIED_GRACE_CYCLES;
          timed_out = true;
        }

      if (timed_out && (int32_t)(now - grace_deadline) >= 0)
        {
          /* The caller's Hub buffer may be its stack.  Stop the worker
           * before returning so it cannot complete into a dead stack frame.
           */

          p2_psram_stop_failed_cog();
          up_irq_restore(flags);
          return -ETIMEDOUT;
        }
    }
}
#endif

int p2_psram_get_geometry(FAR struct p2_psram_geometry_s *geometry)
{
  struct p2_psram_geometry_s snapshot;
  irqstate_t flags;
  int ret;

  if (geometry == NULL)
    {
      return -EINVAL;
    }

  ret = nxmutex_lock(&g_p2_psram.mutex);
  if (ret < 0)
    {
      return ret;
    }

  if (!g_p2_psram.registered || g_p2_psram.failed)
    {
      ret = -ENODEV;
      goto out_unlock;
    }

  flags = p2_psram_task_lock();
  snapshot.size_bytes = P2_PSRAM_SIZE_BYTES;
  snapshot.chip_count = P2_PSRAM_CHIP_COUNT;
  snapshot.chip_size_bytes = P2_PSRAM_CHIP_SIZE_BYTES;
  snapshot.natural_word_bytes = P2_PSRAM_NATURAL_WORD_BYTES;
  snapshot.max_request_bytes = CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST;
  snapshot.qpi_clock_hz = P2_PSRAM_QPI_CLOCK_HZ;
  snapshot.ce_low_limit_cycles = P2_PSRAM_CE_LOW_LIMIT_CYCLES;
  snapshot.max_ce_low_cycles = g_p2_psram.max_ce_cycles;
  snapshot.service_cog = g_p2_psram.service_cog;
  p2_psram_task_unlock(flags);

  /* In unified mode, geometry may itself be a tagged PSRAM object.  The
   * aggregate assignment below is compiler-lowered into the xmem copy ABI.
   * Release both service locks first so that transfer can acquire the raw
   * hardware lock without recursively deadlocking this caller.
   */

  nxmutex_unlock(&g_p2_psram.mutex);
  *geometry = snapshot;
  return 0;

out_unlock:
  nxmutex_unlock(&g_p2_psram.mutex);
  return ret;
}

ssize_t p2_psram_transfer(enum p2_psram_operation_e operation,
                          uint32_t external_address, FAR void *hub_buffer,
                          size_t length, uint32_t timeout_ticks)
{
#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
  int ret;

  (void)timeout_ticks;
  ret = p2_psram_unified_transfer(operation, external_address, hub_buffer,
                                  (uint32_t)length);
  return ret < 0 ? ret : (ssize_t)length;
#else
  FAR struct p2_psram_request_s *request = &g_p2_psram.request;
  irqstate_t flags;
  uint32_t sequence;
  int ret;

  if (operation != P2_PSRAM_OPERATION_READ &&
      operation != P2_PSRAM_OPERATION_WRITE)
    {
      return -EINVAL;
    }

  if ((hub_buffer == NULL && length != 0) ||
      !p2_psram_range_valid(external_address, length) ||
      length > CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST)
    {
      return -EINVAL;
    }

  if (length == 0)
    {
      return 0;
    }

  if (timeout_ticks == 0)
    {
      timeout_ticks = CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS;
    }

  ret = nxmutex_lock(&g_p2_psram.mutex);
  if (ret < 0)
    {
      return ret;
    }

  if (!g_p2_psram.registered || g_p2_psram.failed)
    {
      nxmutex_unlock(&g_p2_psram.mutex);
      return -ENODEV;
    }

  sequence = p2_psram_next_sequence();
  flags = p2_psram_task_lock();
  request->completion_sequence = 0;
  g_p2_psram.cancel_sequence = 0;
  request->sequence = sequence;
  request->operation = operation;
  request->external_address = external_address;
  request->hub_buffer = (uintptr_t)hub_buffer;
  request->length = length;
  request->status = -EINPROGRESS;
  request->timeout_ticks = timeout_ticks;
  request->completion = P2_PSRAM_COMPLETION_SUBMITTED;
  p2_psram_task_unlock(flags);

  ret = p2_psram_wait(sequence, timeout_ticks);
  nxmutex_unlock(&g_p2_psram.mutex);
  return ret < 0 ? ret : (ssize_t)length;
#endif
}
