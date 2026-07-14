include $(TOPDIR)/Make.defs
CSRCS = p2_ec32mb_boot.c p2_ec32mb_pins.c

ifeq ($(CONFIG_USERLED),y)
CSRCS += p2_ec32mb_userleds.c
endif

ifeq ($(CONFIG_P2_EC32MB_GPIO),y)
CSRCS += p2_ec32mb_gpio.c
endif

ifeq ($(CONFIG_P2_EC32MB_UART1),y)
CSRCS += p2_ec32mb_uart.c
endif

ifeq ($(CONFIG_P2_EC32MB_PWM),y)
CSRCS += p2_ec32mb_pwm.c
endif

ifeq ($(CONFIG_P2_EC32MB_CAPTURE),y)
CSRCS += p2_ec32mb_capture.c
endif

ifeq ($(CONFIG_P2_EC32MB_ADC),y)
CSRCS += p2_ec32mb_adc.c
endif

ifeq ($(CONFIG_P2_EC32MB_DAC),y)
CSRCS += p2_ec32mb_dac.c
endif

ifeq ($(CONFIG_P2_EC32MB_SPI),y)
CSRCS += p2_ec32mb_spi.c
endif

ifeq ($(CONFIG_P2_EC32MB_I2C),y)
CSRCS += p2_ec32mb_i2c.c
endif

ifeq ($(CONFIG_P2_EC32MB_PSRAM),y)
CSRCS += p2_ec32mb_psram.c
ASRCS += p2_ec32mb_psram_service.S
endif

ifeq ($(CONFIG_P2_STORAGE),y)
CSRCS += p2_ec32mb_storage.c p2_ec32mb_storage_arbiter.c
endif

ifeq ($(CONFIG_ETC_ROMFS),y)
RCSRCS = etc/init.d/rc.sysinit etc/init.d/rcS

ifeq ($(CONFIG_INTERPRETERS_BERRY),y)
RCRAWS += etc/berry-p2/core_smoke.be
endif
endif

include $(TOPDIR)/boards/Board.mk
