FREE='free'; RESERVED={**{p:'psram' for p in range(40,58)}, **{p:'storage' for p in range(58,62)}, 62:'console',63:'console',38:'led',39:'led'}
class PinManager:
    def __init__(self): self.owners={}
    def claim(self,pin,owner):
        if pin<0 or pin>63: raise ValueError('invalid pin')
        if pin in RESERVED and RESERVED[pin]!=owner: raise PermissionError('reserved')
        if pin in self.owners and self.owners[pin]!=owner: raise RuntimeError('busy')
        self.owners[pin]=owner
    def release(self,pin,owner):
        if self.owners.get(pin)!=owner: raise PermissionError('not owner')
        del self.owners[pin]
