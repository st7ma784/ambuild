#!/usr/bin/env python3
"""Write Ambuild parameter files (bond, angle, dihedral, improper and pair CSVs) for a set
of GAFF atom types, from a GAFF force field in OpenMM's ffxml form (openmmforcefields:
openmmforcefields/ffxml/amber/gaff/ffxml/gaff-1.81.xml, generated from AmberTools'
gaff-1.81.dat).

    python scripts/gaff_params.py gaff-1.81.xml OUTPUT_DIR ca ha c1 cp --alias=cp=ca

--alias=T=S: for terms GAFF lacks for type T, use those of S (noted in the row's comment).

Ambuild's units and forms (ambuild/hoomd4.py) and the conversions from OpenMM's:
    bonds      V = k/2 (r - r0)^2          k [kcal/mol/A^2] = k_openmm [kJ/mol/nm^2] / 418.4,
                                           r0 [A] = 10 x length [nm]
    angles     V = k/2 (theta - t0)^2      k [kcal/mol/rad^2] = k_openmm / 4.184, t0 in degrees
    dihedrals  V = k/2 (1 + d cos(n phi))  k = 2 x k_openmm / 4.184, d = cos(phase) (phase 0 or pi)
    pairs      Lennard-Jones               sigma [A] = 10 x sigma [nm], epsilon [kcal/mol] =
                                           epsilon [kJ/mol] / 4.184, Lorentz-Berthelot for unlike pairs
Angles and dihedrals are looked up by their exact type string, so both orders are
written. GAFF's impropers are periodic, Ambuild's harmonic: none are written (rigid
blocks and the ring dihedrals keep rings planar).
"""
import itertools
import math
import os
import sys
import xml.etree.ElementTree as ET

KJ = 4.184


def _classes(el, n):
    return [el.get("class%d" % i) or "X" for i in range(1, n + 1)]


def load(path):
    root = ET.parse(path).getroot()
    source = root.find("Info/Source")
    info = "{0} ({1} {2})".format(source.text, source.get("sourcePackage"), source.get("sourcePackageVersion")) \
        if source is not None else os.path.basename(path)
    bonds = {tuple(_classes(b, 2)): b for b in root.iter("Bond")}
    angles = {tuple(_classes(a, 3)): a for a in root.iter("Angle")}
    propers = [(tuple(_classes(t, 4)), t) for t in root.iter("Proper")]
    lj = {a.get("class"): a for f in root.iter("NonbondedForce") for a in f if a.get("class")}
    return info, bonds, angles, propers, lj


def fmt(x):
    return "{0:.6g}".format(x)


def write(path, header, rows):
    with open(path, "w", newline="\n") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(",".join(row) + "\n")


def main(xmlPath, outDir, types, aliases=None):
    """aliases: {type: stand-in} for terms GAFF lacks, e.g. {"cp": "ca"}: GAFF's cp (an
    aromatic carbon bonded to another ring) has no terms with hydrogen or an alkyne, where it
    behaves as ca. The exact types are always tried first; a stand-in is noted in the row."""
    aliases = aliases or {}
    info, bonds, angles, propers, lj = load(xmlPath)
    note = "GAFF 1.81, from " + info
    os.makedirs(outDir, exist_ok=True)

    def lookup(q, find):
        """(element, the types it was found for) for types q, trying q itself first, then
        the stand-ins with the fewest substitutions"""
        options = [(t, aliases[t]) if t in aliases else (t,) for t in q]
        candidates = sorted(itertools.product(*options), key=lambda c: sum(a != b for a, b in zip(c, q)))
        for cand in candidates:
            el = find(cand)
            if el is not None:
                return el, cand
        return None, None

    def standIn(q, used):
        swaps = sorted({"{0} as {1}".format(a, b) for a, b in zip(q, used) if a != b})
        return " ({0})".format(", ".join(swaps)) if swaps else ""

    def findBond(q):
        return bonds[q] if q in bonds else bonds.get(q[::-1])

    def findAngle(q):
        return angles[q] if q in angles else angles.get(q[::-1])

    def findProper(q):
        """The most specific GAFF torsion for types q (either direction), as Amber matches"""
        best = None
        for key, el in propers:
            for cand in (q, q[::-1]):
                if all(k in ("X", t) for k, t in zip(key, cand)):
                    score = sum(k != "X" for k in key)
                    if best is None or score > best[0]:
                        best = (score, el)
        return best[1] if best else None

    rows = []
    for q in itertools.combinations_with_replacement(types, 2):
        el, used = lookup(q, findBond)
        if el is not None:
            rows.append([q[0], q[1], fmt(float(el.get("k")) / (100 * KJ)), fmt(10 * float(el.get("length"))),
                         '"{0} {1}{2}"'.format(note, "-".join(used), standIn(q, used))])
    write(os.path.join(outDir, "bond_params.csv"), "A,B,k,r0,comments", rows)

    rows = []
    for q in itertools.product(types, repeat=3):
        el, used = lookup(q, findAngle)
        if el is not None:
            rows.append(["-".join(q), fmt(float(el.get("k")) / KJ), fmt(math.degrees(float(el.get("angle")))),
                         '"{0} {1}{2}"'.format(note, "-".join(used), standIn(q, used))])
    write(os.path.join(outDir, "angle_params.csv"), "angle,k,t0,comments", rows)

    rows = []
    for q in itertools.product(types, repeat=4):
        el, used = lookup(q, findProper)
        if el is None:
            continue
        terms = [i for i in range(1, 5) if el.get("k%d" % i) is not None]
        if len(terms) != 1:
            raise SystemExit("{0}: {1} Fourier terms; Ambuild's dihedrals take one".format("-".join(q), len(terms)))
        i = terms[0]
        phase = float(el.get("phase%d" % i))
        if not (math.isclose(phase, 0.0, abs_tol=1e-9) or math.isclose(phase, math.pi, abs_tol=1e-9)):
            raise SystemExit("{0}: phase {1} is not 0 or pi".format("-".join(q), phase))
        rows.append(["-".join(q), fmt(2 * float(el.get("k%d" % i)) / KJ), fmt(round(math.cos(phase))),
                     el.get("periodicity%d" % i),
                     '"{0} {1}{2}"'.format(note, "-".join(_classes(el, 4)), standIn(q, used))])
    write(os.path.join(outDir, "dihedral_params.csv"), "dihedral,k,d,n,comments", rows)

    write(os.path.join(outDir, "improper_params.csv"), "improper,k,chi,comments", [])

    rows = []
    for a, b in itertools.combinations_with_replacement(types, 2):
        pa, pb = lj.get(a, lj.get(aliases.get(a))), lj.get(b, lj.get(aliases.get(b)))
        eps = math.sqrt(float(pa.get("epsilon")) * float(pb.get("epsilon"))) / KJ
        sigma = 10 * (float(pa.get("sigma")) + float(pb.get("sigma"))) / 2
        rows.append([a, b, fmt(eps), fmt(sigma), '"{0}; Lorentz-Berthelot"'.format(note)])
    write(os.path.join(outDir, "pair_params.csv"), "atom1,atom2,epsilon,sigma,comments", rows)
    print("wrote", outDir, "for", ", ".join(types), "with stand-ins" if aliases else "", aliases or "")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--alias=")]
    aliases = dict(a[len("--alias="):].split("=", 1) for a in sys.argv[1:] if a.startswith("--alias="))
    if len(args) < 3:
        raise SystemExit(__doc__)
    main(args[0], args[1], args[2:], aliases)
