"""Deterministic rendering of clearance-aware field reports."""

from copy import deepcopy
from html import escape
import json


_FORMATS = {"text", "html", "json"}
_CLEARANCE_LEVEL = {"public": 0, "admin": 1}


class Report:
    """A report containing titled sections and a source registry."""

    def __init__(self, sections, sources):
        self.sections = deepcopy(list(sections))
        self.sources = deepcopy(dict(sources))


def validate(report):
    """Return sorted diagnostics for references missing from the registry."""
    missing = {
        item["source"]
        for section in report.sections
        for item in section["items"]
        if item.get("kind") == "reference"
        and item.get("source") not in report.sources
    }
    return [f"missing source: {source_id}" for source_id in sorted(missing)]


def _project(report, clearance):
    level = _CLEARANCE_LEVEL[clearance]
    sections = []
    references = []
    numbers = {}

    for section in report.sections:
        visible_items = []

        for original in section["items"]:
            item_clearance = original.get("clearance", "public")
            if _CLEARANCE_LEVEL.get(item_clearance, level + 1) > level:
                continue

            item = deepcopy(original)
            item.pop("clearance", None)

            if item.get("kind") == "reference":
                source_id = item["source"]
                if source_id not in numbers:
                    number = len(references) + 1
                    numbers[source_id] = number
                    source = report.sources[source_id]
                    references.append(
                        {
                            "number": number,
                            "id": source_id,
                            "title": source["title"],
                            "url": source["url"],
                        }
                    )
                item["number"] = numbers[source_id]

            visible_items.append(item)

        if visible_items:
            sections.append(
                {"title": section["title"], "items": visible_items}
            )

    return sections, references


def _render_text(sections, references):
    blocks = []

    for section in sections:
        lines = [f"## {section['title']}"]

        for item in section["items"]:
            kind = item["kind"]
            if kind == "paragraph":
                lines.append(str(item["text"]))
            elif kind == "measurement":
                lines.append(
                    f"{item['label']}: {item['value']} {item['unit']}"
                )
            elif kind == "warning":
                lines.append(f"WARNING: {item['text']}")
            elif kind == "reference":
                lines.append(f"{item['text']} [{item['number']}]")
            elif kind == "image":
                lines.append(f"[Image: {item['alt']}] ({item['path']})")

        blocks.append("\n".join(lines))

    if references:
        lines = ["References:"]
        lines.extend(
            f"[{source['number']}] {source['title']} — {source['url']}"
            for source in references
        )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _render_html(sections, references):
    output = []
    reference_by_number = {
        source["number"]: source for source in references
    }

    for section in sections:
        title = escape(str(section["title"]), quote=True)
        parts = [f"<section><h2>{title}</h2>"]

        for item in section["items"]:
            kind = item["kind"]

            if kind == "paragraph":
                text = escape(str(item["text"]), quote=True)
                parts.append(f"<p>{text}</p>")
            elif kind == "measurement":
                label = escape(str(item["label"]), quote=True)
                value = escape(str(item["value"]), quote=True)
                unit = escape(str(item["unit"]), quote=True)
                parts.append(
                    f'<p class="measurement" data-unit="{unit}">'
                    f"<strong>{label}:</strong> {value} {unit}</p>"
                )
            elif kind == "warning":
                text = escape(str(item["text"]), quote=True)
                parts.append(f'<p class="warning">{text}</p>')
            elif kind == "reference":
                source = reference_by_number[item["number"]]
                text = escape(str(item["text"]), quote=True)
                url = escape(str(source["url"]), quote=True)
                parts.append(
                    f'<p class="reference">{text} '
                    f'<a href="{url}">[{item["number"]}]</a></p>'
                )
            elif kind == "image":
                path = escape(str(item["path"]), quote=True)
                alt = escape(str(item["alt"]), quote=True)
                parts.append(f'<img src="{path}" alt="{alt}">')

        parts.append("</section>")
        output.append("".join(parts))

    if references:
        items = []
        for source in references:
            url = escape(str(source["url"]), quote=True)
            title = escape(str(source["title"]), quote=True)
            items.append(f'<li><a href="{url}">{title}</a></li>')
        output.append(f'<ol class="references">{"".join(items)}</ol>')

    return "".join(output)


def render(report, format, clearance):
    """Render a report as text, HTML, or JSON at the given clearance."""
    if format not in _FORMATS:
        raise ValueError(f"unknown format: {format}")
    if clearance not in _CLEARANCE_LEVEL:
        raise ValueError(f"unknown clearance: {clearance}")

    diagnostics = validate(report)
    if diagnostics:
        raise ValueError("; ".join(diagnostics))

    sections, references = _project(report, clearance)

    if format == "text":
        return _render_text(sections, references)
    if format == "html":
        return _render_html(sections, references)

    return json.dumps(
        {"sections": sections, "references": references},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
