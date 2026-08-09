import json
from fractions import Fraction
from copy import deepcopy

def adapt(recipe, request, catalog):
    # Deep copy to avoid mutation
    recipe = deepcopy(recipe)
    request = deepcopy(request)
    catalog = deepcopy(catalog)

    target_yield = request['target_yield']
    available_equipment = set(request['available_equipment'])
    excluded = set(request['excluded'])

    reasons = []
    choices = []
    warnings = []

    # Scale yield and ingredient quantities
    scale = target_yield / recipe['yield']
    recipe['yield'] = target_yield
    for ing in recipe['ingredients']:
        ing['quantity'] *= scale

    # Prepare substitution lookup: by id and by name
    subst_by_id = {}
    subst_by_name = {}
    for choice in catalog:
        if choice['for'] in subst_by_id:
            subst_by_id[choice['for']].append(choice)
        else:
            subst_by_id[choice['for']] = [choice]
        # Also index by name? We'll handle by id primarily, but also by name if needed
        # We'll also index by name for ingredients that have name matching
        # But 'for' is likely an ingredient id, so we'll use that.

    # Sort each list by priority then original order (catalog order is preserved in list)
    for key in subst_by_id:
        subst_by_id[key].sort(key=lambda c: (c['priority'], catalog.index(c)))

    # We'll also need to handle substitutions for additional ingredients introduced
    # We'll process ingredients iteratively, tracking visited ids to detect cycles
    visited = set()
    ingredient_list = recipe['ingredients']
    i = 0
    while i < len(ingredient_list):
        ing = ingredient_list[i]
        ing_id = ing['id']
        if ing_id in visited:
            # cycle detected
            reasons.append(f"substitution cycle involving {ing['name']}")
            i += 1
            continue
        visited.add(ing_id)

        # Find best substitution for this ingredient
        best = None
        if ing_id in subst_by_id:
            best = subst_by_id[ing_id][0]
        # Also check by name? The contract says 'for' is an id, but we'll also allow name match
        elif ing['name'] in subst_by_name:
            best = subst_by_name[ing['name']][0]

        if best is not None:
            choices.append(best['id'])
            result = best['result']
            # Apply quantity factor
            if 'quantity_factor' in result:
                ing['quantity'] *= result['quantity_factor']
            # Apply unit
            if 'unit' in result:
                ing['unit'] = result['unit']
            # Apply preparation
            if 'preparation' in result:
                ing['preparation'] = result['preparation']
            # Apply name change
            if 'name' in result:
                ing['name'] = result['name']
            # Apply equipment changes
            if 'equipment_additions' in result:
                recipe['equipment'].extend(result['equipment_additions'])
            if 'equipment_removals' in result:
                for eq in result['equipment_removals']:
                    if eq in recipe['equipment']:
                        recipe['equipment'].remove(eq)
            # Apply wording changes
            if 'wording_changes' in result:
                for change in result['wording_changes']:
                    old = change['old']
                    new = change['new']
                    for idx, instr in enumerate(recipe['instructions']):
                        if old in instr:
                            recipe['instructions'][idx] = instr.replace(old, new)
            # Add additional ingredients
            if 'additional_ingredients' in result:
                for add in result['additional_ingredients']:
                    # Scale additional ingredient quantity by scale and factor? The contract says additional ingredients are subject to same exclusions and catalog choices.
                    # We'll add them with quantity scaled by scale (and possibly factor if substitution applies later)
                    new_ing = deepcopy(add)
                    new_ing['quantity'] *= scale
                    ingredient_list.append(new_ing)
                    # We'll process them later in the loop
        i += 1

    # After substitutions, check exclusions
    for ing in ingredient_list:
        if ing['name'] in excluded:
            reasons.append(f"no substitution for {ing['name']}")

    # Check equipment availability
    for eq in recipe['equipment']:
        if eq not in available_equipment:
            reasons.append(f"equipment {eq} unavailable")

    # Check for cycles: we already detected during processing, but also check if any ingredient appears twice? Actually cycle detection is about substitution cycles, not duplicate ingredients.
    # We'll also check if any ingredient appears in excluded after substitution? Already done.

    if reasons:
        # Sort reasons and return impossible
        reasons = sorted(set(reasons))
        return {
            'possible': False,
            'recipe': None,
            'choices': choices,
            'warnings': sorted(warnings),
            'reasons': reasons
        }

    # Sort equipment
    recipe['equipment'] = sorted(recipe['equipment'])

    # Ensure no excluded ingredients remain (should be already)
    # Return possible
    return {
        'possible': True,
        'recipe': recipe,
        'choices': choices,
        'warnings': sorted(warnings),
        'reasons': []
    }

def print_original(recipe):
    return recipe['authored_text']
