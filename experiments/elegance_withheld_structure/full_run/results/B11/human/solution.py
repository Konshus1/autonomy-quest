from datetime import date
from decimal import Decimal


_VALID_LOCALES = {"en", "fr", "ja"}
_VALID_USES = {"wall", "mobile", "audio"}
_AUDIO_WORDS = {
    "en": {"&": "and", "×": "times", "%": "percent"},
    "fr": {"&": "et", "×": "fois", "%": "pour cent"},
    "ja": {"&": "と", "×": "かける", "%": "パーセント"},
}


def _translate(source, locale, translations, fallbacks):
    if locale == "en":
        return source

    locale_translations = translations.get(locale, {})
    if source in locale_translations:
        return locale_translations[source]

    fallbacks.add(source)
    return source


def _format_name(creator, locale):
    given = creator["given"]
    family = creator["family"]
    if locale == "ja":
        return f"{family} {given}"
    return f"{given} {family}"


def _format_date_value(value, locale):
    parsed = date.fromisoformat(value)
    if locale == "en":
        return parsed.isoformat()
    if locale == "fr":
        return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year:04d}"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _format_date_range(date_record, locale):
    start_source = date_record["start"]
    end_source = date_record["end"]
    start = _format_date_value(start_source, locale)
    if start_source == end_source:
        return start
    end = _format_date_value(end_source, locale)
    return f"{start}–{end}"


def _format_number(value):
    if isinstance(value, bool):
        raise TypeError("dimension values must be numbers")

    if isinstance(value, int):
        return str(value)

    number = Decimal(str(value))
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered == "-0":
        return "0"
    return rendered


def _format_dimensions(dimensions):
    height, width = dimensions
    return f"{_format_number(height)} × {_format_number(width)} cm"


def _format_quote(text, locale):
    if locale == "en":
        return f"“{text}”"
    if locale == "fr":
        return f"«\u00a0{text}\u00a0»"
    return f"「{text}」"


def _verbalize(text, locale):
    words = _AUDIO_WORDS[locale]
    for symbol in ("&", "×", "%"):
        text = text.replace(symbol, words[symbol])
    return text


def _render(fields, locale, audio):
    if audio:
        fields = [_verbalize(field, locale) for field in fields]
    return "\n".join(fields)


def generate_label(exhibit, locale, use, mobile_budget=None):
    if locale not in _VALID_LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    if use not in _VALID_USES:
        raise ValueError(f"unsupported use: {use}")

    translations = exhibit.get("translations", {})
    fallbacks = set()

    title = _translate(exhibit["title"], locale, translations, fallbacks)
    creators = ", ".join(
        _format_name(creator, locale) for creator in exhibit["creators"]
    )
    date_text = _format_date_range(exhibit["date"], locale)
    dimensions = _format_dimensions(exhibit["dimensions_cm"])

    paragraphs = []
    for paragraph in exhibit["paragraphs"]:
        paragraphs.append(
            {
                "id": paragraph["id"],
                "text": _translate(
                    paragraph["text"], locale, translations, fallbacks
                ),
                "optional": bool(paragraph["optional"]),
            }
        )

    quotes = [
        _format_quote(_translate(value, locale, translations, fallbacks), locale)
        for value in exhibit["quotes"]
    ]
    credits = [
        _translate(value, locale, translations, fallbacks)
        for value in exhibit["credits"]
    ]
    image_descriptions = [
        _translate(value, locale, translations, fallbacks)
        for value in exhibit["image_descriptions"]
    ]

    fixed_prefix = [title, creators, date_text, dimensions]
    fixed_suffix = quotes + credits

    if use == "audio":
        fields = (
            fixed_prefix
            + [paragraph["text"] for paragraph in paragraphs]
            + fixed_suffix
            + image_descriptions
        )
        return {
            "text": _render(fields, locale, audio=True),
            "omissions": [],
            "fallbacks": sorted(fallbacks),
        }

    included = list(paragraphs)
    omitted_ids = []

    if use == "mobile" and mobile_budget is not None:
        while True:
            fields = (
                fixed_prefix
                + [paragraph["text"] for paragraph in included]
                + fixed_suffix
            )
            rendered = _render(fields, locale, audio=False)
            if len(rendered) <= mobile_budget:
                break

            optional_index = next(
                (
                    index
                    for index in range(len(included) - 1, -1, -1)
                    if included[index]["optional"]
                ),
                None,
            )
            if optional_index is None:
                break

            omitted_ids.append(included[optional_index]["id"])
            del included[optional_index]
    else:
        fields = (
            fixed_prefix
            + [paragraph["text"] for paragraph in included]
            + fixed_suffix
        )
        rendered = _render(fields, locale, audio=False)

    return {
        "text": rendered,
        "omissions": sorted(omitted_ids),
        "fallbacks": sorted(fallbacks),
    }
