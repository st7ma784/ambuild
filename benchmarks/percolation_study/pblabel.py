"""Line-by-line Python port of Poreblazer 3.0.5's clusteranalysis (percolation.f90),
exact periodic union-find labelling, and a search for the smallest lattice on which
they disagree. Grids are indexed grid[k][j][i] (i fastest, as in the Fortran scan)."""
import itertools
import json
import random
import sys


def neighbours(i, j, k, LX, LY, LZ):
    # the order of reveal_local / scenario_2: k-1, k+1, j-1, j+1, i-1, i+1 (periodic)
    return [
        (i, j, (k - 1) % LZ), (i, j, (k + 1) % LZ),
        (i, (j - 1) % LY, k), (i, (j + 1) % LY, k),
        ((i - 1) % LX, j, k), ((i + 1) % LX, j, k),
    ]


def poreblazer_labels(grid, trace=None):
    LZ, LY, LX = len(grid), len(grid[0]), len(grid[0][0])
    cluster = [[[0] * LX for _ in range(LY)] for _ in range(LZ)]
    trcl = [0] * (LX * LY * LZ + 2)
    nc = 0
    for k in range(LZ):
        for j in range(LY):
            for i in range(LX):
                if grid[k][j][i] != 1 or cluster[k][j][i] != 0:
                    continue
                nbs = [(a, b, c) for a, b, c in neighbours(i, j, k, LX, LY, LZ)
                       if grid[c][b][a] == 1 and cluster[c][b][a] != 0]
                if not nbs:  # scenario 1
                    nc += 1
                    trcl[nc] = nc
                    cluster[k][j][i] = nc
                    if trace is not None:
                        trace.append({"site": [i, j], "scenario": 1, "label": nc, "trcl": trcl[1:nc + 1]})
                    continue
                # scenario 2
                lowest = min(cluster[c][b][a] for a, b, c in nbs)
                trlowest = min(trcl[cluster[c][b][a]] for a, b, c in nbs)
                cluster[k][j][i] = lowest
                trcl[lowest] = trlowest
                for a, b, c in neighbours(i, j, k, LX, LY, LZ):
                    if cluster[c][b][a] != 0:
                        trcl[cluster[c][b][a]] = trlowest
                if trace is not None:
                    trace.append({"site": [i, j], "scenario": 2, "label": lowest,
                                  "neighbour_labels": sorted({cluster[c][b][a] for a, b, c in nbs}),
                                  "trlowest": trlowest, "trcl": trcl[1:nc + 1]})
    # relabel in scan order through trcl (one level)
    final = [[[0] * LX for _ in range(LY)] for _ in range(LZ)]
    newlabel = {}
    for k in range(LZ):
        for j in range(LY):
            for i in range(LX):
                if grid[k][j][i] == 0:
                    continue
                t = trcl[cluster[k][j][i]]
                if t not in newlabel:
                    newlabel[t] = len(newlabel) + 1
                final[k][j][i] = newlabel[t]
    return cluster, final, len(newlabel)


def exact_labels(grid):
    LZ, LY, LX = len(grid), len(grid[0]), len(grid[0][0])
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sites = [(i, j, k) for k in range(LZ) for j in range(LY) for i in range(LX) if grid[k][j][i] == 1]
    for s in sites:
        parent[s] = s
    for (i, j, k) in sites:
        for n in neighbours(i, j, k, LX, LY, LZ):
            if n in parent:
                a, b = find((i, j, k)), find(n)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    final = [[[0] * LX for _ in range(LY)] for _ in range(LZ)]
    newlabel = {}
    for (i, j, k) in sites:
        r = find((i, j, k))
        if r not in newlabel:
            newlabel[r] = len(newlabel) + 1
        final[k][j][i] = newlabel[r]
    return final, len(newlabel)


def search(nx, ny, tries, seed):
    """Smallest 2D (LZ = 1) lattice, padded with an empty border so nothing wraps,
    on which Poreblazer's labelling splits a connected cluster."""
    rng = random.Random(seed)
    best = None
    for _ in range(tries):
        p = rng.uniform(0.35, 0.7)
        inner = [[1 if rng.random() < p else 0 for _ in range(nx)] for _ in range(ny)]
        grid = [[[0] * (nx + 2)] + [[0] + row + [0] for row in inner] + [[0] * (nx + 2)]]
        _, _, nup = poreblazer_labels(grid)
        _, nex = exact_labels(grid)
        if nup != nex:
            n = sum(map(sum, inner))
            if best is None or n < best[0]:
                best = (n, grid)
    return best


def minimise(grid):
    """Remove occupied sites one at a time while the disagreement survives."""
    changed = True
    while changed:
        changed = False
        for j, i in itertools.product(range(len(grid[0])), range(len(grid[0][0]))):
            if grid[0][j][i] == 1:
                grid[0][j][i] = 0
                _, _, nup = poreblazer_labels(grid)
                _, nex = exact_labels(grid)
                if nup != nex:
                    changed = True
                else:
                    grid[0][j][i] = 1
    return grid


if __name__ == "__main__":
    if sys.argv[1] == "search":
        best = None
        for nx, ny in [(4, 4), (5, 4), (5, 5), (6, 5), (6, 6)]:
            found = search(nx, ny, 20000, 1)
            if found and (best is None or found[0] < best[0]):
                best = found
        grid = minimise(best[1])
        trace = []
        _, final, nup = poreblazer_labels(grid, trace)
        exact, nex = exact_labels(grid)
        json.dump({"grid": grid[0], "poreblazer": final[0], "exact": exact[0], "trace": trace,
                   "clusters_poreblazer": nup, "clusters_exact": nex}, sys.stdout)
    elif sys.argv[1] == "lattices":
        # random 3D lattices for the Fortran cross-check: L, then values
        rng = random.Random(int(sys.argv[2]))
        for t in range(int(sys.argv[3])):
            L = rng.choice([5, 8, 12])
            p = rng.uniform(0.2, 0.7)
            print(L, " ".join("1" if rng.random() < p else "0" for _ in range(L ** 3)))
    elif sys.argv[1] == "check":
        # compare with the Fortran labels: lines "L labels..."
        ok = bad = 0
        lat = open(sys.argv[2]).read().split("\n")
        lab = open(sys.argv[3]).read().split("\n")
        for a, b in zip(lat, lab):
            if not a.strip():
                continue
            v = a.split()
            L = int(v[0])
            flat = [int(x) for x in v[1:]]
            grid = [[[flat[i + L * j + L * L * k] for i in range(L)] for j in range(L)] for k in range(L)]
            _, final, _ = poreblazer_labels(grid)
            mine = [final[k][j][i] for k in range(L) for j in range(L) for i in range(L)]
            theirs = [int(x) for x in b.split()[1:]]
            if mine == theirs:
                ok += 1
            else:
                bad += 1
        print("identical labels:", ok, "differing:", bad)
