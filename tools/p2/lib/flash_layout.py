FLASH_SIZE=0x01000000
ERASE=0x1000
HUB_RAM=512*1024
PARTITIONS=[('boot',0,0x100000,True),('smartfs',0x100000,0x0f00000,False)]
def validate(parts=PARTITIONS, flash_size=FLASH_SIZE, erase=ERASE, image_size=0):
    end=0
    for name,off,size,prot in sorted(parts, key=lambda p:p[1]):
        if off%erase or size%erase: raise ValueError(f'{name}: erase alignment')
        if off<end: raise ValueError(f'{name}: overlap')
        if off+size>flash_size: raise ValueError(f'{name}: capacity')
        end=off+size
    boot=[p for p in parts if p[0]=='boot'][0]
    if image_size>boot[2]: raise ValueError('image overflow')
    if image_size>HUB_RAM: raise ValueError('hub image overflow')
    return True
