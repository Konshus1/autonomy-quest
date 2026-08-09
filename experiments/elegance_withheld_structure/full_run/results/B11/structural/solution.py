"""Locale- and use-specific exhibit label generation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


_LOCALES = {"en", "fr", "ja"}
_USES = {"wall", "mobile", "audio"}
_AUDIO_WORDS = {
    "en": {"&": "and", "×": "times", "%": "percent"},
    "fr": {"&": "et", "×": "fois", "%": "pour cent"},
    "ja": {"&": "と", "×": "かける", "%": "パーセント"},
}


def _translate(
    source: str,
    locale_code: str,
    translations: dict[str, Any],
    fallbacks: set[str],
) -> str:
    if locale_code == "en":
        return source

    locale_translations = translations.get(locale_code, {})
    if source in locale_translations:
        return locale_translations[source]

    fallbacks.add(source)
    return source


def _format_name(creator: dict[str, str], locale_code: str) -> str:
    if locale_code == "ja":
        return f"{creator['family']} {creator['given']}"
    return f"{creator['given']} {creator['family']}"


def _format_date(value: str, locale_code: str) -> str:
    parsed = date.fromisoformat(value)

    if locale_code == "en":
        return parsed.isoformat()
    if locale_code == "fr":
        return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year:04d}"
    return f"{parsed.year:04d}年{parsed.month}月{parsed.day}日"


def _format_date_range(date_range: dict[str, str], locale_code: str) -> str:
    start = _format_date(date_range["start"], locale_code)
    end = _format_date(date_range["end"], locale_code)
    return start if start == end else f"{start}–{end}"


def _format_number(value: int | float) -> str:
    rendered = format(Decimal(str(value)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _format_dimensions(dimensions: list[int | float]) -> str:
    height, width = dimensions
    return f"{_format_number(height)} × {_format_number(width)} cm"


def _quote(text: str, locale_code: str) -> str:
    if locale_code == "en":
        return f"“{text}”"
    if locale_code == "fr":
        return f"« {text} »"
    return f"「{text}」"


def _verbalize(text: str, locale_code: str) -> str:
    for symbol, word in _AUDIO_WORDS[locale_code].items():
        text = text.replace(symbol, word)
    return text


def generate_label(
    exhibit: dict[str, Any],
    locale: str,
    use: str,
    mobile_budget: int | None = None,
) -> dict[str, Any]:
    """Generate an exhibit label and its omission/fallback diagnostics."""
    if locale not in _LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    if use not in _USES:
        raise ValueError(f"unsupported use: {use}")

    translations = exhibit.get("translations", {})
    fallbacks: set[str] = set()

    title = _translate(exhibit["title"], locale, translations, fallbacks)
    creators = ", ".join(
        _format_name(creator, locale) for creator in exhibit["creators"]
    )
    rendered_date = _format_date_range(exhibit["date"], locale)
    dimensions = _format_dimensions(exhibit["dimensions_cm"])

    paragraphs = [
        {
            "id": paragraph["id"],
            "text": _translate(
                paragraph["text"], locale, translations, fallbacks
            ),
            "optional": paragraph["optional"],
        }
        for paragraph in exhibit["paragraphs"]
    ]
    quotes = [
        _quote(_translate(source, locale, translations, fallbacks), locale)
        for source in exhibit["quotes"]
    ]
    credits = [
        _translate(source, locale, translations, fallbacks)
        for source in exhibit["credits"]
    ]
    translated_image_descriptions = [
        _translate(source, locale, translations, fallbacks)
        for source in exhibit["image_descriptions"]
    ]
    image_descriptions = (
        translated_image_descriptions if use == "audio" else []
    )

    omitted: set[str] = set()

    def assemble() -> list[str]:
        included_paragraphs = [
            paragraph["text"]
            for paragraph in paragraphs
            if paragraph["id"] not in omitted
        ]
        return [
            title,
            creators,
            rendered_date,
            dimensions,
            *included_paragraphs,
            *quotes,
            *credits,
            *image_descriptions,
        ]

    if use == "mobile" and mobile_budget is not None:
        for paragraph in reversed(paragraphs):
            if len("\n".join(assemble())) <= mobile_budget:
                break
            if paragraph["optional"]:
                omitted.add(paragraph["id"])

    fields = assemble()
    if use == "audio":
        fields = [_verbalize(field, locale) for field in fields]

    return {
        "text": "\n".join(fields),
        "omissions": sorted(omitted),
        "fallbacks": sorted(fallbacks),
    }
