"""Profile Poreblazer: time per step, CPU time, peak memory and, optionally, gprof.

    python3 profile_poreblazer.py OUT.json [--repeats 1] [--gprof]

Builds benzene cells and runs Poreblazer ($POREBLAZER_EXE) directly on each, in three
series that separate the things that could drive its cost:

    box       20, 30 and 40 A cells at a fixed density
    atoms     a 30 A cell with 3, 12 and 48 blocks
    cubelet   the 30 A cell with grid spacings of 0.2 (Ambuild's default), 0.3 and 0.4 A

Each run records the wall time of Poreblazer's eight steps (from the time each
"Step N/8" line appears), user and system CPU time and peak memory from
/usr/bin/time -v, and the parsed results, so accuracy can be compared across grid
spacings. With --gprof, the executable must be built with -pg (see
poreblazer-flags.Dockerfile, OFLAGS="-O2 -pg") and the top of gprof's flat profile
is recorded too.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

from ambuild import ab_cell, ab_poreblazer

TESTS = os.environ.get("AMBUILD_TESTS_DIR", os.path.join(os.path.dirname(__file__), "..", "tests"))
PARAMS = os.path.join(TESTS, "params")
STEPS = {
    1: "initialise", 2: "lattice", 3: "helium_lattice", 4: "nitrogen_lattice",
    5: "helium_volume", 6: "surface_area", 7: "pore_distribution", 8: "limiting_diameter",
}


def buildCell(box, blocks, workdir):
    random.seed(11)
    cell = ab_cell.Cell([box] * 3, paramsDir=PARAMS, outputDir=workdir)
    cell.libraryAddFragment(os.path.join(TESTS, "blocks", "benzene.car"), fragmentType="A")
    cell.addBondType("A:a-A:a")
    added = cell.seed(blocks)
    xyz = os.path.join(workdir, "ambuild.xyz")
    cell.writeXyz(xyz)
    atoms = cell.numAtoms()
    cell.close()
    return xyz, atoms, added


def defaultsWithCubelet(cubelet, visualisation="grd"):
    """defaults.dat with a different grid spacing (and the .grd output, as when profiled)"""
    return ab_poreblazer.defaults_dat(cubelet_size=cubelet, visualisation=visualisation)


def runPoreblazer(exe, xyz, box, cubelet, rundir, gprof, visualisation="grd"):
    os.makedirs(rundir, exist_ok=True)
    os.replace(xyz, os.path.join(rundir, "ambuild.xyz"))
    inputDat = ab_poreblazer.write_input_dat("ambuild.xyz", box, box, box, directory=rundir)
    with open(os.path.join(rundir, "defaults.dat"), "w") as f:
        f.write(defaultsWithCubelet(cubelet, visualisation))
    with open(os.path.join(rundir, "UFF.atoms"), "w") as f:
        f.write(ab_poreblazer.UFF_ATOMS)

    timeFile = os.path.join(rundir, "time.txt")
    start = time.time()
    proc = subprocess.Popen(
        ["/usr/bin/time", "-v", "-o", timeFile, exe], cwd=rundir,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    proc.stdin.write(inputDat)
    proc.stdin.close()
    marks = {}
    with open(os.path.join(rundir, "poreblazer.log"), "w") as log:
        for line in proc.stdout:
            log.write(line)
            m = re.match(r"\s*Step (\d)/8", line)
            if m:
                marks[int(m.group(1))] = time.time()
    proc.wait()
    end = time.time()

    steps = {}
    ordered = sorted(marks)
    for i, step in enumerate(ordered):
        stop = marks[ordered[i + 1]] if i + 1 < len(ordered) else end
        steps[STEPS[step]] = stop - marks[step]
    with open(timeFile) as f:
        timing = f.read()

    def field(label):
        m = re.search(re.escape(label) + r":\s*([\d.:]+)", timing)
        return m.group(1) if m else None

    record = {
        "returncode": proc.returncode,
        "wall_seconds": end - start,
        "steps": steps,
        "user_seconds": float(field("User time (seconds)") or 0),
        "system_seconds": float(field("System time (seconds)") or 0),
        "max_rss_mb": int(field("Maximum resident set size (kbytes)") or 0) / 1024.0,
        "results": {k: v for k, v in ab_poreblazer.parse_output(rundir).items()
                    if not k.startswith("psd")},
    }
    gmon = os.path.join(rundir, "gmon.out")
    if gprof and os.path.isfile(gmon):
        out = subprocess.run(["gprof", "-b", "-p", exe, gmon], capture_output=True, text=True).stdout
        record["gprof_flat"] = [l for l in out.splitlines() if re.match(r"\s*\d", l)][:12]
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--gprof", action="store_true")
    parser.add_argument("--workdir", default="/tmp/profile_pb")
    args = parser.parse_args()
    exe = os.environ["POREBLAZER_EXE"]

    runs = [("box", b, round(6 * (b / 30.0) ** 3), 0.2) for b in (20, 30, 40)]
    runs += [("atoms", 30, n, 0.2) for n in (3, 12, 48)]
    runs += [("cubelet", 30, 6, c) for c in (0.2, 0.3, 0.4)]
    records = []
    for i, (series, box, blocks, cubelet) in enumerate(runs):
        for repeat in range(args.repeats):
            workdir = os.path.join(args.workdir, "%02d_%s_%d_%d_%g_%d" % (i, series, box, blocks, cubelet, repeat))
            xyz, atoms, added = buildCell(float(box), blocks, workdir)
            record = runPoreblazer(exe, xyz, float(box), cubelet, os.path.join(workdir, "pb"), args.gprof)
            record.update(series=series, box=box, blocks=added, atoms=atoms, cubelet=cubelet, repeat=repeat)
            records.append(record)
            top = max(record["steps"], key=record["steps"].get) if record["steps"] else "?"
            print("%-7s box %2d  %4d atoms  cubelet %.1f  %7.2f s wall  %6.1f MB  slowest %s (%.2f s)  SA %s" % (
                series, box, atoms, cubelet, record["wall_seconds"], record["max_rss_mb"], top,
                record["steps"].get(top, 0), record["results"]["surface_area_m2_g"]), flush=True)
            with open(args.out, "w") as f:
                json.dump(records, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
