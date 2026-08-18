# Copyright (C) 2026 RemoteRF
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import os
import shutil
import sys

from .ansi_codes import Sty, printf


ASCII_BANNER = "\n".join(
    line.ljust(69)
    for line in (
        "██████╗ ███████╗███╗   ███╗ ██████╗ ████████╗███████╗██████╗ ███████╗",
        "██╔══██╗██╔════╝████╗ ████║██╔═══██╗╚══██╔══╝██╔════╝██╔══██╗██╔════╝",
        "██████╔╝█████╗  ██╔████╔██║██║   ██║   ██║   █████╗  ██████╔╝█████╗",
        "██╔══██╗██╔══╝  ██║╚██╔╝██║██║   ██║   ██║   ██╔══╝  ██╔══██╗██╔══╝",
        "██║  ██║███████╗██║ ╚═╝ ██║╚██████╔╝   ██║   ███████╗██║  ██║██║",
        "╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝ ╚═════╝    ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝",
    )
)

ASCII_COMPAT_BANNER = r""" ____                      _       ____  _____
|  _ \ ___ _ __ ___   ___ | |_ ___|  _ \|  ___|
| |_) / _ \ '_ ` _ \ / _ \| __/ _ \ |_) | |_
|  _ <  __/ | | | | | (_) | ||  __/  _ <|  _|
|_| \_\___|_| |_| |_|\___/ \__\___|_| \_\_|"""

BRANDED_ASCII_BANNER = ASCII_BANNER
BRANDED_ASCII_COMPAT_BANNER = ASCII_COMPAT_BANNER

COMPACT_BANNER = "RemoteRF"
ASCII_WORDMARK_WIDTH = max(len(line) for line in ASCII_BANNER.splitlines())
ASCII_BANNER_WIDTH = max(len(line) for line in BRANDED_ASCII_BANNER.splitlines())
ASCII_BANNER_MIN_COLUMNS = ASCII_BANNER_WIDTH + 1
PANEL_CONTENT_WIDTH = 31
PANEL_INNER_WIDTH = PANEL_CONTENT_WIDTH + 2
PANEL_WIDTH = PANEL_INNER_WIDTH + 2
PANEL_GAP = 1
SIDE_BY_SIDE_MIN_COLUMNS = ASCII_BANNER_WIDTH + PANEL_GAP + PANEL_WIDTH + 1
ASCII_RF_COLUMN = 53
ASCII_COMPAT_RF_COLUMN = 34


