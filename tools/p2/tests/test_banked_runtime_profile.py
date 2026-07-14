#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APPS = ROOT.parent / "apps"


class BankedRuntimeProfileTests(unittest.TestCase):
    def read(self, path):
        return path.read_text(encoding="utf-8")

    def test_manager_keeps_operational_tools_and_excludes_heavy_runtime(self):
        config = self.read(BOARD / "configs/berrymgr/defconfig")
        for setting in (
            "# CONFIG_DISABLE_PSEUDOFS_OPERATIONS is not set",
            "CONFIG_LINE_MAX=128",
            "CONFIG_NSH_CLE=y",
            "CONFIG_NSH_NESTDEPTH=8",
            "CONFIG_NSH_CMDOPT_HEXDUMP=y",
            "# CONFIG_NSH_DISABLE_CAT is not set",
            "# CONFIG_NSH_DISABLE_CP is not set",
            "# CONFIG_NSH_DISABLE_ECHO is not set",
            "# CONFIG_NSH_DISABLE_HEXDUMP is not set",
            "# CONFIG_NSH_DISABLE_LS is not set",
            "# CONFIG_NSH_DISABLE_MKDIR is not set",
            "# CONFIG_NSH_DISABLE_MOUNT is not set",
            "# CONFIG_NSH_DISABLE_PWD is not set",
            "# CONFIG_NSH_DISABLE_RM is not set",
            "# CONFIG_NSH_DISABLE_TEST is not set",
            "# CONFIG_NSH_DISABLE_UMOUNT is not set",
            "# CONFIG_NSH_DISABLESCRIPT is not set",
            "# CONFIG_NSH_DISABLE_ITEF is not set",
            "# CONFIG_NSH_DISABLE_REBOOT is not set",
            "CONFIG_SYSTEM_CLE=y",
            "CONFIG_SYSTEM_CLE_CMD_HISTORY=y",
            "CONFIG_SYSTEM_VI=y",
            "CONFIG_SYSTEM_P2RECV=y",
            "CONFIG_SYSTEM_P2BANK=y",
            "CONFIG_P2_EC32MB_PSRAM=y",
            "CONFIG_P2_EC32MB_FLASHBOOT=y",
            "CONFIG_P2_EC32MB_SDCARD_AUTOMOUNT=y",
            "CONFIG_FS_SMARTFS=y",
            "CONFIG_FS_FAT=y",
        ):
            self.assertIn(setting, config)

        self.assertNotIn("CONFIG_INTERPRETERS_BERRY=y", config)
        self.assertNotIn("CONFIG_GRAPHICS_LVGL=y", config)
        self.assertNotIn("CONFIG_ELF=y", config)

    def test_berry_bank_is_self_contained_and_does_not_embed_manager(self):
        config = self.read(BOARD / "configs/berrybank/defconfig")
        for setting in (
            "# CONFIG_DISABLE_PSEUDOFS_OPERATIONS is not set",
            'CONFIG_INIT_ENTRYPOINT="p2berrybank_main"',
            "CONFIG_BOARDCTL_RESET=y",
            "CONFIG_FS_FAT=y",
            "CONFIG_GRAPHICS_LVGL=y",
            "CONFIG_LV_USE_LABEL=y",
            "CONFIG_INTERPRETERS_BERRY=y",
            "CONFIG_INTERPRETERS_BERRY_LVGL=y",
            "CONFIG_SYSTEM_P2BERRYBANK=y",
            "CONFIG_INTERPRETERS_BERRY_LVGL_P2_PRESSURE_MIN=64",
        ):
            self.assertIn(setting, config)

        for excluded in (
            "CONFIG_SYSTEM_NSH=y",
            "CONFIG_SYSTEM_VI=y",
            "CONFIG_SYSTEM_P2BANK=y",
            "CONFIG_P2_EC32MB_PSRAM=y",
            "CONFIG_P2_EC32MB_FLASHBOOT=y",
            "CONFIG_ELF=y",
        ):
            self.assertNotIn(excluded, config)

        binding = self.read(APPS / "interpreters/berry/be_lvgl.c")
        self.assertIn("sample.z2 > sample.z1", binding)
        self.assertIn(
            "static void be_lvgl_colorarg(bvm *vm, int index, "
            "lv_color_t *color)",
            binding,
        )
        self.assertIn("memset(color, 0, sizeof(*color));", binding)
        self.assertNotIn("static lv_color_t be_lvgl_colorarg", binding)

    def test_manager_provisions_bank_directory_and_examples_non_destructively(self):
        script = self.read(BOARD / "src/etc/init.d/rcS")
        makefile = self.read(BOARD / "src/Makefile")
        self.assertIn("/mnt/flash/banks/berry.bin", script)
        self.assertIn("P2BANK:BERRY=READY", script)
        self.assertIn("CONFIG_SYSTEM_P2BANK", script)
        self.assertIn(
            "$(CONFIG_INTERPRETERS_BERRY_LVGL) $(CONFIG_SYSTEM_P2BANK)",
            makefile,
        )
        for name in ("core_smoke.be", "lvgl_bars.be", "lvgl_widgets.be"):
            self.assertIn("if [ ! -e /mnt/sd/berry-p2/{} ]".format(name), script)
            self.assertIn("etc/berry-p2/{}".format(name), makefile)
        self.assertNotIn("mkfatfs", script)
        self.assertNotIn("mksmartfs", script)

    def test_manager_vi_serial_console_reliability(self):
        config = self.read(BOARD / "configs/berrymgr/defconfig")
        termcurses = self.read(
            APPS / "system/termcurses/tcurses_vt100.c"
        )
        vi = self.read(APPS / "system/vi/vi.c")

        self.assertIn("CONFIG_SYSTEM_TERMCURSES_ESCDELAY_MS=30", config)
        self.assertIn("CONFIG_ENABLE_ALL_SIGNALS=y", config)
        self.assertIn("CONFIG_SIG_DEFAULT=y", config)
        self.assertIn("CONFIG_SIG_SIGKILL_ACTION=y", config)
        self.assertIn("CONFIG_TTY_SIGINT=y", config)
        self.assertIn("CONFIG_TTY_SIGINT_CHAR=0x03", config)
        self.assertIn("tcurses_vt100_savekeys", termcurses)
        self.assertIn("ret = read(in_fd", termcurses)
        self.assertIn("sizeof(priv->keybuf) - 1 - len", termcurses)
        self.assertIn("digits >= 5", termcurses)
        self.assertIn("row > UINT16_MAX", termcurses)
        self.assertIn("col > UINT16_MAX", termcurses)
        self.assertIn("ret = select(fd + 1", termcurses)
        self.assertIn("FD_SET(fd, &rfds)", termcurses)
        self.assertIn("if (ch == '\\r')", vi)
        self.assertIn("skip_lf_after_cr", vi)
        self.assertIn(
            "nwritten = write(STDOUT_FILENO, buffer, nremaining)", vi
        )
        self.assertIn("ch != KEY_CMDMODE_SAVEQUIT", vi)
        self.assertIn("if (vi->modified)", vi)
        self.assertIn("vi->cursave = vi->cursor", vi)
        self.assertIn("isprint((unsigned char)ch)", vi)
        self.assertIn("if (fflush(stream) != 0)", vi)
        self.assertIn("if (fclose(stream) != 0 && errcode == 0)", vi)
        self.assertIn('"%zuC written"', vi)
        self.assertIn("if (vi->textsize > 0 && pos == vi->textsize", vi)
        self.assertNotIn("if (filename || vi->modified)", vi)
        self.assertIn("if (pathlen < 0 ||", vi)

    def test_sd_examples_cover_animation_navigation_keypad_and_timing(self):
        examples = BOARD / "src/etc/berry-p2"
        bars = self.read(examples / "lvgl_bars.be")
        for contract in (
            'print("P2BERRY:LVGL_BARS=READY:UPDATES=80")',
            "while step < 80",
            "meter1.set_value(phase, lv.ANIM_ON)",
            "updates_x100 = int(80 * 100000 / elapsed)",
            "UPDATES_PER_SEC_X100=",
        ):
            self.assertIn(contract, bars)

        widgets = self.read(examples / "lvgl_widgets.be")
        for contract in (
            'keypad_label.set_text("[ 1 ]  [ 2 ]  [ 3 ]',
            "keypad_panel.add_event_cb(keypad_clicked, lv.EVENT_CLICKED)",
            "left_button.add_event_cb(previous_page, lv.EVENT_CLICKED)",
            "right_button.add_event_cb(next_page, lv.EVENT_CLICKED)",
            "show_page((page_index[0] + page_count - 1) % page_count)",
            "show_page((page_index[0] + 1) % page_count)",
            'print("P2BERRY:LVGL_WIDGETS=READY:PAGES=2:',
            "while step < 180",
            "updates_x100 = int(180 * 100000 / elapsed)",
            "UPDATES_PER_SEC_X100=",
        ):
            self.assertIn(contract, widgets)

    def test_shared_handoff_and_fixed_psram_stage_are_bounded(self):
        header = self.read(BOARD / "include/p2_ec32mb_bank.h")
        self.assertIn("P2_BANK_HUB_IMAGE_LIMIT       UINT32_C(0x0007c000)", header)
        self.assertIn("P2_BANK_HANDOFF_ADDRESS       UINT32_C(0x0007c000)", header)
        self.assertIn("UINT32_C(33554432) - P2_BANK_HUB_IMAGE_LIMIT", header)
        self.assertIn("p2_bank_handoff_crc32", header)
        self.assertIn("p2_bank_handoff_valid", header)
        self.assertIn("handoff->bank_size > P2_BANK_HUB_IMAGE_LIMIT", header)

    def test_launcher_fails_closed_before_destructive_switch(self):
        source = self.read(APPS / "system/p2bank/p2bank.c")
        for contract in (
            'p2bank_path_safe(bank_path, "/mnt/flash/", 255)',
            'p2bank_path_safe(script_path, "/mnt/sd/",',
            "S_ISREG(status.st_mode)",
            "P2_PSRAM_NATURAL_WORD_BYTES - 1u",
            "p2_psram_bank_reserve()",
            "p2bank_verify_psram",
            "p2bank_write_handoff",
            "p2_psram_boot_bank(P2_BANK_PSRAM_STAGE_ADDRESS, image_size)",
        ):
            self.assertIn(contract, source)
        self.assertLess(source.index("p2bank_verify_psram(image_size"),
                        source.index("p2_psram_boot_bank("))
        self.assertIn("p2_psram_bank_release()", source)
        self.assertNotIn("uint8_t padding", source)

        board = self.read(BOARD / "src/p2_ec32mb_psram.c")
        transfer = board[board.index("ssize_t p2_psram_transfer"):
                         board.index("int p2_psram_boot_bank")]
        boot = board[board.index("int p2_psram_boot_bank"):]
        self.assertIn("g_p2_psram.bank_reserved &&", transfer)
        self.assertIn("g_p2_psram.bank_owner != nxsched_gettid()", transfer)
        self.assertIn("if (!g_p2_psram.bank_reserved)", boot)
        self.assertIn("return -EPERM;", boot)
        self.assertIn("g_p2_psram.bank_owner != nxsched_gettid()", boot)

    def test_cog_loader_stops_peers_before_overwriting_hub(self):
        source = self.read(BOARD / "src/p2_ec32mb_psram_service.S")
        loader = source[source.index("p2_psram_bank_loader_image:") :]
        self.assertIn("cogstop P2_BANK_TARGET", loader)
        self.assertIn("wrlong  P2_BANK_DATA, P2_BANK_HUB", loader)
        self.assertIn("coginit #0, #0", loader)
        self.assertIn("overlaps cog data registers", loader)
        self.assertLess(loader.index("cogstop P2_BANK_TARGET"),
                        loader.index("wrlong  P2_BANK_DATA, P2_BANK_HUB"))
        self.assertLess(loader.index("wrlong  P2_BANK_DATA, P2_BANK_HUB"),
                        loader.index("coginit #0, #0"))

    def test_bank_entry_preserves_repl_and_resets_to_manager(self):
        source = self.read(APPS / "system/p2berrybank/p2berrybank_main.c")
        self.assertIn("P2BERRYBANK:SCRIPT=REPL", source)
        self.assertIn("P2BERRYBANK:REPL=READY:EXIT=quit()", source)
        self.assertIn("berry_main(berry_argc, berry_argv)", source)
        self.assertIn("mallinfo()", source)
        self.assertIn("boardctl(BOARDIOC_RESET, 0)", source)

        patch = self.read(
            APPS
            / "interpreters/berry/0004-Add-P2-bank-REPL-quit-function.patch"
        )
        for contract in (
            "#ifdef CONFIG_SYSTEM_P2BERRYBANK",
            "static int berry_bank_quit(bvm *vm)",
            "be_exit(vm, status);",
            'be_setglobal(vm, "quit");',
            "berry_bank_register_quit(vm);",
        ):
            self.assertIn(contract, patch)

        makefile = self.read(APPS / "interpreters/berry/Makefile")
        cmake = self.read(APPS / "interpreters/berry/CMakeLists.txt")
        patch_name = "0004-Add-P2-bank-REPL-quit-function.patch"
        self.assertIn(patch_name, makefile)
        self.assertIn(patch_name, cmake)

    def test_trimmed_berry_keeps_global_format_for_fstrings(self):
        coc_config = self.read(
            APPS / "interpreters/berry/include/berry_coc_p2.h"
        )
        self.assertIn("#define BE_USE_STRING_MODULE 0", coc_config)

        builtin_patch = self.read(
            APPS
            / "interpreters/berry/0003-Honor-optional-builtin-class-dependencies.patch"
        )
        self.assertIn("format, func(be_str_format)", builtin_patch)
        self.assertNotIn(
            "+    format, func(be_str_format), BE_USE_STRING_MODULE",
            builtin_patch,
        )

        patch_name = "0005-Keep-global-format-without-string-module.patch"
        format_patch = self.read(APPS / "interpreters/berry" / patch_name)
        removed_guard = format_patch.index("-#if BE_USE_STRING_MODULE")
        format_function = format_patch.index("int be_str_format(bvm *vm)")
        restored_guard = format_patch.index("+#if BE_USE_STRING_MODULE")
        self.assertLess(removed_guard, format_function)
        self.assertLess(format_function, restored_guard)

        makefile = self.read(APPS / "interpreters/berry/Makefile")
        cmake = self.read(APPS / "interpreters/berry/CMakeLists.txt")
        self.assertIn(patch_name, makefile)
        self.assertIn(patch_name, cmake)


if __name__ == "__main__":
    unittest.main()
