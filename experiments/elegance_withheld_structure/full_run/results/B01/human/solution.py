"""Deterministic rendering of clearance-filtered field reports."""

from __future__ import annotations

import copy
import html
import json
from typing import Any, Mapping, Sequence


_CLEARANCE_LEVELS = {"public": 0, "admin": 1}
_FORMATS = {"text", "html", "json"}
_KINDS = {"paragraph", "measurement", "warning", "reference", "image"}


class Report:
    """A report containing titled sections and a source catalogue."""

    def __init__(
        self,
        sections: Sequence[Mapping[str, Any]],
        sources: Mapping[str, Mapping[str, str]],
    ) -> None:
        self.sections = copy.deepcopy(list(sections))
        self.sources = copy.deepcopy(dict(sources))


def validate(report: Report) -> list[str]:
    """Return stable diagnostics for references with no corresponding source."""

    missing = {
        str(item["source"])
        for section in report.sections
        for item in section["items"]
        if item.get("kind") == "reference"
        and item.get("source") not in report.sources
    }
    return [f"missing source: {source_id}" for source_id in sorted(missing)]


def _filtered_content(
    report: Report, clearance: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    limit = _CLEARANCE_LEVELS[clearance]
    sections: list[dict[str, Any]] = []
    reference_numbers: dict[str, int] = {}
    references: list[dict[str, Any]] = []

    for section in report.sections:
        visible_items: list[dict[str, Any]] = []

        for original in section["items"]:
            item_clearance = original.get("clearance", "public")
            if item_clearance not in _CLEARANCE_LEVELS:
                raise ValueError(f"unknown clearance: {item_clearance}")
            if _CLEARANCE_LEVELS[item_clearance] > limit:
                continue

            kind = original.get("kind")
            if kind not in _KINDS:
                raise ValueError(f"unknown item kind: {kind}")

            if kind in {"paragraph", "warning"}:
                item = {
                    "kind": kind,
                    "text": copy.deepcopy(original["text"]),
                }
            elif kind == "measurement":
                item = {
                    "kind": kind,
                    "label": copy.deepcopy(original["label"]),
                    "value": copy.deepcopy(original["value"]),
                    "unit": copy.deepcopy(original["unit"]),
                }
            elif kind == "image":
                item = {
                    "kind": kind,
                    "path": copy.deepcopy(original["path"]),
                    "alt": copy.deepcopy(original["alt"]),
                }
            else:
                source_id = original["source"]
                if source_id not in reference_numbers:
                    number = len(references) + 1
                    reference_numbers[source_id] = number
                    source = report.sources[source_id]
                    references.append(
                        {
                            "number": number,
                            "id": copy.deepcopy(source_id),
                            "title": copy.deepcopy(source["title"]),
                            "url": copy.deepcopy(source["url"]),
                        }
                    )

                item = {
                    "kind": kind,
                    "source": copy.deepcopy(source_id),
                    "text": copy.deepcopy(original["text"]),
                    "number": reference_numbers[source_id],
                }

            visible_items.append(item)

        if visible_items:
            sections.append(
                {
                    "title": copy.deepcopy(section["title"]),
                    "items": visible_items,
                }
            )

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
            else:
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


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _render_html(
    sections: list[dict[str, Any]], references: list[dict[str, Any]]
) -> str:
    output: list[str] = []
    references_by_number = {
        reference["number"]: reference for reference in references
    }

    for section in sections:
        parts = [f"<section><h2>{_escape(section['title'])}</h2>"]

        for item in section["items"]:
            kind = item["kind"]
            if kind == "paragraph":
                parts.append(f"<p>{_escape(item['text'])}</p>")
            elif kind == "measurement":
                parts.append(
                    f'<p class="measurement" data-unit="{_escape(item["unit"])}">'
                    f"<strong>{_escape(item['label'])}:</strong> "
                    f"{_escape(item['value'])} {_escape(item['unit'])}</p>"
                )
            elif kind == "warning":
                parts.append(
                    f'<p class="warning">{_escape(item["text"])}</p>'
                )
            elif kind == "reference":
                source = references_by_number[item["number"]]
                parts.append(
                    f'<p class="reference">{_escape(item["text"])} '
                    f'<a href="{_escape(source["url"])}">'
                    f'[{item["number"]}]</a></p>'
                )
            else:
                parts.append(
                    f'<img src="{_escape(item["path"])}" '
                    f'alt="{_escape(item["alt"])}">'
                )

        parts.append("</section>")
        output.append("".join(parts))

    if references:
        parts = ['<ol class="references">']
        parts.extend(
            f'<li><a href="{_escape(reference["url"])}">'
            f'{_escape(reference["title"])}</a></li>'
            for reference in references
        )
        parts.append("</ol>")
        output.append("".join(parts))

    return "".join(output)


def render(report: Report, format: str, clearance: str) -> str:
    """Render a report as deterministic plain text, HTML, or JSON."""

    if format not in _FORMATS:
        raise ValueError(f"unknown format: {format}")
    if clearance not in _CLEARANCE_LEVELS:
        raise ValueError(f"unknown clearance: {clearance}")

    diagnostics = validate(report)
    if diagnostics:
        raise ValueError("; ".join(diagnostics))

    sections, references = _filtered_content(report, clearance)

    if format == "text":
        return _render_text(sections, references)
    if format == "html":
        return _render_html(sections, references)

    return json.dumps(
        {"sections": sections, "references": references},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


__all__ = ["Report", "validate", "render"]
