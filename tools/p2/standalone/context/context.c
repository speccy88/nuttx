/****************************************************************************
 * tools/p2/standalone/context/context.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * One-million-switch native Propeller 2 CT1 context stress proof.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <stdarg.h>

#include <propeller2.h>

#define __ASSEMBLY__ 1
#include <arch/p2/include/context.h>
#undef __ASSEMBLY__

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_RCFAST_MODE              0x000000f0u
#define P2_CLOCK_SETUP              0x010008f4u
#define P2_CLOCK_FINAL              0x010008f7u
#define P2_CLOCK_LOCK_WAIT_CYCLES  300000u
#define P2_SYSTEM_FREQUENCY_HZ     180000000u

#define P2_UART_RX_PIN             63u
#define P2_UART_TX_PIN             62u
#define P2_UART_BAUD               230400u
#define P2_UART_TX_TIMEOUT_TICKS   (P2_SYSTEM_FREQUENCY_HZ / 100u)

#define P2_SWITCH_TARGET           1000000u
#define P2_PROGRESS_INTERVAL       100000u
#define P2_TIMER_PERIOD_CYCLES     18000u

#define P2_TASK_COUNT              2u
#define P2_TASK_STACK_LONGS        3072u
#define P2_STACK_GUARD_LONGS       16u
#define P2_STACK_GUARD_LOW         0x51acce55u
#define P2_STACK_GUARD_HIGH        0xa55ec7a1u
#define P2_FRAME_TOTAL_LONGS       (P2_XCPT_REGS + 1u)

#define P2_IRQ_AREA_GUARD_LONGS    4u
#define P2_IRQ_AREA_FRAME_LONG     P2_IRQ_AREA_GUARD_LONGS
#define P2_IRQ_AREA_LONGS          (P2_FRAME_TOTAL_LONGS + \
                                    2u * P2_IRQ_AREA_GUARD_LONGS)
#define P2_IRQ_STACK_GUARD_LONGS   16u
#define P2_IRQ_STACK_USABLE_LONGS  512u
#define P2_IRQ_STACK_LONGS         (P2_IRQ_STACK_USABLE_LONGS + \
                                    2u * P2_IRQ_STACK_GUARD_LONGS)
#define P2_IRQ_GUARD_LOW           0x1a51cafeu
#define P2_IRQ_GUARD_HIGH          0xe71d5afeu

#define P2_FAIL_REGPATTERN         (1u << 0)
#define P2_FAIL_CANARY             (1u << 1)
#define P2_FAIL_SPILL              (1u << 2)
#define P2_FAIL_VARARGS            (1u << 3)
#define P2_FAIL_ARITH64            (1u << 4)
#define P2_FAIL_FRAME              (1u << 5)
#define P2_FAIL_IRQ_CANARY         (1u << 6)

#define P2_NOINLINE __attribute__((noinline))

#define P2_WINDOW_PASS             0u
#define P2_WINDOW_FAIL             1u
#define P2_WINDOW_TERMINAL         2u

/****************************************************************************
 * Private Types
 ****************************************************************************/

typedef unsigned int p2_u32;
typedef signed int p2_s32;
typedef unsigned long long p2_u64;
typedef unsigned int p2_uptr;

typedef char p2_u32_is_32[(sizeof(p2_u32) == 4) ? 1 : -1];
typedef char p2_u64_is_64[(sizeof(p2_u64) == 8) ? 1 : -1];
typedef char p2_pointer_is_32[(sizeof(void *) == 4) ? 1 : -1];
typedef char p2_frame_is_38_longs[(P2_FRAME_TOTAL_LONGS == 38u) ? 1 : -1];

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

extern void p2_context_int1(void);
extern void p2_context_start(p2_u32 *frame_top) __attribute__((noreturn));
extern void p2_context_timer_enable(void (*handler)(void));
extern void p2_context_timer_mask(void);
extern void p2_context_timer_quiesce(void);
extern p2_u32 p2_context_register_window(p2_u32 task,
                                         volatile p2_u32 *switches);

/****************************************************************************
 * Public Data
 ****************************************************************************/

volatile p2_u32 g_p2_context_switches;
volatile p2_u32 g_p2_context_irq_area[P2_IRQ_AREA_LONGS]
  __attribute__((aligned(512)));
volatile p2_u32 g_p2_context_irq_stack[P2_IRQ_STACK_LONGS]
  __attribute__((aligned(512)));

