#include <nuttx/config.h>
#include <nuttx/kmalloc.h>
#include <nuttx/sched.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

uintptr_t p2_getsp(void)
{
  /* HIL-REQUIRED: inline PASM2 for PTRA read must be derived from p2llvm. */

  return 0;
}

size_t up_check_tcbstack(struct tcb_s *tcb)
{
  /* DRAFTED conservative answer: do not claim coloration high-water logic. */

  return tcb->adj_stack_size;
}

void up_stack_color(void *stackbase, size_t nbytes)
{
  memset(stackbase, 0xaa, nbytes);
}

int up_use_stack(struct tcb_s *tcb, void *stack, size_t stack_size)
{
  /* Upward stack ABI: the initial PTRA is the allocation base after any TLS
   * reservation.  Detailed TLS/top preservation remains documented as risk.
   */

  tcb->stack_alloc_ptr = stack;
  tcb->stack_base_ptr  = stack;
  tcb->adj_stack_size  = stack_size;
  return 0;
}

void up_release_stack(struct tcb_s *dtcb, uint8_t ttype)
{
  (void)ttype;
  if (dtcb->stack_alloc_ptr != NULL)
    {
      kmm_free(dtcb->stack_alloc_ptr);
    }
}

int up_create_stack(struct tcb_s *tcb, size_t stack_size, uint8_t ttype)
{
  void *stack;

  (void)ttype;
  stack = kmm_malloc(stack_size);
  if (stack == NULL)
    {
      return -ENOMEM;
    }

  return up_use_stack(tcb, stack, stack_size);
}
