#include <nuttx/config.h>
#include <nuttx/mm/mm.h>
extern char _ebss[];
extern char _eheap[];
void up_allocate_heap(void **heap_start, size_t *heap_size){*heap_start=_ebss; *heap_size=(size_t)(_eheap-_ebss);}
