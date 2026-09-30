"""The recipe gallery: Ambuild's example recipes grouped by family (gallery.json), each
summarised from its recipe file: its blocks, joins, cell, how it builds and what it
measures. The web GUI shows it at /gallery. Standard library only.
"""
import json
import os

from ambuild import campaign as ab_campaign
from ambuild import recipe as ab_recipe

CATALOGUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gallery.json")


def catalogue():
    with open(CATALOGUE, encoding="utf-8") as f:
        return json.load(f)["families"]


def exampleBody(name):
    """An example recipe as shipped (file paths as they are, not resolved)"""
    with open(os.path.join(ab_recipe.EXAMPLES_DIR, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _stages(stages):
    for s in stages or []:
        if "repeat" in s:
            yield from _stages(s["stages"])
        else:
            yield s


def summarise(name):
    """What an example builds: for a gallery card"""
    body = exampleBody(name)
    stages = body.get("stages", [])
    ops = [s["op"] for s in _stages(stages)]
    passes = [s for s in stages if "repeat" in s]
    params = body.get("params") or {}
    paramSet = os.path.basename(os.path.dirname(next(iter(params.values())))) if params else "Ambuild's default"
    measures = []
    if "poreblazer" in ops:
        measures.append("pores (Poreblazer)")
    ionMaps = [s for s in _stages(stages) if s["op"] == "ion_map"]
    if ionMaps:
        ions = ionMaps[0].get("ions", ["Li+"])
        measures.append("ion maps: " + ", ".join([ions] if isinstance(ions, str) else ions))
    if "conduction" in ops:
        measures.append("π conduction (liminal)")
    return {
        "example": name,
        "name": body["name"],
        "description": body.get("description", ""),
        "blocks": [f.get("name") or f["type"] for f in body.get("fragments", [])],
        "joins": len(body.get("bond_types", [])),
        "box": body["cell"]["box"],
        "seeds": sum(s.get("count", 0) for s in stages if s.get("op") == "seed"),
        "passes": passes[0]["repeat"] if passes else 0,
        "operations": ab_recipe.countSteps(stages),
        "optimised": "optimise" in ops or "md_optimise" in ops,
        "params": paramSet,
        "measures": measures,
        "resources": body.get("resources") or {},
    }


def gallery():
    """[{family..., "recipes": [summaries], "campaigns": [{name, objective, constraints}]}]"""
    campaigns = ab_campaign.examples()
    out = []
    for family in catalogue():
        entry = dict(family)
        entry["recipes"] = [summarise(n) for n in family["recipes"]]
        entry["campaigns"] = [{"name": c, "objective": campaigns[c].get("objective"),
                               "constraints": campaigns[c].get("constraints", [])} for c in family.get("campaigns", [])]
        out.append(entry)
    return out