/****************************************************************************
 * Private Data
 ****************************************************************************/

static p2_u32 g_task_stacks[P2_TASK_COUNT][P2_TASK_STACK_LONGS]
  __attribute__((aligned(16)));
static volatile p2_u32
  g_detached_frames[P2_TASK_COUNT][P2_FRAME_TOTAL_LONGS]
  __attribute__((aligned(16)));
static volatile p2_u32 g_active_task;
static volatile p2_u32 g_timer_deadline;
static volatile p2_u32 g_progress_ticket;
static volatile p2_u32 g_progress_emitted;
static volatile p2_u32 g_next_progress;
static volatile p2_u32 g_target_reached;
static volatile p2_u32 g_failures;
static volatile p2_u32 g_iterations[P2_TASK_COUNT];
static volatile p2_u32 g_register_windows[P2_TASK_COUNT];
static volatile p2_u32 g_nested_hits[P2_TASK_COUNT];
static volatile p2_u32 g_vararg_hits[P2_TASK_COUNT];
static volatile p2_u32 g_arith64_hits[P2_TASK_COUNT];
static volatile p2_u32 g_checksums[P2_TASK_COUNT];

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static p2_u32 p2_counter(void)
{
  p2_u32 value;

  __asm__ volatile ("getct %0" : "=r" (value));
  return value;
}

static void p2_clock_configure(void)
{
  hubset(P2_RCFAST_MODE);
  _clkmode = P2_CLOCK_FINAL;
  _clkfreq = P2_SYSTEM_FREQUENCY_HZ;
  hubset(P2_CLOCK_SETUP);
  waitx(P2_CLOCK_LOCK_WAIT_CYCLES);
  hubset(P2_CLOCK_FINAL);
}

static void p2_uart_configure(void)
{
  p2_u32 bit_period;

  dirl(P2_UART_RX_PIN);
  dirl(P2_UART_TX_PIN);
  bit_period = (P2_SYSTEM_FREQUENCY_HZ / P2_UART_BAUD) << 16;
  bit_period &= 0xfffffc00u;
  bit_period |= 7u;
  wrpin(P_ASYNC_TX | P_TT_01, P2_UART_TX_PIN);
  wxpin(bit_period, P2_UART_TX_PIN);
  dirh(P2_UART_TX_PIN);
  wrpin(P_ASYNC_RX, P2_UART_RX_PIN);
  wxpin(bit_period, P2_UART_RX_PIN);
  dirh(P2_UART_RX_PIN);
}

static int p2_uart_send(char ch)
{
  p2_u32 deadline = p2_counter() + P2_UART_TX_TIMEOUT_TICKS;
  int done;

  wypin((p2_u32)(unsigned char)ch, P2_UART_TX_PIN);
  waitx(20u);
  do
    {
      testp(P2_UART_TX_PIN, done);
      if (done != 0)
        {
          return 0;
        }
    }
  while ((p2_s32)(p2_counter() - deadline) < 0);

  return -1;
}

static void p2_emit(const char *text)
{
  while (*text != '\0')
    {
      if (p2_uart_send(*text++) < 0)
        {
          for (; ; )
            {
              waitx(P2_SYSTEM_FREQUENCY_HZ);
            }
        }
    }
}

static void p2_emit_u32(p2_u32 value)
{
  char digits[10];
  p2_u32 count = 0;

  do
    {
      digits[count++] = (char)('0' + value % 10u);
      value /= 10u;
    }
  while (value != 0u);

  while (count != 0u)
    {
      if (p2_uart_send(digits[--count]) < 0)
        {
          for (; ; )
            {
              waitx(P2_SYSTEM_FREQUENCY_HZ);
            }
        }
    }
}

static void p2_emit_value(const char *prefix, p2_u32 value)
{
  p2_emit(prefix);
  p2_emit_u32(value);
  p2_emit("\r\n");
}

static void p2_stack_initialize(p2_u32 task)
{
  p2_u32 i;

  for (i = 0; i < P2_STACK_GUARD_LONGS; i++)
    {
      g_task_stacks[task][i] = P2_STACK_GUARD_LOW ^ task;
      g_task_stacks[task][P2_TASK_STACK_LONGS - 1u - i] =
        P2_STACK_GUARD_HIGH ^ task;
    }
}

