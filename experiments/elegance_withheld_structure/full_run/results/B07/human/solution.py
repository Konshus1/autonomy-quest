"""Render customer correspondence packets for several delivery channels."""

from html import escape
import textwrap


def _actions(packet):
    value = packet.get("action")
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _mask(value):
    if len(value) < 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _projected_facts(packet, channel):
    projected = []
    for fact in packet["facts"]:
        value = fact["value"]
        if fact["sensitivity"] == "sensitive":
            if channel in ("print", "sms"):
                continue
            if channel == "text":
                value = _mask(value)
        projected.append((fact["label"], value))
    return projected


def render_html(packet):
    """Return an HTML fragment containing all packet blocks."""
    e = lambda value: escape(value, quote=True)
    parts = [
        '<p class="greeting">{}</p>'.format(e(packet["greeting"])),
        '<p class="body">{}</p>'.format(e(packet["free_text"])),
        "<address>{}</address>".format(
            "<br>".join(e(line) for line in packet["address"])
        ),
    ]

    facts = "".join(
        "<dt>{}</dt><dd>{}</dd>".format(e(label), e(value))
        for label, value in _projected_facts(packet, "html")
    )
    parts.append("<dl>{}</dl>".format(facts))

    table = packet.get("table")
    if table:
        rows = "".join(
            "<tr>{}</tr>".format(
                "".join("<td>{}</td>".format(e(cell)) for cell in row)
            )
            for row in table
        )
        parts.append("<table>{}</table>".format(rows))

    for action in _actions(packet):
        parts.append(
            '<p class="action"><a href="{}">{}</a></p>'.format(
                e(action["url"]), e(action["label"])
            )
        )

    parts.extend(
        [
            '<p class="legal">{}</p>'.format(e(packet["legal"])),
            '<p class="signoff">{}</p>'.format(e(packet["signoff"])),
        ]
    )
    return "\n".join(parts)


def _plain_blocks(packet, channel):
    """Build fresh (kind, text) blocks and assign stable URL footnotes."""
    blocks = []

    def add(kind, text):
        if text:
            blocks.append((kind, text))

    add("greeting", packet["greeting"])
    add("body", packet["free_text"])
    add("address", "\n".join(packet["address"]))

    fact_lines = [
        "{}: {}".format(label, value)
        for label, value in _projected_facts(packet, channel)
    ]
    add("facts", "\n".join(fact_lines))

    table = packet.get("table")
    if table:
        add("table", "\n".join(" | ".join(row) for row in table))

    numbers = {}
    ordered_urls = []
    for action in _actions(packet):
        url = action["url"]
        if url not in numbers:
            numbers[url] = len(ordered_urls) + 1
            ordered_urls.append(url)
        add("action", "{} [{}]".format(action["label"], numbers[url]))

    add("legal", packet["legal"])
    add("signoff", packet["signoff"])
    if ordered_urls:
        add(
            "links",
            "Links:\n" + "\n".join(
                "[{}] {}".format(numbers[url], url) for url in ordered_urls
            ),
        )
    return blocks


def render_text(packet):
    """Return the plain-email representation of a packet."""
    return "\n\n".join(text for _, text in _plain_blocks(packet, "text"))


def _wrapped_lines(text, width):
    result = []
    for line in text.split("\n"):
        result.extend(
            textwrap.wrap(
                line,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )
    return result


def render_print(packet, width, height):
    """Return printable pages constrained by line width and page height."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    prepared = []
    for kind, text in _plain_blocks(packet, "print"):
        lines = _wrapped_lines(text, width)
        atomic = kind in ("address", "legal")
        if atomic and len(lines) > height:
            raise ValueError("{} block does not fit on a page".format(kind))
        prepared.append((atomic, lines))

    pages = []
    page = []

    def finish_page():
        nonlocal page
        if page:
            pages.append("\n".join(page))
            page = []

    for atomic, lines in prepared:
        if atomic:
            needed = len(lines) + (1 if page else 0)
            if page and needed > height:
                finish_page()
            if page:
                page.append("")
            page.extend(lines)
            continue

        if page:
            if len(page) + 1 >= height:
                finish_page()
            else:
                page.append("")

        index = 0
        while index < len(lines):
            room = height - len(page)
            take = min(room, len(lines) - index)
            page.extend(lines[index:index + take])
            index += take
            if index < len(lines):
                finish_page()

    finish_page()
    return pages


def render_sms(packet, budget):
    """Return the most complete SMS permitted by the supplied budget."""
    public_facts = [
        "{}: {}".format(fact["label"], fact["value"])
        for fact in packet["facts"]
        if fact["sensitivity"] == "public"
    ]
    actions = [
        "{} {}".format(action["label"], action["sms_alias"])
        for action in _actions(packet)
    ]
    include_free_text = True
    include_greeting = True
    include_signoff = True

    def compose():
        pieces = []
        if include_greeting and packet["greeting"]:
            pieces.append(packet["greeting"])
        if include_free_text and packet["free_text"]:
            pieces.append(packet["free_text"])
        pieces.extend(public_facts)
        pieces.extend(piece for piece in actions if piece)
        if packet["legal"]:
            pieces.append(packet["legal"])
        if include_signoff and packet["signoff"]:
            pieces.append(packet["signoff"])
        return " ".join(pieces)

    result = compose()
    while len(result) > budget and public_facts:
        public_facts.pop()
        result = compose()
    if len(result) > budget:
        include_free_text = False
        result = compose()
    if len(result) > budget:
        include_greeting = False
        result = compose()
    if len(result) > budget:
        include_signoff = False
        result = compose()
    if len(result) > budget:
        raise ValueError("required action and legal wording exceed SMS budget")
    return result
