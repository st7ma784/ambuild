"""Save recipes in the web GUI (deploy/docker-compose.yml, profile web), with their
building blocks uploaded, so the New run and New sweep pages have some to start from:
the demo builds' recipe and every example shipped with Ambuild (ambuild.recipe.examples).
A recipe already saved as it is is not saved again.

    python seed_recipes.py        # AMBUILD_API_URL (default http://web:8000)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapi  # noqa: E402

from ambuild import ab_util, recipe  # noqa: E402


def uploaded(body):
    """The recipe with its files (paths) replaced by references to uploaded copies"""
    def swap(block):
        for key in ("car", "csv", "ambody"):
            if block.get(key) and not recipe.isRef(block[key]):
                block[key] = webapi.upload(block[key])

    for frag in body.get("fragments", []):
        swap(frag)
    for name, path in list((body.get("params") or {}).items()):
        if not recipe.isRef(path):
            body["params"][name] = webapi.upload(path)
    return body


def save(body):
    digest = recipe.recipeHash(body)
    saved = webapi.call("GET", "/api/recipes")["recipes"]
    if any(r["name"] == body["name"] and r["sha256"] == digest for r in saved):
        print("already saved:", body["name"])
        return
    row = webapi.call("POST", "/api/recipes", body)
    print("saved {0} v{1}: {2}".format(row["recipe_id"], row["version"], body["name"]))


def main():
    webapi.waitForWeb()
    save(webapi.demoRecipe(ab_util.blocksDir()))
    for name in recipe.examples():
        save(uploaded(recipe.example(name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
