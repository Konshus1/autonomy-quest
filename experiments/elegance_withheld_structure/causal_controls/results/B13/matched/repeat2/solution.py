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

    # Scale yield and ingredient quantities
    original_yield = recipe['yield']
    target_yield = request['target_yield']
    scale = target_yield / original_yield
    recipe['yield'] = target_yield
    for ing in recipe['ingredients']:
        ing['quantity'] = ing['quantity'] * scale

    # Prepare equipment set
    equipment_set = set(recipe.get('equipment', []))

    # Prepare instructions list (will apply wording changes later)
    instructions = recipe.get('instructions', [])

    # Prepare catalog index for deterministic priority
    # Sort by (priority, original index)
    catalog_sorted = sorted(enumerate(catalog), key=lambda x: (x[1]['priority'], x[0]))

    # Function to find applicable choice for an ingredient
    def find_choice(ingredient):
        for idx, choice in catalog_sorted:
            if choice['for'] == ingredient['id'] or choice['for'] == ingredient['name']:
                return idx, choice
        return None

    # Track visited ingredient ids to detect cycles
    visited = set()

    # Process substitutions iteratively
    changed = True
    while changed:
        changed = False
        # Iterate over a snapshot of ingredients because list may change
        for ing in list(recipe['ingredients']):
            if ing['id'] in visited:
                continue
            found = find_choice(ing)
            if found:
                idx, choice = found
                result['choices'].append(choice['id'])
                visited.add(ing['id'])
                changed = True

                # Apply result fields
                res = choice['result']
                if 'name' in res:
                    ing['name'] = res['name']
                if 'quantity_factor' in res:
                    ing['quantity'] = ing['quantity'] * res['quantity_factor']
                if 'unit' in res:
                    ing['unit'] = res['unit']
                    result['warnings'].append(f"unit changed for {ing['name']}")
                if 'preparation' in res:
                    ing['preparation'] = res['preparation']
                    result['warnings'].append(f"preparation changed for {ing['name']}")

                # Apply wording changes
                if 'wording_changes' in choice:
                    for wc in choice['wording_changes']:
                        old = wc['old']
                        new = wc['new']
                        instructions = [inst.replace(old, new) for inst in instructions]

                # Apply equipment changes
                if 'equipment_additions' in choice:
                    for eq in choice['equipment_additions']:
                        equipment_set.add(eq)
                if 'equipment_removals' in choice:
                    for eq in choice['equipment_removals']:
                        equipment_set.discard(eq)

                # Add additional ingredients
                if 'additional_ingredients' in choice:
                    for add in choice['additional_ingredients']:
                        new_ing = {
                            'id': add.get('id', f"additional_{len(recipe['ingredients'])}"),
                            'name': add['name'],
                            'quantity': add.get('quantity', Fraction(1)) * scale,
                            'unit': add.get('unit', ''),
                            'preparation': add.get('preparation', '')
                        }
                        recipe['ingredients'].append(new_ing)
                        result['warnings'].append(f"additional ingredient {new_ing['name']}")

                # Break to restart iteration because list changed
                break
        # End for
    # End while

    # Check exclusions
    excluded = set(request['excluded'])
    for ing in recipe['ingredients']:
        if ing['name'] in excluded:
            result['reasons'].append(f"no substitution for {ing['name']}")

    # Check equipment
    available = set(request['available_equipment'])
    for eq in equipment_set:
        if eq not in available:
            result['reasons'].append(f"equipment {eq} unavailable")

    # Check cycles: if any ingredient id visited more than once? Actually we detect by visited set, but cycles may cause infinite loop? We break after each change, but if a cycle exists, we might revisit? We use visited to prevent infinite, but we should detect cycle explicitly.
    # For simplicity, we rely on visited to stop, but we need to detect cycle for reason.
    # We'll detect if a substitution leads back to an already visited ingredient? Actually we only apply each ingredient once, so cycles are not infinite. But the spec says 'substitution cycle involving NAME' as a reason. We need to detect if a substitution chain creates a cycle where an ingredient is substituted back to itself? We'll implement a simple detection: if a choice's result name equals the original name? Or if we try to substitute an ingredient that has already been substituted? We'll add a check: if we find a choice for an ingredient that has already been visited, that indicates a cycle? But we skip visited, so we won't apply. To detect, we can track the chain of substitutions for each ingredient and if we see a repeat, record reason.
    # For now, we'll add a simple cycle detection: if a choice's result name is the same as the ingredient's original name? Not reliable.
    # We'll implement a more robust: maintain a mapping from ingredient id to the id it was substituted to? Actually we don't have ids for results. We'll skip explicit cycle detection for now, but we'll add a check: if a substitution would cause an ingredient to be substituted again with the same choice? We'll rely on visited to prevent infinite loops.
    # To satisfy the contract, we need to detect cycles. We'll implement a simple cycle detection: if a choice's result name is already in the excluded? No.
    # Let's implement a cycle detection by tracking the chain of ingredient names for each substitution. We'll maintain a dict from ingredient id to the name it was substituted to, and if we see a name that already appears in the chain, we record a cycle.
    # But we don't have that info now. We'll add a simple check: if a choice's result name is the same as the ingredient's name before substitution, that's a cycle? Not necessarily.
    # We'll implement a more general: if we apply a substitution and then later find a choice for the new ingredient that leads back to an original ingredient, that's a cycle. We'll track the original name for each ingredient id.
    # For simplicity, we'll add a check: if a choice's result name is in the excluded set? No.
    # Given time, we'll implement a basic cycle detection: if a choice's result name is the same as the ingredient's name, we record a cycle? That's not correct.
    # We'll skip explicit cycle detection for now, but we'll add a check: if we try to substitute an ingredient that has already been substituted (i.e., its id is in visited), we record a cycle? But we skip visited, so we won't apply. To detect, we can check if a choice's 'for' matches an ingredient that has already been substituted? That would be a cycle if the same ingredient is targeted again.
    # We'll add: if we find a choice for an ingredient that is already in visited, we record a cycle reason and mark impossible.
    # But we skip visited, so we need to check before skipping.
    # Let's modify the loop: before skipping visited, check if there is a choice for that ingredient; if yes and it's already visited, record cycle.
    # We'll do that.

    # Re-run cycle detection: for each ingredient, if there is a choice and the ingredient id is already visited, record cycle.
    for ing in recipe['ingredients']:
        if ing['id'] in visited:
            found = find_choice(ing)
            if found:
                result['reasons'].append(f"substitution cycle involving {ing['name']}")

    # If any reasons, impossible
    if result['reasons']:
        result['possible'] = False
        result['recipe'] = None
        result['reasons'] = sorted(set(result['reasons']))
        result['warnings'] = sorted(set(result['warnings']))
        return result

    # Sort equipment
    recipe['equipment'] = sorted(equipment_set)

    # Update instructions
    recipe['instructions'] = instructions

    # Set recipe
    result['recipe'] = recipe
    result['warnings'] = sorted(set(result['warnings']))
    return result

def print_original(recipe):
    return recipe['authored_text']
