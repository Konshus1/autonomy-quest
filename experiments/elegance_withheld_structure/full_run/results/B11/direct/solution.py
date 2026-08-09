from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Any


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


def _format_date(value: str, locale: str) -> str:
    parsed = date.fromisoformat(value)
    if locale == "en":
        return parsed.isoformat()
    if locale == "fr":
        return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year:04d}"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _format_number(value: int | float) -> str:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("dimensions must be finite numbers")

    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", "+0"} else rendered


def _expand_audio_symbols(text: str, locale: str) -> str:
    words = _AUDIO_WORDS[locale]

    if locale == "ja":
        for symbol, word in words.items():
            text = text.replace(symbol, word)
        return text

    text = re.sub(r"\s*&\s*", f" {words['&']} ", text)
    text = re.sub(r"\s*×\s*", f" {words['×']} ", text)
    text = re.sub(r"\s*%", f" {words['%']}", text)
    return text


def generate_label(
    exhibit: dict[str, Any],
    locale: str,
    use: str,
    mobile_budget: int | None = None,
) -> dict[str, Any]:
    if locale not in _LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    if use not in _USES:
        raise ValueError(f"unsupported use: {use}")

    fallbacks: set[str] = set()
    translations = exhibit.get("translations", {}).get(locale, {})

    def translate(source: str) -> str:
        if locale == "en":
            return source
        if source in translations:
            return translations[source]
        fallbacks.add(source)
        return source

    title = translate(exhibit["title"])
    paragraphs = [
        {
            "id": paragraph["id"],
            "text": translate(paragraph["text"]),
            "optional": paragraph["optional"],
        }
        for paragraph in exhibit["paragraphs"]
    ]
    quotes = [translate(quote) for quote in exhibit["quotes"]]
    credits = [translate(credit) for credit in exhibit["credits"]]
    image_descriptions = [
        translate(description) for description in exhibit["image_descriptions"]
    ]

    creators = ", ".join(
        (
            f"{creator['family']} {creator['given']}"
            if locale == "ja"
            else f"{creator['given']} {creator['family']}"
        )
        for creator in exhibit["creators"]
    )

    start = _format_date(exhibit["date"]["start"], locale)
    end = _format_date(exhibit["date"]["end"], locale)
    rendered_date = start if start == end else f"{start}–{end}"

    height, width = exhibit["dimensions_cm"]
    dimensions = f"{_format_number(height)} × {_format_number(width)} cm"

    opening_quote, closing_quote = _QUOTE_MARKS[locale]
    rendered_quotes = [
        f"{opening_quote}{quote}{closing_quote}" for quote in quotes
    ]

    def assemble(
        selected_paragraphs: list[dict[str, Any]],
        include_images: bool = False,
    ) -> list[str]:
        fields = [title, creators, rendered_date, dimensions]
        fields.extend(paragraph["text"] for paragraph in selected_paragraphs)
        fields.extend(rendered_quotes)
        fields.extend(credits)
        if include_images:
            fields.extend(image_descriptions)
        return fields

    selected_paragraphs = list(paragraphs)
    omissions: list[str] = []

    if use == "mobile" and mobile_budget is not None:
        while len("\n".join(assemble(selected_paragraphs))) > mobile_budget:
            optional_index = next(
                (
                    index
                    for index in range(len(selected_paragraphs) - 1, -1, -1)
                    if selected_paragraphs[index]["optional"]
                ),
                None,
            )
            if optional_index is None:
                break
            omissions.append(selected_paragraphs[optional_index]["id"])
            del selected_paragraphs[optional_index]

    fields = assemble(
        selected_paragraphs,
        include_images=(use == "audio"),
    )
    if use == "audio":
        fields = [_expand_audio_symbols(field, locale) for field in fields]

    return {
        "text": "\n".join(fields),
        "omissions": sorted(omissions),
        "fallbacks": sorted(fallbacks),
    }
