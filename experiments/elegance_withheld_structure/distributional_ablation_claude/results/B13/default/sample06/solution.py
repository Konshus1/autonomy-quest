"""recipebook.py

Adapt recipes to a target yield, available equipment, and dietary
exclusions using a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is
JSON-shaped.  A catalog choice is {id, for, priority, result} where
result may contain:
    name                    replacement ingredient name (required)
    quantity_factor         Fraction multiplier on the quantity
    unit                    replacement unit
    preparation             replacement preparation wording
    wording_changes         list of {"old": ..., "new": ...} edits applied
                            to the instruction strings
    equipment_add           equipment names the substitute requires
    equipment_remove        equipment names no longer needed
    yield_factor            Fraction multiplier on the final yield
    additional_ingredients  ingredient dicts introduced by the substitute
                            (quantities authored at the original yield;
                            subject to exclusions and further substitution)

Lower numeric priority wins; catalog order breaks ties.  Neither public
function mutates its arguments.
"""

from fractions import Fraction
import copy

# Hard ceiling on substitution steps so mutually re-introducing catalogs
# terminate with a cycle reason instead of looping forever.
_MAX_STEPS = 10000


def print_original(recipe):
    """Return the authored text of the recipe byte-for-byte."""
    return recipe["authored_text"]


def _best_choice(catalog, name):
    """Lowest (priority, catalog index) choice whose `for` matches name."""
    best = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best is None or key < best[0]:
            best = (key, choice)
    return best[1] if best is not None else None


def _normalized(ingredient):
    """Fresh ingredient dict with defaults filled in (never a shared ref)."""
    return {
        "id": ingredient.get("id", ingredient["name"]),
        "name": ingredient["name"],
        "quantity": Fraction(ingredient["quantity"]),
        "unit": ingredient.get("unit", ""),
        "preparation": ingredient.get("preparation", ""),
    }


class _Adaptation:
    """Accumulates the outcome of one adapt() call."""

    def __init__(self, request, catalog, scale):
        self.excluded = set(request["excluded"])
        self.available = set(request["available_equipment"])
        self.catalog = catalog
        self.scale = scale
        self.choices = []          # catalog ids in application order
        self.warnings = set()
        self.reasons = set()
        self.wording = []          # (old, new) in application order
        self.equipment_ops = []    # ("add" | "remove", name) in order
        self.yield_factor = Fraction(1)
        self.steps = 0

    def resolve(self, ingredient, extras):
        """Adapt one ingredient to the target scale, following substitution
        chains until the name is not excluded.  Returns the adapted dict, or
        None after recording a reason.  Additional ingredients introduced by
        substitutions are appended to `extras`."""
        adapted = _normalized(ingredient)
        adapted["quantity"] *= self.scale
        seen = set()
        while adapted["name"] in self.excluded:
            name = adapted["name"]
            if name in seen or self.steps >= _MAX_STEPS:
                self.reasons.add("substitution cycle involving %s" % name)
                return None
            seen.add(name)
            self.steps += 1
            choice = _best_choice(self.catalog, name)
            if choice is None:
                self.reasons.add("no substitution for %s" % name)
                return None
            self.choices.append(choice["id"])
            result = choice["result"]
            adapted["name"] = result["name"]
            adapted["quantity"] *= result.get("quantity_factor", Fraction(1))
            if "unit" in result:
                adapted["unit"] = result["unit"]
            if "preparation" in result:
                adapted["preparation"] = result["preparation"]
            for change in result.get("wording_changes", []):
                self.wording.append((change["old"], change["new"]))
            for item in result.get("equipment_add", []):
                self.equipment_ops.append(("add", item))
            for item in result.get("equipment_remove", []):
                self.equipment_ops.append(("remove", item))
            factor = result.get("yield_factor")
            if factor is not None and factor != 1:
                self.yield_factor *= Fraction(factor)
                self.warnings.add("substitution %s changed the yield"
                                  % choice["id"])
            extras.extend(result.get("additional_ingredients", []))
            if adapted["name"] in self.excluded:
                self.warnings.add(
                    "substitution for %s introduced excluded ingredient %s"
                    % (name, adapted["name"]))
        return adapted

    def adapt_list(self, ingredients):
        """Adapt a whole ingredient list, including ingredients introduced
        by substitutions (which are themselves subject to exclusions)."""
        out = []
        queue = list(ingredients)
        while queue:
            ingredient = queue.pop(0)
            extras = []
            resolved = self.resolve(ingredient, extras)
            if resolved is not None:
                out.append(resolved)
            queue.extend(extras)
        return out


def adapt(recipe, request, catalog):
    """Adapt `recipe` to the request, or report every applicable reason
    why no adaptation is possible.  Inputs are not mutated."""
    scale = Fraction(request["target_yield"]) / Fraction(recipe["yield"])
    ctx = _Adaptation(request, catalog, scale)

    ingredients = ctx.adapt_list(recipe["ingredients"])

    components = []
    for component in recipe.get("components") or []:
        adapted_component = copy.deepcopy(component)
        if isinstance(component, dict) and "ingredients" in component:
            adapted_component["ingredients"] = \
                ctx.adapt_list(component["ingredients"])
        components.append(adapted_component)

    equipment = set(recipe["equipment"])
    for op, name in ctx.equipment_ops:
        if op == "add":
            equipment.add(name)
        else:
            equipment.discard(name)
    equipment = sorted(equipment)
    for name in equipment:
        if name not in ctx.available:
            ctx.reasons.add("equipment %s unavailable" % name)

    instructions = []
    for line in recipe["instructions"]:
        for old, new in ctx.wording:
            line = line.replace(old, new)
        instructions.append(line)

    if ctx.reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": list(ctx.choices),
            "warnings": sorted(ctx.warnings),
            "reasons": sorted(ctx.reasons),
        }

    adapted = {
        "title": recipe["title"],
        "yield": Fraction(request["target_yield"]) * ctx.yield_factor,
        "ingredients": ingredients,
        "equipment": equipment,
        "instructions": instructions,
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": list(ctx.choices),
        "warnings": sorted(ctx.warnings),
        "reasons": [],
    }
