"""Deterministic, clearance-aware field report rendering."""

from __future__ import annotations

from copy import deepcopy
from html import escape
import json
from typing import Any, Mapping, Sequence


_FORMATS = {"text", "html", "json"}
_CLEARANCES = {"public", "admin"}
_CLEARANCE_LEVEL = {"public": 0, "admin": 1}


class Report:
    """A snapshot of report sections and referenced sources."""

    def __init__(
        self,
        sections: Sequence[dict[str, Any]],
        sources: Mapping[str, dict[str, str]],
    ) -> None:
        self.sections = deepcopy(list(sections))
        self.sources = deepcopy(dict(sources))


def validate(report: Report) -> list[str]:
    """Return stable diagnostics for references to missing sources."""

    diagnostics = {
        f"missing source: {item['source']}"
        for section in report.sections
        for item in section["items"]
        if item["kind"] == "reference" and item["source"] not in report.sources
    }
    return sorted(diagnostics)


def _visible(item: dict[str, Any], clearance: str) -> bool:
    item_clearance = item.get("clearance", "public")
    return _CLEARANCE_LEVEL[item_clearance] <= _CLEARANCE_LEVEL[clearance]


def _prepare(
    report: Report, clearance: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    source_numbers: dict[str, int] = {}
    references: list[dict[str, Any]] = []

    for section in report.sections:
        items: list[dict[str, Any]] = []
        for original in section["items"]:
            if not _visible(original, clearance):
                continue

            item = deepcopy(original)
            item.pop("clearance", None)

            if item["kind"] == "reference":
                source_id = item["source"]
                if source_id not in source_numbers:
                    number = len(references) + 1
                    source_numbers[source_id] = number
                    source = report.sources[source_id]
                    references.append(
                        {
                            "number": number,
                            "id": source_id,
                            "title": source["title"],
                            "url": source["url"],
                        }
                    )
                item["number"] = source_numbers[source_id]

            items.append(item)

        if items:
            sections.append({"title": section["title"], "items": items})

    return sections, references


def _render_text(
    sections: list[dict[str, Any]], references: list[dict[str, Any]]
) -> str:
    blocks: list[str] = []

    for section in sections:
        lines = [f"## {section['title']}"]
        for item in section["items"]:
            kind = item["kind"]
            if kind == "paragraph":
                lines.append(str(item["text"]))
            elif kind == "measurement":
                lines.append(f"{item['label']}: {item['value']} {item['unit']}")
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


def _html(value: Any) -> str:
    return escape(str(value), quote=True)


def _render_html(
    sections: list[dict[str, Any]], references: list[dict[str, Any]]
) -> str:
    output: list[str] = []
    references_by_number = {
        reference["number"]: reference for reference in references
    }

    for section in sections:
        output.append(f"<section><h2>{_html(section['title'])}</h2>")

        for item in section["items"]:
            kind = item["kind"]
            if kind == "paragraph":
                output.append(f"<p>{_html(item['text'])}</p>")
            elif kind == "measurement":
                output.append(
                    f'<p class="measurement" data-unit="{_html(item["unit"])}">'
                    f"<strong>{_html(item['label'])}:</strong> "
                    f"{_html(item['value'])} {_html(item['unit'])}</p>"
                )
            elif kind == "warning":
                output.append(f'<p class="warning">{_html(item["text"])}</p>')
            elif kind == "reference":
                reference = references_by_number[item["number"]]
                output.append(
                    f'<p class="reference">{_html(item["text"])} '
                    f'<a href="{_html(reference["url"])}">'
                    f'[{item["number"]}]</a></p>'
                )
            elif kind == "image":
                output.append(
                    f'<img src="{_html(item["path"])}" '
                    f'alt="{_html(item["alt"])}">'
                )

        output.append("</section>")

    if references:
        output.append('<ol class="references">')
        output.extend(
            f'<li><a href="{_html(reference["url"])}">'
            f'{_html(reference["title"])}</a></li>'
            for reference in references
        )
        output.append("</ol>")

    return "".join(output)


def render(report: Report, format: str, clearance: str) -> str:
    """Render a report deterministically for the selected clearance."""

    if format not in _FORMATS:
        raise ValueError(f"unknown format: {format}")
    if clearance not in _CLEARANCES:
        raise ValueError(f"unknown clearance: {clearance}")

    diagnostics = validate(report)
    if diagnostics:
        raise ValueError("\n".join(diagnostics))

    sections, references = _prepare(report, clearance)

    if format == "text":
        return _render_text(sections, references)
    if format == "html":
        return _render_html(sections, references)

    return json.dumps(
        {"sections": sections, "references": references},
        sort_keys=True,
        separators=(",", ":"),
    )
