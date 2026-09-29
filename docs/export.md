# Structure export (spec)

Status: proposed. It isn't built yet; milestones E1–E4 are at the end.

Ambuild's periodic boxes are the input to everything downstream: DFT, other MD codes,
visualisers and the planned ion-intercalation work (`TODO.md`). Today a checkpoint writes an
extended XYZ file (`step_N.xyz`: lattice, element, wrapped position, fragment, block) and a
pickle. Only the pickle holds the rest (force-field types, charges, bonds, free end
groups), and a pickle can be read only by Ambuild's own code. Nothing outside Ambuild
should read one, and the web GUI never unpickles anything.

This spec defines an export that carries everything a downstream code needs, in standard
files plus one documented JSON file.

## Goals

- **Complete:** each checkpoint exports atoms, the cell, force-field types, charges, bonds
  (including across the periodic boundary), block membership and free end groups.
- **Standard files:** extended XYZ, CIF, POSCAR, a CP2K `&SUBSYS` section and a LAMMPS data
  file, each readable by the usual tools (ASE, pymatgen, CP2K, LAMMPS).
- **No pickles across the boundary:** exports are written at build time as plain files. Every
  other format is derived from those files alone, so the web GUI and other tools can produce
  them without Ambuild's objects.
- **Traceable:** every export names its run, step, recipe hash, Ambuild version and
  parameter files, with a sha256 per file.
- **Deterministic:** the same checkpoint always gives byte-identical files.

## Non-goals: the boundary with the DFT spin-out

