"""Deterministic rendering for clearance-aware field reports."""

from copy import deepcopy
from html import escape
import json


_FORMATS = {"text", "html", "json"}
_CLEARANCE_LEVELS = {"public": 0, "admin": 1}


class Report:
    """A report definition whose supplied data is defensively copied."""

    def __init__(self, sections, sources):
        self.sections = deepcopy(list(sections))
        self.sources = deepcopy(dict(sources))


def validate(report):
    """Return stable diagnostics for references to missing sources."""
    missing = {
        str(item["source"])
        for section in report.sections
        for item in section["items"]
        if item.get("kind") == "reference"
        and item.get("source") not in report.sources
    }
    return [f"missing source: {source_id}" for source_id in sorted(missing)]


def _is_visible(item, clearance):
    item_clearance = item.get("clearance", "public")
    if item_clearance not in _CLEARANCE_LEVELS:
        raise ValueError(f"unknown clearance: {item_clearance}")
    return _CLEARANCE_LEVELS[item_clearance] <= _CLEARANCE_LEVELS[clearance]


def _prepare(report, clearance):
    sections = []
    reference_numbers = {}
    references = []

    for section in report.sections:
        visible_items = []

        for original in section["items"]:
            if not _is_visible(original, clearance):
                continue

            item = deepcopy(dict(original))
            if item.get("kind") == "reference":
                source_id = item["source"]
                if source_id not in reference_numbers:
                    number = len(references) + 1
                    reference_numbers[source_id] = number
                    source = report.sources[source_id]
                    references.append(
                        {
                            "number": number,
                            "id": source_id,
                            "title": source["title"],
                            "url": source["url"],
                        }
                    )
                item["number"] = reference_numbers[source_id]

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
            f"[{reference['number']}] {reference['title']} — {reference['url']}"
            for reference in references
        )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _html(value):
    return escape(str(value), quote=True)


def _render_html(sections, references):
    parts = []
    sources_by_number = {
        reference["number"]: reference for reference in references
    }

    for section in sections:
        body = [f"<section><h2>{_html(section['title'])}</h2>"]

        for item in section["items"]:
            kind = item["kind"]
            if kind == "paragraph":
                body.append(f"<p>{_html(item['text'])}</p>")
            elif kind == "measurement":
                body.append(
                    f'<p class="measurement" data-unit="{_html(item["unit"])}">'
                    f"<strong>{_html(item['label'])}:</strong> "
                    f"{_html(item['value'])} {_html(item['unit'])}</p>"
                )
            elif kind == "warning":
                body.append(
                    f'<p class="warning">{_html(item["text"])}</p>'
                )
            elif kind == "reference":
                source = sources_by_number[item["number"]]
                body.append(
                    f'<p class="reference">{_html(item["text"])} '
                    f'<a href="{_html(source["url"])}">'
                    f'[{item["number"]}]</a></p>'
                )
            elif kind == "image":
                body.append(
                    f'<img src="{_html(item["path"])}" '
                    f'alt="{_html(item["alt"])}">'
                )

        body.append("</section>")
        parts.append("".join(body))

    if references:
        items = "".join(
            f'<li><a href="{_html(reference["url"])}">'
            f'{_html(reference["title"])}</a></li>'
            for reference in references
        )
        parts.append(f'<ol class="references">{items}</ol>')

    return "".join(parts)


def _render_json(sections, references):
    fields = {
        "paragraph": ("text",),
        "measurement": ("label", "value", "unit"),
        "warning": ("text",),
        "reference": ("source", "text", "number"),
        "image": ("path", "alt"),
    }
    clean_sections = []

    for section in sections:
        clean_items = []
        for item in section["items"]:
            kind = item["kind"]
            clean_item = {"kind": kind}
            clean_item.update(
                (field, item[field]) for field in fields.get(kind, ())
            )
            clean_items.append(clean_item)

        clean_sections.append(
            {"title": section["title"], "items": clean_items}
        )

    return json.dumps(
        {"sections": clean_sections, "references": references},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def render(report, format, clearance):
    """Render a validated report in the requested format and clearance."""
    if format not in _FORMATS:
        raise ValueError(f"unknown format: {format}")
    if clearance not in _CLEARANCE_LEVELS:
        raise ValueError(f"unknown clearance: {clearance}")

    diagnostics = validate(report)
    if diagnostics:
        raise ValueError("; ".join(diagnostics))

    sections, references = _prepare(report, clearance)
    if format == "text":
        return _render_text(sections, references)
    if format == "html":
        return _render_html(sections, references)
    return _render_json(sections, references)


__all__ = ["Report", "validate", "render"]
