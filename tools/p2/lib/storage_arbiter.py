IDLE='IDLE'; FLASH='FLASH_SELECTED'; SD='SD_SELECTED'; RECOVERY='RECOVERY'
class Arbiter:
    def __init__(self): self.state=IDLE; self.log=[]
    def select_flash(self):
        if self.state==SD: self.state=RECOVERY; raise RuntimeError('sd busy')
        self.state=FLASH; self.log.append(('p60','clk','p61','cs')); return self.state
    def select_sd(self):
        if self.state==FLASH: self.state=RECOVERY; raise RuntimeError('flash busy')
        self.state=SD; self.log.append(('p60','cs','p61','clk')); return self.state
    def release(self): self.state=IDLE; return self.state
    def recover(self): self.state=IDLE; self.log.append(('recover',)); return self.state