Ambuild exports boxes. The separate DFT project, [liminal](https://github.com/st7ma784/liminal)
(the submodule `external/liminal`; like Poreblazer and HOOMD-blue, not part of Ambuild), does
everything after that. Its `liminal.boxes` already reads export version 1 as specified here.
It handles: finding ion sites, cutting
clusters, capping them, and running calculations. Neither side imports the other's code.
The DFT side reads the export files (extended XYZ through ASE, plus the topology JSON by its
published schema). How results come back into Ambuild, as campaign metrics, gets a spec of
its own later.

## What each checkpoint writes (export format version 1)

`Cell.dump()` already writes `step_N.pkl.gz` and `step_N.xyz`. With this change it writes:

### `step_N.xyz`: extended XYZ, now with types and charges

```
488
Lattice="36.0 0.0 0.0 0.0 36.0 0.0 0.0 0.0 36.0" Properties=species:S:1:pos:R:3:type:S:1:charge:R:1:fragment:S:1:block:I:1 pbc="T T T" step=7 run_id="..." recipe_sha256="..." ambuild_version="2.0.1" export_version=1
C 12.345678 1.234567 30.000001 cp 0.000 A 0
...
```

- **Positions** are wrapped into [0, L) and given in Å.
- **Atom order:** block by block, and within each block in the block's own atom order. The
  topology file relies on this order.
- **New columns:** `type` (the force-field type, e.g. `ca`, `c1`, `ha`) and `charge` (e).
  Existing readers are unaffected: the web viewer maps columns by name from `Properties`.
- **Header keys:** `run_id`, `recipe_sha256`, `ambuild_version` and `export_version` are
  new. Values with spaces are quoted.

### `step_N.topology.json`: what XYZ can't hold

```json
{
  "format": "ambuild-topology", "version": 1,
  "structure": "step_7.xyz", "structure_sha256": "…", "atoms": 488,
  "run_id": "…", "step": 7,
  "params": {"bond": {"file": "bond_params.csv", "sha256": "…"}, "angle": {…}, "dihedral": {…}, "pair": {…}},
  "blocks": [{"id": 0, "start": 0, "end": 12, "fragments": ["A"]}, …],
  "bonds": [[0, 1, [0, 0, 0]], [5, 131, [1, 0, 0]], …],
  "free_end_groups": [{"atom": 4, "cap": 10, "type": "a", "block": 0}, …]
}
```

- **Blocks:** `blocks[].start`/`end` are the half-open index range of each block's atoms in
  the XYZ order. Together they cover every atom exactly once.
- **Bonds:** each bond is `[i, j, image]` with `i < j`. `image` is the lattice shift that
  gives the short bond vector from the wrapped positions: `pos[j] + image·L − pos[i]`. The
  list holds both kinds of bond, within blocks and between them, sorted.
- **Free end groups:** end groups still unbonded when the checkpoint was written. For each:
  its atom, its cap atom (the atom that would be removed on bonding, usually an H), the end
  group's type and its block. The DFT side uses these for capping and for choosing sites.
- **Schema:** `ambuild/schemas/topology-v1.json` (JSON Schema). A reader must check
  `format` and `version`.

Both files are recorded as run artifacts: the XYZ as `structure`, as now, and the new
file as `topology`. `ambuild-upload` uploads them with the rest of the run.

## Deriving the other formats

`ambuild/export.py` (standard library only, so the web GUI and the DFT side can use it
without NumPy) reads `step_N.xyz` and `step_N.topology.json` and writes:

| Format | File | Contents | Needs topology |
| --- | --- | --- | --- |
| `extxyz` | `structure.extxyz` | the checkpoint's XYZ file as it is | no |
| `cif` | `structure.cif` | P1 cell, fractional coordinates, `_atom_site_type_symbol`, and the force-field type as the atom label's suffix | no |
| `poscar` | `POSCAR` | VASP 5, species grouped (which reorders the atoms; the manifest records the mapping) | no |
| `cp2k` | `subsys.inc` | `&SUBSYS` with `&CELL` (ABC, PERIODIC XYZ) and `&COORD`, one `&KIND` per element; include it in a CP2K input with `@INCLUDE` | no |
| `lammps` | `structure.data` | `atom_style full`: masses, types, charges, bonds with image flags | yes |

- **Bundle:** an export is a directory (or zip) holding the chosen files plus
  `manifest.json`. The manifest holds `export_version`, the run id, step, the provenance
  above, each file's sha256, and the POSCAR atom order when `poscar` is included.
- **Older runs** (no topology file, no type or charge columns): export offers the formats
  that need no topology and says what is missing. `lammps` refuses with a clear message.
- **Regenerating locally:** the command line can rebuild the files from a pickle
  (`--from-pickle`, trusted local use only).

## Where you can export

- **Command line:**
  ```
  python -m ambuild.export RUN_DIR [--step N|last] [--format cif,poscar,cp2k,lammps] [--out DIR|FILE.zip] [--from-pickle]
  ```
- **Web API:** `GET /api/runs/{id}/export?step=N&format=cif,cp2k` returns a zip built from
  the uploaded files (never from pickles). A 404 means no such step; a 409 means a format
  needs topology that the run doesn't have.
- **Run page:** an Export menu on each checkpoint in the structure viewer.
- **Agent skill:** `ambuild_api.py export RUN_ID --step N --format …`, so an assistant can
  hand a box to the DFT workflow.

## Tests

**Unit tests** (`tests/testExport.py`, and `services/web/tests` for the API):

| # | Checks |
| --- | --- |
| 1 | The enriched XYZ: column order from `Properties`; each atom's type and charge equal its block's; every position in [0, L); the header's provenance equals `run.json`. |
| 2 | The topology: the bond count equals the cell's bonds (within and between blocks); every bond's minimum-image length is within the build's bond margin of r0 for its types (zipped bonds can be that far off until optimised), and within 0.05 Å after a HOOMD-blue optimisation; each bond's `image` reproduces its short vector; the free end groups equal the cell's free end groups (count, atoms, caps); block ranges cover every atom exactly once. |
| 3 | Determinism: exporting the same checkpoint twice gives byte-identical files and manifest hashes. |
| 4 | Converter golden files on a small fixture: two benzene blocks bonded across the boundary, plus one free end group. Covers CIF fractional coordinates, POSCAR grouping and the recorded order, CP2K atom and cell lines, and LAMMPS header counts, bonds and image flags. |
| 5 | Independent readers (CI's image job, which has ASE and pymatgen): each format read back gives the same cell, the same species in the documented order, and positions within 1e-5 Å (modulo the lattice). |
| 6 | Older runs: an XYZ file without types and topology still converts to extxyz, CIF, POSCAR and CP2K, with a warning; LAMMPS refuses clearly. |
| 7 | Scale: 20,000 atoms export in under 5 s. |
| 8 | The schema: every topology file written in the tests validates against `topology-v1.json`, and a file with a different `version` is refused. |

**Integration tests:**

| # | Checks |
| --- | --- |
| 9 | `li_ion_carbon` after optimisation: read back through ASE, the topology's bonds include every C≡C (1.203 Å) and ring–alkyne (1.44 Å) bond `testLiIonCarbon.py` measures, and nothing longer than 1.8 Å. |
| 10 | Upload and web: the topology artifact is uploaded and listed. The export zip's manifest hashes match its files. The web API never fetches a pickle for an export (checked with the pickles removed from storage). |
| 11 | CP2K smoke test (manual workflow, not on every push): a CP2K container runs a GFN1-xTB single point on an exported `benzene_network` cell, and the SCF converges. |
| 12 | The agent skill's helper: `export` writes a bundle that `manifest.json` verifies. |

## Milestones

| | Delivers | Done when |
| --- | --- | --- |
| E1 | Enriched XYZ and topology JSON at every checkpoint; the schema; uploaded as artifacts | tests 1–3, 8, 9 pass; a new run's files validate |
| E2 | `ambuild/export.py` converters and the command line, with `--from-pickle` | tests 4–7 pass |
| E3 | Web API, Export menu, skill command | tests 10 and 12 pass |
| E4 | CP2K smoke test | test 11 passes on demand |
