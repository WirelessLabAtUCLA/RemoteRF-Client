import unittest
from unittest import mock

from remoteRF.common.utils.banner import (
    ASCII_BANNER,
    ASCII_COMPAT_BANNER,
    BRANDED_ASCII_BANNER,
    ASCII_BANNER_MIN_COLUMNS,
    ASCII_BANNER_WIDTH,
    COMPACT_BANNER,
    PANEL_WIDTH,
    SIDE_BY_SIDE_MIN_COLUMNS,
    banner_text,
    client_banner_text,
    internal_banner_text,
    supports_unicode,
)
from remoteRF.common.utils import Sty


class BannerTests(unittest.TestCase):
    def test_wide_terminal_uses_ascii_art(self):
        self.assertEqual(banner_text(ASCII_BANNER_MIN_COLUMNS), BRANDED_ASCII_BANNER)

    def test_narrow_terminal_uses_compact_banner(self):
        self.assertEqual(banner_text(ASCII_BANNER_WIDTH), COMPACT_BANNER)

    def test_reported_width_matches_widest_line(self):
        self.assertEqual(
            69,
            max(len(line) for line in ASCII_BANNER.splitlines()),
        )
        self.assertTrue(
            all(len(line) == 69 for line in ASCII_BANNER.splitlines())
        )

    def test_wide_client_header_places_panel_beside_logo(self):
        text = client_banner_text(
            "2.0.9 (latest)",
            SIDE_BY_SIDE_MIN_COLUMNS,
            server="192.0.2.10:61005",
        )
        lines = text.splitlines()

        self.assertEqual(len(lines), len(BRANDED_ASCII_BANNER.splitlines()))
        panel_top = next(
            index for index, line in enumerate(lines) if "╭─ REMOTERF CLIENT" in line
        )
        self.assertIn("◆ www.remoterf.net", lines[panel_top + 1])
        self.assertIn("Created by the Wireless Lab at UCLA", lines[panel_top + 2])
        self.assertIn("VERSION  2.0.9 (latest)", lines[panel_top + 3])
        self.assertIn("SERVER   192.0.2.10:61005", lines[panel_top + 4])
        self.assertNotIn("STATUS", text)
        self.assertIn("UCLA", text)
        self.assertNotIn("TIME", text)
        self.assertTrue(all(len(line) < SIDE_BY_SIDE_MIN_COLUMNS for line in lines))

    def test_medium_client_header_shows_panel_without_logo(self):
        text = client_banner_text("2.0.9", ASCII_BANNER_MIN_COLUMNS)

        self.assertTrue(text.startswith("╭─ REMOTERF CLIENT"))
        self.assertIn("SERVER   NA", text)
        self.assertNotIn("█", text)

    def test_narrow_client_header_uses_compact_wordmark(self):
        text = client_banner_text("2.0.9", PANEL_WIDTH)

        self.assertTrue(text.startswith("◆ REMOTERF CLIENT\n"))
        self.assertNotIn("█", text)

    def test_legacy_encoding_uses_ascii_only_fallback(self):
        self.assertFalse(supports_unicode("ascii"))
        text = client_banner_text(
            "2.0.9",
            140,
            server="rrf.example:61005",
            unicode_supported=False,
        )

        text.encode("ascii")
        self.assertIn(ASCII_COMPAT_BANNER.splitlines()[0], text)
        self.assertNotIn("█", text)
        self.assertIn("* www.remoterf.net", text)
        self.assertIn("Created by the Wireless Lab at UCLA", text)
        self.assertIn("SERVER   rrf.example:61005", text)
        self.assertNotIn("TIME", text)

    def test_compact_layout_never_exceeds_terminal_width(self):
        for width in (8, 12, 20, 40, ASCII_BANNER_WIDTH):
            text = client_banner_text("2.0.9 (latest)", width)
            self.assertTrue(all(len(line) <= width for line in text.splitlines()))

        self.assertIn("\nv2.0.9", client_banner_text("2.0.9 (latest)", 12))

    def test_internal_header_uses_session_box(self):
        text = internal_banner_text(
            "2.0.9 (latest)",
            server="192.0.2.10:61005",
            tos_url="https://remoterf.net/tos",
        )

        self.assertTrue(text.startswith("╭─ REMOTERF SESSION"))
        self.assertIn("◆ www.remoterf.net", text)
        self.assertIn("VERSION  2.0.9 (latest)", text)
        self.assertIn("SERVER   192.0.2.10:61005", text)
        self.assertNotIn("ACCOUNT", text)
        self.assertIn("TOS      remoterf.net/tos", text)
        self.assertNotIn("UCLA", text)
        self.assertNotIn("Pacific Time", text)

    @mock.patch("remoteRF.common.utils.banner.supports_unicode", return_value=True)
    @mock.patch("remoteRF.common.utils.banner.printf")
    def test_full_logo_uses_pink_remote_and_blue_rf(self, printf, _supports_unicode):
        from remoteRF.common.utils.banner import print_client_banner

        print_client_banner("2.0.9", columns=140, server="example.test:61005")

        first_line = printf.call_args_list[0].args
        self.assertIn(Sty.BRIGHT_MAGENTA, first_line[1])
        self.assertIn(Sty.BRIGHT_BLUE, first_line[3])
        self.assertEqual(len(first_line[0]), 53)


if __name__ == "__main__":
    unittest.main()
