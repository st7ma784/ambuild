#!/usr/bin/env python3
"""Write a tetraphenyl node block, X(C6H4)4 linked at its four para carbons, for Ambuild
(tests/blocks/<name>.car and .csv).

    python scripts/make_tetraphenyl.py C tests/blocks/tetraphenylmethane
    python scripts/make_tetraphenyl.py Si tests/blocks/tetraphenylsilane

The four phenyl arms are related by S4 about z, the site symmetry of tetraphenylmethane and
tetraphenylsilane in their crystals. Rings are regular (C-C 1.397 A, C-H 1.084 A, as the
benzene blocks); the centre-ipso bond is GAFF's c3-ca r0 (1.5156 A) for carbon and the
measured Si-C(aryl) 1.87 A for silicon. The propeller twist is the one, in 0.5 degree steps,
that keeps ortho hydrogens on different arms furthest apart.

Typing: the centre c3 (carbon) or si (silicon, params/gaff_si); ipso and para carbons cp;
the rest ca and ha. End group t at each para carbon, capped by its hydrogen.
"""
import math
import sys

import numpy as np

CC, CH = 1.397, 1.084
CENTRE = {"C": (1.5156, "c3", "Tetraphenylmethane", "GAFF c3-ca r0"),
          "Si": (1.87, "si", "Tetraphenylsilane", "measured Si-C(aryl)")}
S4 = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, -1]], float)
ARM = np.array([1, 1, 1]) / np.sqrt(3)


def arm(theta, ipsoLength):
    ref = np.array([0, 0, 1.0]) - ARM * ARM[2]
    ref /= np.linalg.norm(ref)
    u = np.cos(theta) * ref + np.sin(theta) * np.cross(ARM, ref)
    ipso = ipsoLength * ARM
    centre = ipso + CC * ARM
    angles = [180, 120, 60, 0, -60, -120]  # ipso, ortho, meta, para, meta, ortho
    ring = [centre + CC * (np.cos(np.radians(a)) * ARM + np.sin(np.radians(a)) * u) for a in angles]
    hs = [centre + (CC + CH) * (np.cos(np.radians(a)) * ARM + np.sin(np.radians(a)) * u) for a in angles[1:]]
    return ring, hs


def build(theta, ipsoLength):
    carbons, hydrogens = [np.zeros(3)], []
    ring, hs = arm(theta, ipsoLength)
    m = np.eye(3)
    for _ in range(4):
        carbons += [m @ r for r in ring]
        hydrogens += [m @ h for h in hs]
        m = S4 @ m
    return np.array(carbons), np.array(hydrogens)


def closestHH(theta, ipsoLength):
    _, h = build(theta, ipsoLength)
    return min(np.linalg.norm(h[i] - h[j]) for i in range(20) for j in range(20) if i // 5 != j // 5)


def main(element, stem):
    ipsoLength, centreType, name, source = CENTRE[element]
    best, twist = max((closestHH(math.radians(t), ipsoLength), t) for t in np.arange(0, 180, 0.5))
    c, h = build(math.radians(twist), ipsoLength)
    centreText = "GAFF c3 centre" if element == "C" else "{0} centre (params/gaff_si: UFF Si)".format(centreType)
    bondText = "{0:g}".format(ipsoLength) if element == "C" else "{0:g} ({1})".format(ipsoLength, source)
    lines = ["!BIOSYM archive 3", "PBC=OFF",
             "{0} {1}(C6H4)4, linked at the four para carbons; S4 propeller (twist {2:.1f} deg from the arm-S4 axis "
             "plane); {3}, cp ipso/para, ca, ha; {1}-C(ipso) {4}, ring 1.397, C-H 1.084 A".format(
                 name, element, twist, centreText, bondText),
             "!DATE Tue Sep 29 20:00:00 2026"]

    def row(label, x, t, el):
        return "%-5s %15.9f %15.9f %15.9f XXXX 1      %-2s      %s   0.000" % (label, x[0], x[1], x[2], t, el)

    lines.append(row("{0}1".format(element.upper() if element == "C" else element.upper()[0] + element[1:].lower()),
                     c[0], centreType, element))
    for i in range(1, 25):
        lines.append(row("C%d" % (i + 1 if element == "C" else i), c[i], "cp" if (i - 1) % 6 in (0, 3) else "ca", "C"))
    for i in range(20):
        lines.append(row("H%d" % (i + 1), h[i], "ha", "H"))
    lines += ["end", "end"]
    with open(stem + ".car", "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    csv = ["Type,EndGroup,CapAtom,Dihedral,DelAtom"]
    for k in range(4):
        para, meta, paraH = 1 + 6 * k + 3, 1 + 6 * k + 2, 25 + 5 * k + 2
        csv.append("t,%d,%d,%d,-1" % (para, paraH, meta))
    with open(stem + ".csv", "w", newline="\n") as f:
        f.write("\n".join(csv) + "\n")
    print("{0}: twist {1:.1f} deg, closest H...H between arms {2:.3f} A".format(stem, twist, best))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in CENTRE:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
