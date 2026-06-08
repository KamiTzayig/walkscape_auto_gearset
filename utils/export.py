import gzip
import base64
import json
from models import GearSet

def export_gearset(gearset: GearSet) -> str:
    """
    Exports a GearSet to a Gzipped, Base64-encoded JSON string.
    Adapts new uppercase models to the legacy snake_case export format.
    """
    
    # Define the output order strictly matching the import requirements
    slots_map = [
        ("head", 0, lambda g: g.head),
        ("cape", 0, lambda g: g.cape),
        ("back", 0, lambda g: g.back),
        ("chest", 0, lambda g: g.chest),
        ("primary", 0, lambda g: g.primary),
        ("secondary", 0, lambda g: g.secondary),
        ("hands", 0, lambda g: g.hands),
        ("legs", 0, lambda g: g.legs),
        ("neck", 0, lambda g: g.neck),
        ("feet", 0, lambda g: g.feet),
        # Rings (Indices 0, 1 - safely accessed from list)
        ("ring", 0, lambda g: g.rings[0] if len(g.rings) > 0 else None),
        ("ring", 1, lambda g: g.rings[1] if len(g.rings) > 1 else None),
        # Tools (Indices 0-5 - safely accessed from list)
        ("tool", 0, lambda g: g.tools[0] if len(g.tools) > 0 else None),
        ("tool", 1, lambda g: g.tools[1] if len(g.tools) > 1 else None),
        ("tool", 2, lambda g: g.tools[2] if len(g.tools) > 2 else None),
        ("tool", 3, lambda g: g.tools[3] if len(g.tools) > 3 else None),
        ("tool", 4, lambda g: g.tools[4] if len(g.tools) > 4 else None),
        ("tool", 5, lambda g: g.tools[5] if len(g.tools) > 5 else None),
        ("pet", 0, lambda g: g.pet if g.pet else None),
        ("consumable", 0, lambda g: g.consumable if g.consumable else None),
        ("activityInput", 0, lambda g: g.inputs[0] if len(g.inputs) > 0 else None),
    ]

    json_entries = []

    # Suffixes to strip (Legacy support, just in case)
    SUFFIXES = ["_common", "_uncommon", "_rare", "_epic", "_legendary", "_ethereal"]

    for type_name, idx, getter in slots_map:
        item = getter(gearset)
        
        # Safely grab the UUID if it exists and isn't empty, otherwise fallback to the base ID
        id = None
        if item:
            if hasattr(item, 'uuid') and item.uuid:
                id = item.uuid
            elif hasattr(item, 'id') and item.id:
                id = item.id

        if id:
            # Default quality for standard equipment
            quality_val = "Normal"

            # Handle Fine Variants for Consumables
            if type_name == "consumable":
                if id.endswith("_fine"):
                    id = id.replace("_fine", "")
                    quality_val = "consumableFine"
                else:
                    quality_val = "Normal"

            # Handle Fine Variants for Activity Inputs
            elif type_name == "activityInput":
                if id.endswith("_fine"):
                    id = id.replace("_fine", "")
                    quality_val = "materialFine"
                else:
                    # Raw JSON expects `null` for normal materials
                    quality_val = None

            inner_data = {
                "id": id,
                "quality": quality_val, 
                "tag": None
            }
            
            # Double-serialization: The 'item' field must be a JSON string
            item_value_str = json.dumps(inner_data, separators=(',', ':'))
        else:
            item_value_str = "null"

        entry = {
            "type": type_name,
            "index": idx,
            "item": item_value_str, 
            "errors": []
        }
        json_entries.append(entry)

    final_obj = {"items": json_entries}
    
    # Serialize to standard JSON
    json_str = json.dumps(final_obj, separators=(',', ':'))
    
    # Compress (Gzip)
    compressed_data = gzip.compress(json_str.encode('utf-8'))
    
    # Encode (Base64)
    return base64.b64encode(compressed_data).decode('utf-8')