def supports_unicode(encoding: str | None = None) -> bool:
    """Return whether the output encoding can represent the rich banner."""
    if os.environ.get("REMOTERF_ASCII", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        BRANDED_ASCII_BANNER.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _logo(unicode_supported: bool) -> str:
    return BRANDED_ASCII_BANNER if unicode_supported else BRANDED_ASCII_COMPAT_BANNER


def _logo_width(unicode_supported: bool) -> int:
    return max(len(line) for line in _logo(unicode_supported).splitlines())


def _side_by_side_min_columns(unicode_supported: bool) -> int:
    return _logo_width(unicode_supported) + PANEL_GAP + PANEL_WIDTH + 1


def _side_by_side_logo(columns: int, unicode_supported: bool) -> str | None:
    if columns >= _side_by_side_min_columns(unicode_supported):
        return _logo(unicode_supported)
    return None


def _fit_panel_text(text: str) -> str:
    text = str(text).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > PANEL_CONTENT_WIDTH:
        text = f"{text[:PANEL_CONTENT_WIDTH - 1]}…"
    return text


def _panel_lines(
    version: str,
    server: str,
    *,
    unicode_supported: bool = True,
) -> list[str]:
    horizontal = "─" if unicode_supported else "-"
    top_left, top_right = ("╭", "╮") if unicode_supported else ("+", "+")
    bottom_left, bottom_right = ("╰", "╯") if unicode_supported else ("+", "+")
    vertical = "│" if unicode_supported else "|"
    marker = "◆" if unicode_supported else "*"
    label = f"{horizontal} REMOTERF CLIENT "
    top = f"{top_left}{label}{horizontal * (PANEL_INNER_WIDTH - len(label))}{top_right}"
    content = (
        f"{marker} www.remoterf.net",
        "Created by E. Ge and I. Roberts",
        f"VERSION  {version}",
        f"SERVER   {server or 'NA'}",
    )
    body = [
        f"{vertical} {_fit_panel_text(line):<{PANEL_CONTENT_WIDTH}} {vertical}"
        for line in content
    ]
    bottom = f"{bottom_left}{horizontal * PANEL_INNER_WIDTH}{bottom_right}"
    return [top, *body, bottom]


def _internal_panel_lines(
    version: str,
    server: str,
    tos_url: str,
    *,
    unicode_supported: bool = True,
) -> list[str]:
    horizontal = "─" if unicode_supported else "-"
    top_left, top_right = ("╭", "╮") if unicode_supported else ("+", "+")
    bottom_left, bottom_right = ("╰", "╯") if unicode_supported else ("+", "+")
    vertical = "│" if unicode_supported else "|"
    marker = "◆" if unicode_supported else "*"
    label = f"{horizontal} REMOTERF SESSION "
    top = f"{top_left}{label}{horizontal * (PANEL_INNER_WIDTH - len(label))}{top_right}"
    display_tos = str(tos_url).removeprefix("https://").removeprefix("http://")
    content = (
        f"{marker} www.remoterf.net",
        f"VERSION  {version}",
        f"SERVER   {server or 'NA'}",
        f"TOS      {display_tos}",
    )
    body = [
        f"{vertical} {_fit_panel_text(line):<{PANEL_CONTENT_WIDTH}} {vertical}"
        for line in content
    ]
    bottom = f"{bottom_left}{horizontal * PANEL_INNER_WIDTH}{bottom_right}"
    return [top, *body, bottom]


def _compact_lines(
    version: str,
    server: str,
    columns: int,
    *,
    unicode_supported: bool,
) -> list[str]:
    width = max(1, int(columns))
    marker = "◆" if unicode_supported else "*"
    separator = "·" if unicode_supported else "-"
    heading = f"{marker} REMOTERF CLIENT"
    if len(heading) > width:
        heading = COMPACT_BANNER[:width]

    short_version = str(version).split(maxsplit=1)[0]
    server = str(server or "NA")
    detail_options = (
        f"v{version} {separator} {server}",
        f"v{short_version} {separator} {server}",
        f"v{short_version}",
    )
    details = next((line for line in detail_options if len(line) <= width), "")
    if not details:
        details = detail_options[-1][:width]
    return [heading] if not details else [heading, details]


def _styled_panel_args(line: str, index: int, total: int) -> tuple:
    """Return printf arguments with a default-color frame and gray contents."""
    if not line:
        return ("", Sty.DEFAULT)
    if index in {0, total - 1}:
        return (line, Sty.DEFAULT)
    return (
        line[0],
        Sty.DEFAULT,
        line[1:-1],
        Sty.GRAY,
        line[-1],
        Sty.DEFAULT,
    )


def _styled_logo_args(
    line: str,
    index: int,
    *,
    unicode_supported: bool,
) -> tuple:
    """Return printf arguments for the pink REMOTE / blue RF wordmark."""
    wordmark_width = (
        ASCII_WORDMARK_WIDTH
        if unicode_supported
        else max(len(part) for part in ASCII_COMPAT_BANNER.splitlines())
    )
    rf_column = ASCII_RF_COLUMN if unicode_supported else ASCII_COMPAT_RF_COLUMN
    return (
        line[:rf_column],
        (Sty.BOLD, Sty.BRIGHT_MAGENTA),
        line[rf_column:wordmark_width],
        (Sty.BOLD, Sty.BRIGHT_BLUE),
    )


def banner_text(
    columns: int | None = None,
    *,
    unicode_supported: bool | None = None,
) -> str:
    """Return branding that fits without wrapping in the current terminal."""
    if columns is None:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    if unicode_supported is None:
        unicode_supported = supports_unicode()
    logo = _logo(unicode_supported)
    return logo if columns > _logo_width(unicode_supported) else COMPACT_BANNER


def print_banner(columns: int | None = None) -> None:
    printf(banner_text(columns), (Sty.BOLD, Sty.BRIGHT_BLUE))


def client_banner_text(
    version: str,
    columns: int,
    *,
    server: str = "",
    unicode_supported: bool = True,
) -> str:
    """Build a plain-text preview of the responsive first-login header."""
    logo_text = _side_by_side_logo(columns, unicode_supported)
    panel = _panel_lines(version, server, unicode_supported=unicode_supported)
    if logo_text is not None:
        logo_width = max(len(line) for line in logo_text.splitlines())
        logo = logo_text.splitlines()
        height = max(len(logo), len(panel))
        logo.extend([""] * (height - len(logo)))
        panel_top = (height - len(panel)) // 2
        panel = ([""] * panel_top) + panel + ([""] * (height - panel_top - len(panel)))
        return "\n".join(
            f"{logo_line:<{logo_width}}{' ' * PANEL_GAP}{panel_line}".rstrip()
            for logo_line, panel_line in zip(logo, panel)
        )
    if columns > PANEL_WIDTH:
        return "\n".join(panel)

    return "\n".join(
        _compact_lines(
            version,
            server,
            columns,
            unicode_supported=unicode_supported,
        )
    )


def print_client_banner(
    version: str,
    columns: int | None = None,
    *,
    server: str = "",
) -> None:
    """Print the responsive, styled header shown once before client login."""
    if columns is None:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns

    unicode_supported = supports_unicode()
    logo_text = _side_by_side_logo(columns, unicode_supported)
    panel = _panel_lines(version, server, unicode_supported=unicode_supported)
    if logo_text is not None:
        logo_width = max(len(line) for line in logo_text.splitlines())
        logo = logo_text.splitlines()
        panel_height = len(panel)
        height = max(len(logo), len(panel))
        logo.extend([""] * (height - len(logo)))
        panel_top = (height - len(panel)) // 2
        panel = ([""] * panel_top) + panel + ([""] * (height - panel_top - len(panel)))
        for index, (logo_line, panel_line) in enumerate(zip(logo, panel)):
            panel_index = index - panel_top
            printf(
                *_styled_logo_args(
                    f"{logo_line:<{logo_width}}",
                    index,
                    unicode_supported=unicode_supported,
                ),
                " " * PANEL_GAP,
                Sty.DEFAULT,
                *_styled_panel_args(panel_line, panel_index, panel_height),
            )
        return

    if columns > PANEL_WIDTH:
        for index, panel_line in enumerate(panel):
            printf(*_styled_panel_args(panel_line, index, len(panel)))
        return

    compact = _compact_lines(
        version,
        server,
        columns,
        unicode_supported=unicode_supported,
    )
    printf(compact[0], (Sty.BOLD, Sty.DEFAULT))
    if len(compact) > 1:
        printf(compact[1], Sty.GRAY)


def internal_banner_text(
    version: str,
    *,
    server: str = "",
    tos_url: str = "https://remoterf.net/tos",
    unicode_supported: bool = True,
) -> str:
    """Build the boxed header displayed after a successful login."""
    return "\n".join(
        _internal_panel_lines(
            version,
            server,
            tos_url,
            unicode_supported=unicode_supported,
        )
    )


def print_internal_banner(
    version: str,
    *,
    server: str = "",
    tos_url: str = "https://remoterf.net/tos",
) -> None:
    """Print the styled boxed header displayed after a successful login."""
    unicode_supported = supports_unicode()
    lines = _internal_panel_lines(
        version,
        server,
        tos_url,
        unicode_supported=unicode_supported,
    )
    for index, line in enumerate(lines):
        printf(*_styled_panel_args(line, index, len(lines)))
