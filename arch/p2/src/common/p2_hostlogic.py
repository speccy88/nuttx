P2_SYSCLK_HZ = 180_000_000
P2_HUB_RAM_SIZE = 512 * 1024
def baud_ticks(sysclk, baud):
    if baud <= 0: raise ValueError('baud must be positive')
    return (sysclk + baud // 2) // baud
def tick_cycles(sysclk, tick_hz):
    if tick_hz <= 0: raise ValueError('tick_hz must be positive')
    return (sysclk + tick_hz // 2) // tick_hz
def counter_delta(now, then):
    return (now - then) & 0xffffffff
