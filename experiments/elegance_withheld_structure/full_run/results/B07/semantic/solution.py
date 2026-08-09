"""Compose customer correspondence for email, print, and SMS."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import textwrap
from typing import Any


def _actions(packet: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    action = packet.get("action")
    if action is None:
        return []
    if isinstance(action, Mapping):
        return [action]
    return list(action)


def _masked(value: Any) -> str:
    text = str(value)
    if len(text) < 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _footnotes(
    packet: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    numbers: dict[str, int] = {}
    urls: list[str] = []
    action_lines: list[str] = []

    for action in _actions(packet):
        url = str(action["url"])
        if url not in numbers:
            numbers[url] = len(urls) + 1
            urls.append(url)
        action_lines.append(f'{action["label"]} [{numbers[url]}]')

    if not urls:
        return action_lines, []

    link_lines = ["Links:"]
    link_lines.extend(f"[{number}] {url}" for number, url in enumerate(urls, 1))
    return action_lines, link_lines


def _text_blocks(
    packet: Mapping[str, Any], *, print_channel: bool = False
) -> list[tuple[str, bool]]:
    """Return (text, atomic) blocks for text and print rendering."""
    blocks: list[tuple[str, bool]] = []

    def add(value: Any, atomic: bool = False) -> None:
        text = "" if value is None else str(value)
        if text:
            blocks.append((text, atomic))

    add(packet.get("greeting"))
    add(packet.get("free_text"))
    add("\n".join(str(line) for line in packet.get("address", [])), True)

    fact_lines: list[str] = []
    for fact in packet.get("facts", []):
        if fact["sensitivity"] == "sensitive":
            if print_channel:
                continue
            value = _masked(fact["value"])
        else:
            value = str(fact["value"])
        fact_lines.append(f'{fact["label"]}: {value}')
    add("\n".join(fact_lines))

    table = packet.get("table")
    if table:
        add(
            "\n".join(
                " | ".join(str(cell) for cell in row)
                for row in table
            )
        )

    action_lines, link_lines = _footnotes(packet)
    for line in action_lines:
        add(line)

    add(packet.get("legal"), True)
    add(packet.get("signoff"))
    add("\n".join(link_lines))
    return blocks


def render_html(packet: Mapping[str, Any]) -> str:
    """Render escaped email HTML."""

    def escaped(value: Any) -> str:
        return escape(str(value), quote=True)

    parts = [
        f'<p class="greeting">{escaped(packet.get("greeting", ""))}</p>',
        f'<p class="body">{escaped(packet.get("free_text", ""))}</p>',
        "<address>"
        + "<br>".join(escaped(line) for line in packet.get("address", []))
        + "</address>",
    ]

    facts = "".join(
        f'<dt>{escaped(fact["label"])}</dt>'
        f'<dd>{escaped(fact["value"])}</dd>'
        for fact in packet.get("facts", [])
    )
    parts.append(f"<dl>{facts}</dl>")

    table = packet.get("table")
    if table:
        rows = "".join(
            "<tr>"
            + "".join(f"<td>{escaped(cell)}</td>" for cell in row)
            + "</tr>"
            for row in table
        )
        parts.append(f"<table>{rows}</table>")

    for action in _actions(packet):
        parts.append(
            f'<p class="action"><a href="{escaped(action["url"])}">'
            f'{escaped(action["label"])}</a></p>'
        )

    parts.extend(
        [
            f'<p class="legal">{escaped(packet.get("legal", ""))}</p>',
            f'<p class="signoff">{escaped(packet.get("signoff", ""))}</p>',
        ]
    )
    return "\n".join(parts)


def render_text(packet: Mapping[str, Any]) -> str:
    """Render plain-text email with masked sensitive facts."""
    return "\n\n".join(text for text, _ in _text_blocks(packet))


def _wrap_block(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for source_line in text.splitlines():
        if not source_line:
            lines.append("")
        else:
            lines.extend(
                textwrap.wrap(
                    source_line,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    return lines


def render_print(
    packet: Mapping[str, Any], width: int, height: int
) -> list[str]:
    """Render printable pages without splitting address or legal blocks."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    blocks = [
        (_wrap_block(text, width), atomic)
        for text, atomic in _text_blocks(packet, print_channel=True)
    ]

    for lines, atomic in blocks:
        if atomic and len(lines) > height:
            raise ValueError("an indivisible block is taller than a page")

    pages: list[list[str]] = []
    page: list[str] = []

    def finish_page() -> None:
        nonlocal page
        while page and page[-1] == "":
            page.pop()
        if page:
            pages.append(page)
        page = []

    for lines, atomic in blocks:
        separator = 1 if page else 0

        if atomic and len(page) + separator + len(lines) > height:
            finish_page()
            separator = 0

        if separator:
            page.append("")

        for line in lines:
            if len(page) == height:
                finish_page()
            if line == "" and not page:
                continue
            page.append(line)

    finish_page()
    return ["\n".join(lines) for lines in pages]


def render_sms(packet: Mapping[str, Any], budget: int) -> str:
    """Render an SMS using the specified removal priority."""
    if budget < 0:
        raise ValueError("budget must not be negative")

    facts = [
        f'{fact["label"]}: {fact["value"]}'
        for fact in packet.get("facts", [])
        if fact["sensitivity"] == "public"
    ]
    actions = [
        f'{action["label"]} {action["sms_alias"]}'
        for action in _actions(packet)
    ]
    optional = {
        "greeting": str(packet.get("greeting", "")),
        "free_text": str(packet.get("free_text", "")),
        "signoff": str(packet.get("signoff", "")),
    }
    legal = str(packet.get("legal", ""))

    def compose() -> str:
        pieces = (
            [optional["greeting"], optional["free_text"]]
            + facts
            + actions
            + [legal, optional["signoff"]]
        )
        return " ".join(piece for piece in pieces if piece)

    result = compose()

    while len(result) > budget and facts:
        facts.pop()
        result = compose()

    for key in ("free_text", "greeting", "signoff"):
        if len(result) <= budget:
            break
        optional[key] = ""
        result = compose()

    if len(result) > budget:
        raise ValueError("required action and legal wording cannot fit SMS budget")

    return result
