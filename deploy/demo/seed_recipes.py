"""Save the demo recipe in the web GUI (deploy/docker-compose.yml, profile web), with its
building blocks uploaded, so the New run page has something to start from. Saving it
again when it is already saved does nothing.

    python seed_recipes.py        # AMBUILD_API_URL (default http://web:8000)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapi  # noqa: E402

from ambuild import ab_util, recipe  # noqa: E402


def main():
    webapi.waitForWeb()
    body = webapi.demoRecipe(ab_util.blocksDir())
    digest = recipe.recipeHash(body)
    saved = webapi.call("GET", "/api/recipes")["recipes"]
    if any(r["name"] == body["name"] and r["sha256"] == digest for r in saved):
        print("demo recipe already saved")
        return 0
    row = webapi.call("POST", "/api/recipes", body)
    print("saved demo recipe {0} v{1}".format(row["recipe_id"], row["version"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