static int p2_stack_valid(p2_u32 task)
{
  p2_u32 i;

  for (i = 0; i < P2_STACK_GUARD_LONGS; i++)
    {
      if (g_task_stacks[task][i] != (P2_STACK_GUARD_LOW ^ task) ||
          g_task_stacks[task][P2_TASK_STACK_LONGS - 1u - i] !=
            (P2_STACK_GUARD_HIGH ^ task))
        {
          return 0;
        }
    }

  return 1;
}

static void p2_irq_storage_initialize(void)
{
  p2_u32 i;

  for (i = 0; i < P2_IRQ_AREA_GUARD_LONGS; i++)
    {
      g_p2_context_irq_area[i] = P2_IRQ_GUARD_LOW ^ i;
      g_p2_context_irq_area[P2_IRQ_AREA_LONGS - 1u - i] =
        P2_IRQ_GUARD_HIGH ^ i;
    }

  for (i = 0; i < P2_IRQ_STACK_GUARD_LONGS; i++)
    {
      g_p2_context_irq_stack[i] = P2_IRQ_GUARD_LOW ^ (0x100u + i);
      g_p2_context_irq_stack[P2_IRQ_STACK_LONGS - 1u - i] =
        P2_IRQ_GUARD_HIGH ^ (0x100u + i);
    }
}

static int p2_irq_storage_valid(void)
{
  p2_u32 i;

  for (i = 0; i < P2_IRQ_AREA_GUARD_LONGS; i++)
    {
      if (g_p2_context_irq_area[i] != (P2_IRQ_GUARD_LOW ^ i) ||
          g_p2_context_irq_area[P2_IRQ_AREA_LONGS - 1u - i] !=
            (P2_IRQ_GUARD_HIGH ^ i))
        {
          return 0;
        }
    }

  for (i = 0; i < P2_IRQ_STACK_GUARD_LONGS; i++)
    {
      if (g_p2_context_irq_stack[i] !=
            (P2_IRQ_GUARD_LOW ^ (0x100u + i)) ||
          g_p2_context_irq_stack[P2_IRQ_STACK_LONGS - 1u - i] !=
            (P2_IRQ_GUARD_HIGH ^ (0x100u + i)))
        {
          return 0;
        }
    }

  return 1;
}

static p2_u32 *p2_synthetic_frame(p2_u32 task, void (*entry)(void))
{
  p2_u32 *resume = &g_task_stacks[task][P2_STACK_GUARD_LONGS];
  p2_u32 *context = resume + 1;
  p2_u32 i;

  *resume = P2_RESUME_PACK(0, 0, (p2_uptr)entry);
  for (i = 0; i < 32u; i++)
    {
      context[i] = 0x100u + (task << 7) + i;
    }

  context[P2_REG_PA] = 0x0a00u + task;
  context[P2_REG_PB] = 0x0b00u + task;
  context[P2_REG_PTRA] = (p2_uptr)(resume + 1);
  context[P2_REG_PTRB] = 0x0d00u + task;
  context[P2_REG_IRQSTATE] = 0;

  for (i = 0; i < P2_FRAME_TOTAL_LONGS; i++)
    {
      g_detached_frames[task][i] = resume[i];
    }

  return context + P2_XCPT_REGS;
}

static P2_NOINLINE p2_u32 p2_vararg_sum(p2_u32 count, ...)
{
  va_list ap;
  p2_u32 total = 0;
  p2_u32 i;

  va_start(ap, count);
  for (i = 0; i < count; i++)
    {
      total += va_arg(ap, p2_u32);
    }

  va_end(ap);
  return total;
}

static P2_NOINLINE p2_u32 p2_spill_leaf(p2_u32 seed)
{
  volatile p2_u32 spill[24];
  p2_u32 check = 0;
  p2_u32 i;

  for (i = 0; i < 24u; i++)
    {
      spill[i] = seed ^ (0x1021u * (i + 1u));
    }

  for (i = 0; i < 24u; i++)
    {
      if (spill[i] != (seed ^ (0x1021u * (i + 1u))))
        {
          g_failures |= P2_FAIL_SPILL;
        }

      check += spill[i];
    }

  return check;
}

static P2_NOINLINE p2_u32 p2_nested_middle(p2_u32 seed)
{
  return p2_spill_leaf(seed) ^ p2_spill_leaf(seed ^ 0x55aa55aau);
}

static P2_NOINLINE p2_u32 p2_nested_outer(p2_u32 seed)
{
  return p2_nested_middle(seed + 3u) + p2_nested_middle(seed + 7u);
}

