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

    # Scale ingredients and yield
    original_yield = recipe['yield']
    target_yield = request['target_yield']
    scale = Fraction(target_yield, original_yield)
    recipe['yield'] = target_yield
    for ing in recipe['ingredients']:
        ing['quantity'] = ing['quantity'] * scale

    # Prepare equipment set
    equipment = set(recipe.get('equipment', []))

    # Prepare instructions list (will modify later)
    instructions = recipe.get('instructions', [])

    # Track applied choices and visited ingredients for cycle detection
    applied_choices = []
    visited = set()
    warnings = set()
    reasons = set()

    # Function to find best substitution for an ingredient
    def find_substitution(ingredient):
        candidates = []
        for idx, choice in enumerate(catalog):
            if choice['for'] == ingredient['id'] or choice['for'] == ingredient['name']:
                candidates.append((choice['priority'], idx, choice))
        if not candidates:
            return None
        # Sort by priority then catalog order
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2]

    # Process substitutions iteratively
    # We'll use a queue of ingredients to process
    queue = list(recipe['ingredients'])
    while queue:
        ing = queue.pop(0)
        # If already visited, cycle
        if ing['id'] in visited:
            reasons.add(f"substitution cycle involving {ing['name']}")
            continue
        visited.add(ing['id'])

        # Check if excluded
        if ing['name'] in request['excluded']:
            # Try to substitute
            choice = find_substitution(ing)
            if choice is None:
                reasons.add(f"no substitution for {ing['name']}")
                continue
            # Apply substitution
            applied_choices.append(choice['id'])
            # Apply quantity factor
            if 'quantity_factor' in choice['result']:
                ing['quantity'] = ing['quantity'] * Fraction(choice['result']['quantity_factor'])
            # Apply unit
            if 'unit' in choice['result']:
                ing['unit'] = choice['result']['unit']
            # Apply preparation
            if 'preparation' in choice['result']:
                ing['preparation'] = choice['result']['preparation']
            # Apply name
            if 'name' in choice['result']:
                ing['name'] = choice['result']['name']
            # Apply equipment changes
            if 'equipment_additions' in choice['result']:
                equipment.update(choice['result']['equipment_additions'])
            if 'equipment_removals' in choice['result']:
                equipment.difference_update(choice['result']['equipment_removals'])
            # Apply wording changes
            if 'wording_changes' in choice['result']:
                for change in choice['result']['wording_changes']:
                    old = change['old']
                    new = change['new']
                    for i, instr in enumerate(instructions):
                        if old in instr:
                            instructions[i] = instr.replace(old, new)
                            warnings.add(f"wording change: {old} -> {new}")
            # Add additional ingredients
            if 'additional_ingredients' in choice['result']:
                for add in choice['result']['additional_ingredients']:
                    new_ing = {
                        'id': add.get('id', f"additional_{len(recipe['ingredients'])}"),
                        'name': add['name'],
                        'quantity': Fraction(add['quantity']) * scale,
                        'unit': add.get('unit', ''),
                        'preparation': add.get('preparation', '')
                    }
                    recipe['ingredients'].append(new_ing)
                    queue.append(new_ing)
            # After substitution, check if new name is excluded
            if ing['name'] in request['excluded']:
                # Try to substitute again? But we already applied one; maybe need to continue
                # We'll re-add to queue to process again
                queue.append(ing)
        else:
            # Not excluded, but maybe still need to check if substitution is needed? No.
            pass

    # After processing all, check for any remaining excluded ingredients
    for ing in recipe['ingredients']:
        if ing['name'] in request['excluded']:
            # Try to find substitution if not already tried
            choice = find_substitution(ing)
            if choice is None:
                reasons.add(f"no substitution for {ing['name']}")
            else:
                # Apply substitution (similar code as above)
                applied_choices.append(choice['id'])
                if 'quantity_factor' in choice['result']:
                    ing['quantity'] = ing['quantity'] * Fraction(choice['result']['quantity_factor'])
                if 'unit' in choice['result']:
                    ing['unit'] = choice['result']['unit']
                if 'preparation' in choice['result']:
                    ing['preparation'] = choice['result']['preparation']
                if 'name' in choice['result']:
                    ing['name'] = choice['result']['name']
                if 'equipment_additions' in choice['result']:
                    equipment.update(choice['result']['equipment_additions'])
                if 'equipment_removals' in choice['result']:
                    equipment.difference_update(choice['result']['equipment_removals'])
                if 'wording_changes' in choice['result']:
                    for change in choice['result']['wording_changes']:
                        old = change['old']
                        new = change['new']
                        for i, instr in enumerate(instructions):
                            if old in instr:
                                instructions[i] = instr.replace(old, new)
                                warnings.add(f"wording change: {old} -> {new}")
                if 'additional_ingredients' in choice['result']:
                    for add in choice['result']['additional_ingredients']:
                        new_ing = {
                            'id': add.get('id', f"additional_{len(recipe['ingredients'])}"),
                            'name': add['name'],
                            'quantity': Fraction(add['quantity']) * scale,
                            'unit': add.get('unit', ''),
                            'preparation': add.get('preparation', '')
                        }
                        recipe['ingredients'].append(new_ing)
                        # Check if new ingredient is excluded
                        if new_ing['name'] in request['excluded']:
                            # Try to substitute it later? We'll handle in next loop
                            pass

    # Check equipment availability
    available = set(request['available_equipment'])
    for eq in equipment:
        if eq not in available:
            reasons.add(f"equipment {eq} unavailable")

    # If any reasons, impossible
    if reasons:
        result['possible'] = False
        result['recipe'] = None
        result['reasons'] = sorted(reasons)
        result['choices'] = applied_choices
        result['warnings'] = sorted(warnings)
        return result

    # Sort equipment
    recipe['equipment'] = sorted(equipment)
    # Update instructions
    recipe['instructions'] = instructions
    # Set recipe
    result['recipe'] = recipe
    result['choices'] = applied_choices
    result['warnings'] = sorted(warnings)
    return result

def print_original(recipe):
    return recipe['authored_text']
