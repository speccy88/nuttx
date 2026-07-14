import lv

print("P2BERRY:LVGL_WIDGETS:START:PAGES=2:TOUCH=PRESSURE_POLLED")
lv.start()

screen = lv.screen_active()
screen.set_style_bg_color(lv.color(0x101629), lv.PART_MAIN)

widgets_page = lv.obj(screen)
widgets_page.remove_style_all()
widgets_page.set_pos(0, 0)
widgets_page.set_size(240, 270)
widgets_page.remove_flag(lv.OBJ_FLAG_SCROLLABLE)

widgets_title = lv.label(widgets_page)
widgets_title.set_text("Animated widgets")
widgets_title.set_style_text_color(lv.color(0xffffff), lv.PART_MAIN)
widgets_title.align(lv.ALIGN_TOP_MID, 0, 6)

progress = lv.bar(widgets_page)
progress.set_pos(20, 34)
progress.set_size(200, 18)
progress.set_range(0, 100)
progress.set_style_bg_color(lv.color(0x27304f), lv.PART_MAIN)
progress.set_style_bg_color(lv.color(0x2eb67d), lv.PART_INDICATOR)

slider = lv.slider(widgets_page)
slider.set_pos(20, 70)
slider.set_size(200, 24)
slider.set_range(0, 100)
slider.set_value(35, lv.ANIM_OFF)
slider.set_style_bg_color(lv.color(0x27304f), lv.PART_MAIN)
slider.set_style_bg_color(lv.color(0x36c5f0), lv.PART_INDICATOR)
slider.set_style_bg_color(lv.color(0xffffff), lv.PART_KNOB)

toggle = lv.switch(widgets_page)
toggle.set_pos(88, 108)
toggle.set_size(64, 32)
toggle.set_style_bg_color(lv.color(0x27304f), lv.PART_MAIN)
toggle.set_style_bg_color(lv.color(0xe01e5a), lv.PART_INDICATOR | lv.STATE_CHECKED)
toggle.set_style_bg_color(lv.color(0xffffff), lv.PART_KNOB)

graph_bars = []
graph_colors = [0x36c5f0, 0x2eb67d, 0xecb22e,
                0xe01e5a, 0x7c5cff, 0x36c5f0]

i = 0
while i < 6
    graph = lv.obj(widgets_page)
    graph.remove_style_all()
    graph.set_pos(24 + i * 34, 238)
    graph.set_size(22, 10)
    graph.set_style_bg_color(lv.color(graph_colors[i]), lv.PART_MAIN)
    graph.set_style_bg_opa(lv.OPA_COVER, lv.PART_MAIN)
    graph.set_style_radius(3, lv.PART_MAIN)
    graph_bars.push(graph)
    i += 1
end

keypad_page = lv.obj(screen)
keypad_page.remove_style_all()
keypad_page.set_pos(0, 0)
keypad_page.set_size(240, 270)
keypad_page.remove_flag(lv.OBJ_FLAG_SCROLLABLE)
keypad_page.add_flag(lv.OBJ_FLAG_HIDDEN)

keypad_count = [0]
keypad_display = lv.label(keypad_page)
keypad_display.set_text("Enter value")
keypad_display.set_style_text_color(lv.color(0xffffff), lv.PART_MAIN)
keypad_display.align(lv.ALIGN_TOP_MID, 0, 9)

keypad_panel = lv.button(keypad_page)
keypad_panel.set_pos(12, 42)
keypad_panel.set_size(216, 204)
keypad_panel.set_style_bg_color(lv.color(0x27375f), lv.PART_MAIN)
keypad_panel.set_style_radius(8, lv.PART_MAIN)

keypad_label = lv.label(keypad_panel)
keypad_label.set_text("[ 1 ]  [ 2 ]  [ 3 ]\n\n" +
                      "[ 4 ]  [ 5 ]  [ 6 ]\n\n" +
                      "[ 7 ]  [ 8 ]  [ 9 ]\n\n" +
                      "[ C ]  [ 0 ]  [OK ]")
keypad_label.set_style_text_color(lv.color(0xffffff), lv.PART_MAIN)
keypad_label.center()

def keypad_clicked(obj, code)
    keypad_count[0] += 1
    keypad_display.set_text("Keypad taps: " + str(keypad_count[0]))
end

keypad_panel.add_event_cb(keypad_clicked, lv.EVENT_CLICKED)

page_count = 2
page_index = [0]
page_status = lv.label(screen)
page_status.set_text("1 / 2")
page_status.set_style_text_color(lv.color(0xaebbd6), lv.PART_MAIN)
page_status.align(lv.ALIGN_BOTTOM_MID, 0, -12)

left_button = lv.button(screen)
left_button.set_pos(12, 276)
left_button.set_size(48, 36)
left_button.set_style_bg_color(lv.color(0x7c5cff), lv.PART_MAIN)
left_label = lv.label(left_button)
left_label.set_text("<")
left_label.center()

right_button = lv.button(screen)
right_button.set_pos(180, 276)
right_button.set_size(48, 36)
right_button.set_style_bg_color(lv.color(0x7c5cff), lv.PART_MAIN)
right_label = lv.label(right_button)
right_label.set_text(">")
right_label.center()

def show_page(index)
    page_index[0] = index
    if index == 0
        widgets_page.remove_flag(lv.OBJ_FLAG_HIDDEN)
        keypad_page.add_flag(lv.OBJ_FLAG_HIDDEN)
        page_status.set_text("1 / 2")
    else
        widgets_page.add_flag(lv.OBJ_FLAG_HIDDEN)
        keypad_page.remove_flag(lv.OBJ_FLAG_HIDDEN)
        page_status.set_text("2 / 2")
    end
end

def previous_page(obj, code)
    show_page((page_index[0] + page_count - 1) % page_count)
end

def next_page(obj, code)
    show_page((page_index[0] + 1) % page_count)
end

left_button.add_event_cb(previous_page, lv.EVENT_CLICKED)
right_button.add_event_cb(next_page, lv.EVENT_CLICKED)

lv.run(100)
print("P2BERRY:LVGL_WIDGETS=READY:PAGES=2:TOUCH=PRESSURE_POLLED")
started = lv.millis()
step = 0
while step < 180
    phase = (step * 3) % 200
    if phase > 100
        phase = 200 - phase
    end

    progress.set_value(phase, lv.ANIM_ON)
    if step % 120 < 60
        toggle.add_state(lv.STATE_CHECKED)
    else
        toggle.remove_state(lv.STATE_CHECKED)
    end

    i = 0
    while i < graph_bars.size()
        height = 12 + ((phase + i * 19) % 82)
        graph_bars[i].set_pos(24 + i * 34, 252 - height)
        graph_bars[i].set_size(22, height)
        i += 1
    end

    lv.run(20)
    step += 1
end

elapsed = lv.millis() - started
updates_x100 = int(180 * 100000 / elapsed)
last_slider = slider.get_value()
lv.stop()
print("P2BERRY:LVGL_WIDGETS=PASS:PAGES=2:UPDATES=180:ELAPSED_MS=" +
      str(elapsed) + ":UPDATES_PER_SEC_X100=" + str(updates_x100) +
      ":SLIDER=" + str(last_slider))
