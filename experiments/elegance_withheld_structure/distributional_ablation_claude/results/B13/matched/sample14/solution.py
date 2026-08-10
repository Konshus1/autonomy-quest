"""recipebook.py -- adapt authored recipes to a target yield, available
equipment, and dietary exclusions using a prioritized substitution catalog.

Quantities and yields are fractions.Fraction; recipes, requests, and
catalogs are plain JSON-shaped dicts and lists. Standard library only.
"""

from fractions import Fraction
import copy

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _candidates(catalog, name):
    """Catalog choices for `name`, best first: lowest numeric priority wins,
    catalog order breaks ties."""
    ranked = sorted(
        (c.get("priority", 0), i) for i, c in enumerate(catalog)
        if c.get("for") == name
    )
    return [catalog[i] for _, i in ranked]


class _Pass:
    """Accumulated effects of adapting one ingredient list."""

    def __init__(self):
        self.reasons = set()
        self.warnings = set()
        self.choices = []
        self.wording = []          # ordered {old,new} pairs
        self.add_equipment = []
        self.remove_equipment = []
        self.yield_factor = Fraction(1)
        self.ingredients = []

    def merge(self, other):
        self.reasons |= other.reasons
        self.warnings |= other.warnings
        self.choices += other.choices
        self.wording += other.wording
        self.add_equipment += other.add_equipment
        self.remove_equipment += other.remove_equipment
        self.yield_factor *= other.yield_factor


def _substitute(ingredients, excluded, catalog, scale):
    """Scale every ingredient and replace excluded ones, following chains
    when a replacement is itself excluded. Additional ingredients from a
    result join the worklist and receive identical treatment."""
    p = _Pass()
    queue = [copy.deepcopy(i) for i in ingredients]
    while queue:
        ing = dict(queue.pop(0))
        ing.setdefault("id", ing["name"])
        ing.setdefault("preparation", "")
        ing.setdefault("unit", "")
        ing["quantity"] = Fraction(ing.get("quantity", 0)) * scale
        visited = {ing["name"]}
        failed = False
        while ing["name"] in excluded:
            cands = _candidates(catalog, ing["name"])
            if not cands:
                p.reasons.add("no substitution for %s" % ing["name"])
                failed = True
                break
            choice = cands[0]
            result = choice["result"]
            p.choices.append(choice["id"])
            previous = ing["name"]
            ing["name"] = result["name"]
            if "quantity_factor" in result:
                ing["quantity"] *= Fraction(result["quantity_factor"])
            if "unit" in result:
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            p.wording += list(result.get("wording_changes", []))
            p.add_equipment += list(result.get("add_equipment", []))
            p.remove_equipment += list(result.get("remove_equipment", []))
            if "yield_factor" in result:
                p.yield_factor *= Fraction(result["yield_factor"])
                p.warnings.add("substitution %s changes yield" % choice["id"])
            for extra in result.get("additional_ingredients", []):
                queue.append(copy.deepcopy(extra))
            if ing["name"] in excluded:
                p.warnings.add(
                    "substitution for %s introduced excluded ingredient %s"
                    % (previous, ing["name"]))
                if ing["name"] in visited:
                    p.reasons.add(
                        "substitution cycle involving %s" % ing["name"])
                    failed = True
                    break
            visited.add(ing["name"])
        if not failed:
            p.ingredients.append(ing)
    return p


def _ingredient_line(ing):
    line = "- %s %s %s" % (ing["quantity"], ing.get("unit", ""), ing["name"])
    if ing.get("preparation"):
        line += ", %s" % ing["preparation"]
    return line


def _render(recipe):
    """Regenerate authored text for an adapted recipe."""
    lines = [recipe["title"], "Yield: %s" % recipe["yield"], "", "Ingredients:"]
    for ing in recipe["ingredients"]:
        lines.append(_ingredient_line(ing))
    if recipe["equipment"]:
        lines.append("")
        lines.append("Equipment: %s" % ", ".join(recipe["equipment"]))
    lines.append("")
    lines.append("Instructions:")
    for n, step in enumerate(recipe["instructions"], 1):
        lines.append("%d. %s" % (n, step))
    for comp in recipe["components"]:
        lines.append("")
        header = "Component: %s" % comp.get("name", "")
        if comp.get("optional"):
            header += " (optional)"
        lines.append(header)
        for ing in comp.get("ingredients", []):
            lines.append(_ingredient_line(ing))
        for n, step in enumerate(comp.get("instructions", []), 1):
            lines.append("%d. %s" % (n, step))
    return "\n".join(lines) + "\n"


def adapt(recipe, request, catalog):
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    total = _Pass()
    main = _substitute(recipe["ingredients"], excluded, catalog, scale)
    total.merge(main)

    equipment = set(recipe.get("equipment", []))
    kept_components = []
    for comp in recipe.get("components", []):
        cp = _substitute(comp.get("ingredients", []), excluded, catalog, scale)
        if cp.reasons and comp.get("optional"):
            total.warnings.add(
                "omitted optional component %s" % comp.get("name", ""))
            continue
        total.merge(cp)
        equipment |= set(comp.get("equipment", []))
        new_comp = copy.deepcopy(comp)
        new_comp["ingredients"] = cp.ingredients
        kept_components.append(new_comp)

    equipment |= set(total.add_equipment)
    equipment -= set(total.remove_equipment)
    for name in equipment:
        if name not in available:
            total.reasons.add("equipment %s unavailable" % name)

    if total.reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": total.choices,
            "warnings": sorted(total.warnings),
            "reasons": sorted(total.reasons),
        }

    def rewrite(text):
        for wc in total.wording:
            text = text.replace(wc["old"], wc["new"])
        return text

    for comp in kept_components:
        if "instructions" in comp:
            comp["instructions"] = [rewrite(s) for s in comp["instructions"]]

    adapted = {
        "title": recipe["title"],
        "yield": target_yield * total.yield_factor,
        "ingredients": main.ingredients,
        "equipment": sorted(equipment),
        "instructions": [rewrite(s) for s in recipe["instructions"]],
        "components": kept_components,
    }
    adapted["authored_text"] = _render(adapted)
    return {
        "possible": True,
        "recipe": adapted,
        "choices": total.choices,
        "warnings": sorted(total.warnings),
        "reasons": [],
    }
