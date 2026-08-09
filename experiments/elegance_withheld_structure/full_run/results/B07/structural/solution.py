"""Render customer correspondence packets for HTML, text, print, and SMS."""

from html import escape
from textwrap import wrap


def _actions(packet):
    action = packet.get("action")
    if action is None:
        return []
    if isinstance(action, dict):
        return [action]
    return list(action)


def _mask(value):
    visible = min(4, len(value))
    return "*" * (len(value) - visible) + value[-visible:] if visible else ""


def render_html(packet):
    parts = [
        '<p class="greeting">{}</p>'.format(
            escape(packet["greeting"], quote=True)
        ),
        '<p class="body">{}</p>'.format(
            escape(packet["free_text"], quote=True)
        ),
        "<address>{}</address>".format(
            "<br>".join(
                escape(line, quote=True) for line in packet["address"]
            )
        ),
    ]

    facts = "".join(
        "<dt>{}</dt><dd>{}</dd>".format(
            escape(fact["label"], quote=True),
            escape(fact["value"], quote=True),
        )
        for fact in packet["facts"]
    )
    parts.append("<dl>{}</dl>".format(facts))

    table = packet.get("table")
    if table:
        rows = "".join(
            "<tr>{}</tr>".format(
                "".join(
                    "<td>{}</td>".format(escape(cell, quote=True))
                    for cell in row
                )
            )
            for row in table
        )
        parts.append("<table>{}</table>".format(rows))

    for action in _actions(packet):
        parts.append(
            '<p class="action"><a href="{}">{}</a></p>'.format(
                escape(action["url"], quote=True),
                escape(action["label"], quote=True),
            )
        )

    parts.extend(
        [
            '<p class="legal">{}</p>'.format(
                escape(packet["legal"], quote=True)
            ),
            '<p class="signoff">{}</p>'.format(
                escape(packet["signoff"], quote=True)
            ),
        ]
    )
    return "\n".join(parts)


def _text_blocks(packet, channel):
    blocks = []

    def add(text, kind="ordinary"):
        if text:
            blocks.append((text, kind))

    add(packet["greeting"])
    add(packet["free_text"])
    add("\n".join(packet["address"]), "address")

    fact_lines = []
    for fact in packet["facts"]:
        sensitive = fact["sensitivity"] == "sensitive"
        if channel == "print" and sensitive:
            continue
        value = _mask(fact["value"]) if channel == "text" and sensitive else fact["value"]
        fact_lines.append("{}: {}".format(fact["label"], value))
    add("\n".join(fact_lines))

    table = packet.get("table")
    if table:
        add("\n".join(" | ".join(row) for row in table))

    url_numbers = {}
    urls = []
    for action in _actions(packet):
        url = action["url"]
        if url not in url_numbers:
            url_numbers[url] = len(urls) + 1
            urls.append(url)
        add("{} [{}]".format(action["label"], url_numbers[url]))

    add(packet["legal"], "legal")
    add(packet["signoff"])

    if urls:
        add(
            "Links:\n"
            + "\n".join(
                "[{}] {}".format(number, url)
                for number, url in enumerate(urls, 1)
            )
        )

    return blocks


def render_text(packet):
    return "\n\n".join(
        text for text, _kind in _text_blocks(packet, "text")
    )


def _wrapped_lines(text, width):
    result = []
    for line in text.split("\n"):
        if line == "":
            result.append("")
        else:
            result.extend(
                wrap(
                    line,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    return result


def render_print(packet, width, height):
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    blocks = []
    for text, kind in _text_blocks(packet, "print"):
        lines = _wrapped_lines(text, width)
        atomic = kind in {"address", "legal"}
        if atomic and len(lines) > height:
            raise ValueError("address or legal block exceeds page height")
        blocks.append((lines, atomic))

    pages = []
    page = []

    def finish_page():
        nonlocal page
        if page:
            pages.append("\n".join(page))
            page = []

    for lines, atomic in blocks:
        if not lines:
            continue

        if atomic:
            required = len(lines) + (1 if page else 0)
            if page and len(page) + required > height:
                finish_page()
            if page:
                page.append("")
            page.extend(lines)
            if len(page) == height:
                finish_page()
            continue

        if page:
            if height - len(page) < 2:
                finish_page()
            else:
                page.append("")

        index = 0
        while index < len(lines):
            available = height - len(page)
            take = min(available, len(lines) - index)
            page.extend(lines[index:index + take])
            index += take
            if len(page) == height:
                finish_page()

    finish_page()
    return pages


def render_sms(packet, budget):
    if budget < 0:
        raise ValueError("budget must be nonnegative")

    facts = [
        "{}: {}".format(fact["label"], fact["value"])
        for fact in packet["facts"]
        if fact["sensitivity"] == "public"
    ]
    actions = [
        "{} {}".format(action["label"], action["sms_alias"])
        for action in _actions(packet)
    ]
    optional = {
        "greeting": packet["greeting"],
        "free_text": packet["free_text"],
        "signoff": packet["signoff"],
    }

    def compose():
        pieces = []
        if optional["greeting"]:
            pieces.append(optional["greeting"])
        if optional["free_text"]:
            pieces.append(optional["free_text"])
        pieces.extend(facts)
        pieces.extend(actions)
        if packet["legal"]:
            pieces.append(packet["legal"])
        if optional["signoff"]:
            pieces.append(optional["signoff"])
        return " ".join(piece for piece in pieces if piece)

    result = compose()
    while len(result) > budget and facts:
        facts.pop()
        result = compose()

    for field in ("free_text", "greeting", "signoff"):
        if len(result) <= budget:
            break
        optional[field] = ""
        result = compose()

    if len(result) > budget:
        raise ValueError("mandatory SMS content exceeds budget")
    return result