static void p2_progress_poll(void)
{
  p2_u32 ticket = g_progress_ticket;

  if (ticket != 0u && ticket > g_progress_emitted)
    {
      g_progress_emitted = ticket;
      p2_emit_value("P2CTX:PROGRESS=", ticket);
    }
}

static void p2_task_body(p2_u32 task)
{
  p2_u32 iteration = 0;

  for (; ; )
    {
      p2_u32 varsum;
      p2_u32 nested;
      p2_u32 window;
      p2_u64 value;
      p2_u64 quotient;
      p2_u64 remainder;

      if ((iteration & 31u) == 0u)
        {
          window = p2_context_register_window(task,
                                              &g_p2_context_switches);
          if (window == P2_WINDOW_FAIL)
            {
              g_failures |= P2_FAIL_REGPATTERN;
            }
          else if (window == P2_WINDOW_PASS)
            {
              g_register_windows[task]++;
            }
          else if (window != P2_WINDOW_TERMINAL)
            {
              g_failures |= P2_FAIL_REGPATTERN;
            }
        }

      nested = p2_nested_outer((task << 24) ^ iteration);
      g_nested_hits[task]++;

      varsum = p2_vararg_sum(6u, 1u + task, 2u, 3u, 4u, 5u,
                             6u + iteration);
      if (varsum != 21u + task + iteration)
        {
          g_failures |= P2_FAIL_VARARGS;
        }
      else
        {
          g_vararg_hits[task]++;
        }

      value = 0x0123456789abcdefull + ((p2_u64)task << 32) + iteration;
      quotient = value / 37ull;
      remainder = value % 37ull;
      if (quotient * 37ull + remainder != value || remainder >= 37ull)
        {
          g_failures |= P2_FAIL_ARITH64;
        }
      else
        {
          g_arith64_hits[task]++;
        }

      g_checksums[task] ^= nested ^ (p2_u32)value ^ (p2_u32)quotient;
      g_iterations[task] = ++iteration;

      if (!p2_stack_valid(task))
        {
          g_failures |= P2_FAIL_CANARY;
        }

      p2_progress_poll();
      if (g_target_reached != 0u)
        {
          break;
        }
    }

  p2_context_timer_quiesce();

  if (g_p2_context_switches != P2_SWITCH_TARGET)
    {
      g_failures |= P2_FAIL_FRAME;
    }

  if (!p2_stack_valid(0u) || !p2_stack_valid(1u))
    {
      g_failures |= P2_FAIL_CANARY;
    }

  if (!p2_irq_storage_valid())
    {
      g_failures |= P2_FAIL_IRQ_CANARY;
    }

  p2_emit_value("P2CTX:SWITCHES=", g_p2_context_switches);
  p2_emit_value("P2CTX:TASK0_ITERATIONS=", g_iterations[0]);
  p2_emit_value("P2CTX:TASK1_ITERATIONS=", g_iterations[1]);
  p2_emit_value("P2CTX:TASK0_REGWINDOWS=", g_register_windows[0]);
  p2_emit_value("P2CTX:TASK1_REGWINDOWS=", g_register_windows[1]);
  p2_emit_value("P2CTX:TASK0_NESTED=", g_nested_hits[0]);
  p2_emit_value("P2CTX:TASK1_NESTED=", g_nested_hits[1]);
  p2_emit_value("P2CTX:TASK0_VARARGS=", g_vararg_hits[0]);
  p2_emit_value("P2CTX:TASK1_VARARGS=", g_vararg_hits[1]);
  p2_emit_value("P2CTX:TASK0_ARITH64=", g_arith64_hits[0]);
  p2_emit_value("P2CTX:TASK1_ARITH64=", g_arith64_hits[1]);

  if (g_register_windows[0] == 0u || g_register_windows[1] == 0u)
    {
      g_failures |= P2_FAIL_REGPATTERN;
    }

  if (g_nested_hits[0] == 0u || g_nested_hits[1] == 0u)
    {
      g_failures |= P2_FAIL_SPILL;
    }

  if (g_vararg_hits[0] == 0u || g_vararg_hits[1] == 0u)
    {
      g_failures |= P2_FAIL_VARARGS;
    }

  if (g_arith64_hits[0] == 0u || g_arith64_hits[1] == 0u)
    {
      g_failures |= P2_FAIL_ARITH64;
    }

  if (g_failures == 0u)
    {
      p2_emit("P2CTX:REGS=OK\r\n");
      p2_emit("P2CTX:STACKS=OK\r\n");
      p2_emit("P2CTX:REGPATTERN=OK\r\n");
      p2_emit("P2CTX:CANARY=OK\r\n");
      p2_emit("P2CTX:NESTED_SPILLS=OK\r\n");
      p2_emit("P2CTX:VARARGS=OK\r\n");
      p2_emit("P2CTX:ARITH64=OK\r\n");
      p2_emit("P2CTX:IRQ_CANARIES=OK\r\n");
      p2_emit("P2CTX:PASS\r\n");
      p2_emit("P2CTX:PASS SWITCHES=1000000\r\n");
    }
  else
    {
      p2_emit_value("P2CTX:FAIL MASK=", g_failures);
    }

  for (; ; )
    {
      waitx(P2_SYSTEM_FREQUENCY_HZ);
    }
}

