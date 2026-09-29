"""Ion maps: where ions sit in a structure, and how hard it is for them to cross it, from
liminal (a separate project, in the submodule external/liminal).

Like Poreblazer, liminal is an external program: Ambuild runs `liminal map` on a
checkpoint's exported structure (docs/export.md) and reads the results files it writes
(map.json, format "liminal-map" version 1, and an energy grid as a Gaussian cube file).
Ambuild never imports liminal. See docs/ion-maps.md.

The program: LIMINAL_EXE, a command such as "liminal" or "python -m liminal"; by default
`liminal` on the PATH.

This module needs only the standard library (the web GUI and the campaign controller use
its metric names).
"""
import json
import logging
import os
import re
import shlex
import shutil
import statistics
import subprocess

logger = logging.getLogger(__name__)

EVENT = "ion_map_result"  # the event each ion's map records; data: summarise()
MAP_FORMAT, MAP_VERSION = "liminal-map", 1
NAME_STEM = "ion_map"  # ion_map_<fileCount>/ in the run directory

# Metrics for sweeps and campaigns, per ion: <prefix>_<field>
ION_PREFIXES = {"Li+": "li", "Na+": "na", "K+": "k"}
ION_FIELDS = {
    "site_energy": "lowest site energy (kcal/mol)",
    "escape_barrier": "escape barrier from the lowest site (kcal/mol)",
    "lowest_barrier": "lowest escape barrier of any site (kcal/mol)",
    "sites": "sites",
}
METRICS = ["{0}_{1}".format(p, f) for p in ION_PREFIXES.values() for f in ION_FIELDS]


def metricLabel(metric):
    prefix, _, fieldName = metric.partition("_")
    ion = {v: k for k, v in ION_PREFIXES.items()}.get(prefix, prefix)
    return "{0} {1}".format(ion, ION_FIELDS.get(fieldName, fieldName))


def metrics(ionMaps):
    """{metric: value} from {ion: summary}, the latest ion map of each ion"""
    values = {m: None for m in METRICS}
    for ion, summary in (ionMaps or {}).items():
        prefix = ION_PREFIXES.get(ion)
        if prefix and summary:
            for fieldName in ION_FIELDS:
                values["{0}_{1}".format(prefix, fieldName)] = summary.get(fieldName)
    return values


def executable(liminalExe=None):
    """The liminal command as a list, or None if there is none"""
    command = liminalExe or os.environ.get("LIMINAL_EXE")
    if command:
        if os.name == "nt":  # POSIX splitting would eat the backslashes of Windows paths
            return [part.strip('"') for part in shlex.split(command, posix=False)]
        return shlex.split(command)
    found = shutil.which("liminal")
    return [found] if found else None


def ionDirectoryName(ion):
    """A directory name for an ion: Li+ -> Li_plus, Mg2+ -> Mg2_plus"""
    return re.sub(r"[^A-Za-z0-9_]", "_", ion.replace("+", "_plus").replace("-", "_minus"))


def readMap(path):
    """map.json, checked to be the format this Ambuild reads"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != MAP_FORMAT or data.get("version") != MAP_VERSION:
        raise RuntimeError("{0}: not {1} version {2} (format {3!r}, version {4!r}); this Ambuild reads only that".format(
            path, MAP_FORMAT, MAP_VERSION, data.get("format"), data.get("version")))
    return data


def summarise(data):
    """The figures Ambuild records from a map: counts, the lowest site's energy and escape
    barrier, and the spread of barriers"""
    sites = data.get("sites") or []
    barriers = [s["barrier"] for s in sites if s.get("barrier") is not None]
    lowest = min(sites, key=lambda s: s["energy"]) if sites else None
    return {
        "ion": data["ion"],
        "tier": data.get("tier"),
        "liminal_version": data.get("liminal_version"),
        "sites": len(sites),
        "escaping_sites": len(barriers),
        "site_energy": lowest["energy"] if lowest else None,
        "escape_barrier": lowest["barrier"] if lowest else None,
        "lowest_barrier": min(barriers) if barriers else None,
        "median_barrier": statistics.median(barriers) if barriers else None,
        "paths": len(data.get("paths") or []),
        "grid": (data.get("grid") or {}).get("shape"),
        "spacing": (data.get("grid") or {}).get("spacing"),
    }


def runLiminal(command, structure, outDir, ion, spacing, cutoff, maxEnergy, maxPaths, log):
    """Run `liminal map` for one ion; returns its exit code"""
    args = command + ["map", structure, "--out", outDir, "--ion", ion, "--spacing", str(spacing),
                      "--cutoff", str(cutoff), "--max-energy", str(maxEnergy), "--max-paths", str(maxPaths)]
    logger.info("Running liminal: %s", " ".join(shlex.quote(a) for a in args))
    with open(log, "w") as f:
        return subprocess.run(args, stdout=f, stderr=subprocess.STDOUT).returncode
