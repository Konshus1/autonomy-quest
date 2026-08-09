from html import escape
import textwrap


def _actions(packet):
    action = packet.get("action")
    if action is None:
        return ()
    if isinstance(action, dict):
        return (action,)
    return tuple(action)


def _mask(value):
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _footnotes(actions):
    numbers = {}
    urls = []
    numbered = []

    for action in actions:
        url = action["url"]
        if url not in numbers:
            numbers[url] = len(urls) + 1
            urls.append(url)
        numbered.append((action, numbers[url]))

    return numbered, urls


def _text_blocks(packet, channel):
    blocks = []

    def add(kind, content):
        if content:
            blocks.append((kind, content))

    add("greeting", packet["greeting"])
    add("body", packet["free_text"])

    address = "\n".join(packet["address"])
    add("address", address)

    fact_lines = []
    for fact in packet["facts"]:
        if fact["sensitivity"] == "sensitive":
            if channel == "print":
                continue
            value = _mask(fact["value"])
        else:
            value = fact["value"]
        fact_lines.append(f'{fact["label"]}: {value}')
    add("facts", "\n".join(fact_lines))

    table = packet.get("table")
    if table:
        add("table", "\n".join(" | ".join(row) for row in table))

    numbered_actions, urls = _footnotes(_actions(packet))
    for action, number in numbered_actions:
        add("action", f'{action["label"]} [{number}]')

    add("legal", packet["legal"])
    add("signoff", packet["signoff"])

    if urls:
        links = ["Links:"]
        links.extend(f"[{number}] {url}" for number, url in enumerate(urls, 1))
        add("links", "\n".join(links))

    return blocks


def render_html(packet):
    parts = [
        f'<p class="greeting">{escape(packet["greeting"], quote=True)}</p>',
        f'<p class="body">{escape(packet["free_text"], quote=True)}</p>',
    ]

    address = "<br>".join(
        escape(line, quote=True) for line in packet["address"]
    )
    parts.append(f"<address>{address}</address>")

    facts = []
    for fact in packet["facts"]:
        label = escape(fact["label"], quote=True)
        value = escape(fact["value"], quote=True)
        facts.append(f"<dt>{label}</dt><dd>{value}</dd>")
    parts.append(f'<dl>{"".join(facts)}</dl>')

    table = packet.get("table")
    if table:
        rows = []
        for row in table:
            cells = "".join(
                f"<td>{escape(cell, quote=True)}</td>" for cell in row
            )
            rows.append(f"<tr>{cells}</tr>")
        parts.append(f'<table>{"".join(rows)}</table>')

    for action in _actions(packet):
        label = escape(action["label"], quote=True)
        url = escape(action["url"], quote=True)
        parts.append(
            f'<p class="action"><a href="{url}">{label}</a></p>'
        )

    parts.append(
        f'<p class="legal">{escape(packet["legal"], quote=True)}</p>'
    )
    parts.append(
        f'<p class="signoff">{escape(packet["signoff"], quote=True)}</p>'
    )
    return "\n".join(parts)


def render_text(packet):
    return "\n\n".join(
        content for _, content in _text_blocks(packet, "text")
    )


def _wrapped_lines(content, width):
    result = []
    for line in content.split("\n"):
        if line == "":
            result.append("")
            continue
        result.extend(
            textwrap.wrap(
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

    prepared = []
    for kind, content in _text_blocks(packet, "print"):
        lines = _wrapped_lines(content, width)
        atomic = kind in {"address", "legal"}
        if atomic and len(lines) > height:
            raise ValueError(f"{kind} block cannot fit on one page")
        prepared.append((atomic, lines))

    pages = []
    current = []

    def finish_page():
        nonlocal current
        if current:
            pages.append("\n".join(current))
            current = []

    for atomic, lines in prepared:
        if not lines:
            continue

        separator = 1 if current else 0
        if atomic:
            if len(current) + separator + len(lines) > height:
                finish_page()
            elif current:
                current.append("")
            current.extend(lines)
            continue

        if current:
            if len(current) + 1 >= height:
                finish_page()
            else:
                current.append("")

        index = 0
        while index < len(lines):
            available = height - len(current)
            take = min(available, len(lines) - index)
            current.extend(lines[index:index + take])
            index += take
            if index < len(lines):
                finish_page()

    finish_page()
    return pages


def render_sms(packet, budget):
    if budget < 0:
        raise ValueError("budget must be nonnegative")

    greeting = packet["greeting"]
    free_text = packet["free_text"]
    signoff = packet["signoff"]
    legal = packet["legal"]

    facts = [
        f'{fact["label"]}: {fact["value"]}'
        for fact in packet["facts"]
        if fact["sensitivity"] == "public"
    ]
    actions = [
        f'{action["label"]} {action["sms_alias"]}'
        for action in _actions(packet)
    ]

    def compose():
        pieces = [greeting, free_text]
        pieces.extend(facts)
        pieces.extend(actions)
        pieces.extend([legal, signoff])
        return " ".join(piece for piece in pieces if piece)

    result = compose()
    while len(result) > budget and facts:
        facts.pop()
        result = compose()

    if len(result) > budget and free_text:
        free_text = ""
        result = compose()
    if len(result) > budget and greeting:
        greeting = ""
        result = compose()
    if len(result) > budget and signoff:
        signoff = ""
        result = compose()

    if len(result) > budget:
        raise ValueError("required action and legal wording exceed SMS budget")
    return result
