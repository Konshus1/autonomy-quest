"""Locale-aware exhibit label generation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable


_LOCALES = {"en", "fr", "ja"}
_USES = {"wall", "mobile", "audio"}

_QUOTE_MARKS = {
    "en": ("“", "”"),
    "fr": ("«\u00a0", "\u00a0»"),
    "ja": ("「", "」"),
}

_AUDIO_WORDS = {
    "en": {"&": "and", "×": "times", "%": "percent"},
    "fr": {"&": "et", "×": "fois", "%": "pour cent"},
    "ja": {"&": "と", "×": "かける", "%": "パーセント"},
}


def _format_number(value: Any) -> str:
    decimal_value = Decimal(str(value))
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _format_name(creator: dict[str, Any], locale: str) -> str:
    given = creator["given"]
    family = creator["family"]
    if locale == "ja":
        return f"{family} {given}"
    return f"{given} {family}"


def _format_single_date(value: str, locale: str) -> str:
    parsed = date.fromisoformat(value)
    if locale == "en":
        return parsed.isoformat()
    if locale == "fr":
        return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year:04d}"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _format_date_range(value: dict[str, Any], locale: str) -> str:
    start_source = value["start"]
    end_source = value["end"]
    start = _format_single_date(start_source, locale)
    if start_source == end_source:
        return start
    end = _format_single_date(end_source, locale)
    return f"{start}–{end}"


def _format_dimensions(values: list[Any]) -> str:
    height, width = values
    return f"{_format_number(height)} × {_format_number(width)} cm"


def _verbalize_audio(text: str, locale: str) -> str:
    words = _AUDIO_WORDS[locale]
    if locale == "ja":
        return text.replace("&", words["&"]).replace("×", words["×"]).replace("%", words["%"])

    result = text
    for symbol in ("&", "×"):
        parts = result.split(symbol)
        if len(parts) > 1:
            result = f" {words[symbol]} ".join(part.strip() for part in parts)

    parts = result.split("%")
    if len(parts) > 1:
        result = f" {words['%']} ".join(part.strip() for part in parts)
    return result


def generate_label(
    exhibit: dict[str, Any],
    locale: str,
    use: str,
    mobile_budget: int | None = None,
) -> dict[str, Any]:
    """Generate an exhibit label and its omission/translation diagnostics."""

    if locale not in _LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    if use not in _USES:
        raise ValueError(f"unsupported use: {use}")

    locale_translations = exhibit.get("translations", {}).get(locale, {})

    def render(omitted_indices: set[int]) -> tuple[str, list[str]]:
        missing: set[str] = set()

        def translate(source: str) -> str:
            if locale == "en":
                return source
            if source in locale_translations:
                return locale_translations[source]
            missing.add(source)
            return source

        lines: list[str] = [translate(exhibit["title"])]
        lines.append(", ".join(_format_name(creator, locale) for creator in exhibit["creators"]))
        lines.append(_format_date_range(exhibit["date"], locale))
        lines.append(_format_dimensions(exhibit["dimensions_cm"]))

        for index, paragraph in enumerate(exhibit["paragraphs"]):
            if index not in omitted_indices:
                lines.append(translate(paragraph["text"]))

        opening, closing = _QUOTE_MARKS[locale]
        for quote in exhibit["quotes"]:
            lines.append(f"{opening}{translate(quote)}{closing}")

        for credit in exhibit["credits"]:
            lines.append(translate(credit))

        if use == "audio":
            for description in exhibit["image_descriptions"]:
                lines.append(translate(description))
            lines = [_verbalize_audio(line, locale) for line in lines]

        return "\n".join(lines), sorted(missing)

    omitted_indices: set[int] = set()

    if use == "mobile" and mobile_budget is not None:
        text, fallbacks = render(omitted_indices)
        optional_indices = [
            index
            for index, paragraph in enumerate(exhibit["paragraphs"])
            if paragraph["optional"]
        ]
        for index in reversed(optional_indices):
            if len(text) <= mobile_budget:
                break
            omitted_indices.add(index)
            text, fallbacks = render(omitted_indices)
    else:
        text, fallbacks = render(omitted_indices)

    omissions = sorted(
        exhibit["paragraphs"][index]["id"] for index in omitted_indices
    )
    return {
        "text": text,
        "omissions": omissions,
        "fallbacks": fallbacks,
    }