static void p2_task0(void)
{
  p2_task_body(0u);
}

static void p2_task1(void)
{
  p2_task_body(1u);
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/* Called from the assembly veneer on the dedicated interrupt stack.  Copy
 * fixed scratch into the interrupted task's detached frame, select the next
 * task, then copy that detached frame back to fixed restore scratch.
 */

__attribute__((used))
void p2_context_dispatch(void)
{
  p2_u32 active = g_active_task;
  p2_u32 switches = g_p2_context_switches + 1u;
  p2_u32 deadline = g_timer_deadline;
  volatile p2_u32 *detached;
  volatile p2_u32 *scratch;
  p2_u32 i;

  if (!p2_irq_storage_valid())
    {
      g_failures |= P2_FAIL_IRQ_CANARY;
    }

  detached = active == 0u ? &g_detached_frames[0][0] :
                            &g_detached_frames[1][0];
  scratch = &g_p2_context_irq_area[P2_IRQ_AREA_FRAME_LONG];
  for (i = 0; i < P2_FRAME_TOTAL_LONGS; i++)
    {
      *detached++ = *scratch++;
    }

  g_p2_context_switches = switches;

  if (switches == P2_SWITCH_TARGET)
    {
      p2_context_timer_mask();
      g_target_reached = 1u;
    }
  else
    {
      addct1(deadline, P2_TIMER_PERIOD_CYCLES);
      g_timer_deadline = deadline + P2_TIMER_PERIOD_CYCLES;
    }

  if (switches == g_next_progress)
    {
      g_progress_ticket = switches;
      g_next_progress = switches + P2_PROGRESS_INTERVAL;
    }

  active ^= 1u;
  g_active_task = active;

  detached = active == 0u ? &g_detached_frames[0][0] :
                            &g_detached_frames[1][0];
  scratch = &g_p2_context_irq_area[P2_IRQ_AREA_FRAME_LONG];
  for (i = 0; i < P2_FRAME_TOTAL_LONGS; i++)
    {
      *scratch++ = *detached++;
    }
}

__attribute__((section(".text.main"), used))
int main(void)
{
  p2_u32 deadline;
  p2_u32 *task0_frame;

  p2_clock_configure();
  p2_uart_configure();
  p2_context_timer_quiesce();
  p2_emit("P2CTX:ENTRY\r\n");
  p2_emit("P2CTX:FRAME=37+1\r\n");
  p2_emit("P2CTX:TIMER=CT1 ABSOLUTE\r\n");

  p2_stack_initialize(0u);
  p2_stack_initialize(1u);
  p2_irq_storage_initialize();
  task0_frame = p2_synthetic_frame(0u, p2_task0);
  p2_synthetic_frame(1u, p2_task1);
  g_active_task = 0u;
  g_next_progress = P2_PROGRESS_INTERVAL;

  p2_emit("P2CTX:START\r\n");
  p2_emit("P2CTX:TARGET=1000000\r\n");

  /* The global interrupt state is still STALLI here.  Arm CT1 only after
   * all UART output and frame construction, then let p2_context_start
   * restore the synthetic task's ALLOWI state on its selected stack.  The
   * first deadline is therefore safely in the future when RETA enters task
   * 0.
   */

  deadline = p2_counter();
  addct1(deadline, P2_TIMER_PERIOD_CYCLES);
  g_timer_deadline = deadline + P2_TIMER_PERIOD_CYCLES;
  p2_context_timer_enable(p2_context_int1);
  p2_context_start(task0_frame);
}
