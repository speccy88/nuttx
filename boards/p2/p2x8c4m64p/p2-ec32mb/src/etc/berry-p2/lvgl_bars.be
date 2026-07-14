import lv

print("P2BERRY:LVGL_BARS:START")
lv.start()

screen = lv.screen_active()
screen.set_style_bg_color(lv.color(0x0b1625), lv.PART_MAIN)

bars = []
colors = [0x36c5f0, 0x2eb67d, 0xecb22e, 0xe01e5a,
          0x7c5cff, 0x36c5f0, 0x2eb67d, 0xecb22e]

i = 0
while i < 8
    item = lv.obj(screen)
    item.remove_style_all()
    item.set_pos(12 + i * 28, 138)
    item.set_size(18, 12)
    item.set_style_bg_color(lv.color(colors[i]), lv.PART_MAIN)
    item.set_style_bg_opa(lv.OPA_COVER, lv.PART_MAIN)
    item.set_style_radius(3, lv.PART_MAIN)
    bars.push(item)
    i += 1
end

meter1 = lv.bar(screen)
meter1.set_pos(20, 178)
meter1.set_size(200, 22)
meter1.set_range(0, 100)
meter1.set_style_bg_color(lv.color(0x20344d), lv.PART_MAIN)
meter1.set_style_bg_color(lv.color(0x36c5f0), lv.PART_INDICATOR)

meter2 = lv.bar(screen)
meter2.set_pos(20, 218)
meter2.set_size(200, 22)
meter2.set_range(0, 100)
meter2.set_style_bg_color(lv.color(0x20344d), lv.PART_MAIN)
meter2.set_style_bg_color(lv.color(0xe01e5a), lv.PART_INDICATOR)

pulse = lv.obj(screen)
pulse.remove_style_all()
pulse.set_pos(20, 272)
pulse.set_size(28, 28)
pulse.set_style_bg_color(lv.color(0xffffff), lv.PART_MAIN)
pulse.set_style_bg_opa(lv.OPA_COVER, lv.PART_MAIN)
pulse.set_style_radius(14, lv.PART_MAIN)

lv.run(100)
print("P2BERRY:LVGL_BARS=READY:UPDATES=80")
started = lv.millis()
step = 0
while step < 80
    phase = (step * 4) % 200
    if phase > 100
        phase = 200 - phase
    end

    i = 0
    while i < bars.size()
        value = 12 + ((phase + i * 17) % 88)
        bars[i].set_pos(12 + i * 28, 150 - value)
        bars[i].set_size(18, value)
        i += 1
    end

    meter1.set_value(phase, lv.ANIM_ON)
    meter2.set_value(100 - phase, lv.ANIM_ON)
    pulse.set_pos(20 + phase * 17 / 10, 272)
    lv.run(20)
    step += 1
end

elapsed = lv.millis() - started
updates_x100 = int(80 * 100000 / elapsed)
lv.run(250)
lv.stop()
print("P2BERRY:LVGL_BARS=PASS:UPDATES=80:ELAPSED_MS=" + str(elapsed) +
      ":UPDATES_PER_SEC_X100=" + str(updates_x100))
