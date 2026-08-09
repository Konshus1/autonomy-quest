import json
from fractions import Fraction
from copy import deepcopy

def adapt(recipe, request, catalog):
    # Deep copy to avoid mutation
    recipe = deepcopy(recipe)
    request = deepcopy(request)
    catalog = deepcopy(catalog)

    # Initialize result
    result = {
        'possible': True,
        'recipe': None,
        'choices': [],
        'warnings': [],
        'reasons': []
    }

    # Prepare ingredient map by id
    ingredients = {ing['id']: ing for ing in recipe['ingredients']}

    # Track substitutions applied per ingredient id
    applied_subs = {}

    # Function to resolve substitutions for an ingredient id
    def resolve_sub(id, visited):
        if id in visited:
            # Cycle detected
            cycle_ing = ingredients[id]
            result['reasons'].append(f"substitution cycle involving {cycle_ing['name']}")
            return False
        visited = visited | {id}
        # Find applicable catalog choices for this ingredient id
        choices = [c for c in catalog if c['for'] == id]
        if not choices:
            return True
        # Sort by priority then original order
        choices.sort(key=lambda c: (c['priority'], catalog.index(c)))
        for choice in choices:
            # Check if choice introduces excluded ingredient
            new_name = choice['result'].get('name')
            if new_name and new_name in request['excluded']:
                # This choice would introduce excluded ingredient, skip
                continue
            # Apply substitution
            ing = ingredients[id]
            # Record choice
            result['choices'].append(choice['id'])
            # Apply quantity factor
            if 'quantity_factor' in choice['result']:
                factor = Fraction(choice['result']['quantity_factor'])
                ing['quantity'] = ing['quantity'] * factor
            # Apply unit
            if 'unit' in choice['result']:
                ing['unit'] = choice['result']['unit']
            # Apply preparation
            if 'preparation' in choice['result']:
                ing['preparation'] = choice['result']['preparation']
            # Apply name
            if 'name' in choice['result']:
                ing['name'] = choice['result']['name']
            # Apply wording changes
            if 'wording_changes' in choice['result']:
                for change in choice['result']['wording_changes']:
                    old = change['old']
                    new = change['new']
                    for i, instr in enumerate(recipe['instructions']):
                        if old in instr:
                            recipe['instructions'][i] = instr.replace(old, new)
            # Apply equipment additions/removals
            if 'equipment_additions' in choice['result']:
                for eq in choice['result']['equipment_additions']:
                    if eq not in recipe['equipment']:
                        recipe['equipment'].append(eq)
            if 'equipment_removals' in choice['result']:
                for eq in choice['result']['equipment_removals']:
                    if eq in recipe['equipment']:
                        recipe['equipment'].remove(eq)
            # Apply additional ingredients
            if 'additional_ingredients' in choice['result']:
                for add_ing in choice['result']['additional_ingredients']:
                    # Create new ingredient with unique id
                    new_id = f"{id}_add_{len(recipe['ingredients'])}"
                    new_ing = {
                        'id': new_id,
                        'name': add_ing['name'],
                        'quantity': Fraction(add_ing['quantity']),
                        'unit': add_ing.get('unit', ''),
                        'preparation': add_ing.get('preparation', '')
                    }
                    recipe['ingredients'].append(new_ing)
                    ingredients[new_id] = new_ing
                    # Recursively resolve substitutions for new ingredient
                    if not resolve_sub(new_id, visited):
                        return False
            # Mark substitution applied
            applied_subs[id] = choice['id']
            return True
        # No applicable choice found
        ing = ingredients[id]
        result['reasons'].append(f"no substitution for {ing['name']}")
        return False

    # Resolve substitutions for all original ingredients
    for ing in recipe['ingredients']:
        if not resolve_sub(ing['id'], set()):
            result['possible'] = False
            break

    if not result['possible']:
        result['reasons'] = sorted(set(result['reasons']))
        result['recipe'] = None
        return result

    # Scale quantities and yield
    original_yield = recipe['yield']
    target_yield = request['target_yield']
    scale = target_yield / original_yield
    for ing in recipe['ingredients']:
        ing['quantity'] = ing['quantity'] * scale
    recipe['yield'] = target_yield

    # Check exclusions
    excluded = set(request['excluded'])
    for ing in recipe['ingredients']:
        if ing['name'] in excluded:
            result['reasons'].append(f"no substitution for {ing['name']}")
            result['possible'] = False

    # Check equipment
    available = set(request['available_equipment'])
    recipe['equipment'] = sorted(recipe['equipment'])
    for eq in recipe['equipment']:
        if eq not in available:
            result['reasons'].append(f"equipment {eq} unavailable")
            result['possible'] = False

    if not result['possible']:
        result['reasons'] = sorted(set(result['reasons']))
        result['recipe'] = None
        return result

    # Build final recipe
    result['recipe'] = {
        'title': recipe['title'],
        'yield': recipe['yield'],
        'ingredients': recipe['ingredients'],
        'equipment': recipe['equipment'],
        'instructions': recipe['instructions'],
        'components': recipe.get('components', []),
        'authored_text': recipe['authored_text']
    }
    result['warnings'] = sorted(result['warnings'])
    result['reasons'] = []
    return result

def print_original(recipe):
    return recipe['authored_text']
