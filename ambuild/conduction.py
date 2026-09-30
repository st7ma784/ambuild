"""Conduction through a structure's π system, from liminal (a separate project, in the
submodule external/liminal): conjugated domains, the Hückel gap and the network's
conductance, with tunnelling through sp3 atoms (liminal's tier C0).

As with ion maps (ambuild.ionmap), Ambuild runs `liminal conduct` on a checkpoint's
exported structure (docs/export.md) and reads the results file it writes (conduct.json,
format "liminal-conduction" version 1). Ambuild never imports liminal. See
docs/conduction.md.

The figures are relative, for ranking and constraining structures (conductances in g0, one
untwisted aromatic bond), not conductivities in S/m.

This module needs only the standard library (the web GUI and the campaign controller use
its metric names).
"""
import json
import logging
import shlex
import subprocess

from ambuild.ionmap import executable  # noqa: F401 (the same program, found the same way)

logger = logging.getLogger(__name__)

EVENT = "conduction_result"  # data: summarise(), plus the files, directory and exit code
FORMAT, VERSION = "liminal-conduction", 1
NAME_STEM = "conduction"  # conduction_<fileCount>/ in the run directory
RESULTS = "conduct.json"

# Metrics for sweeps and campaigns: el_<field>
FIELDS = {
    "gap": "π gap (eV, Hückel)",
    "conductance": "π conductance (g0, mean of axes)",
    "conductance_min": "π conductance, weakest axis (g0)",
    "conjugated_conductance": "π conductance without sp3 tunnelling (g0)",
    "tunnelling_share": "share of π conductance by sp3 tunnelling",
    "largest_domain_fraction": "largest conjugated domain (fraction of π sites)",
    "radical_domains": "radical π domains",
}
PREFIX = "el"
METRICS = ["{0}_{1}".format(PREFIX, f) for f in FIELDS]


def metricLabel(metric):
    return FIELDS.get(metric[len(PREFIX) + 1:], metric)


def metrics(summary):
    """{metric: value} from a run's latest conduction summary (None: no figures)"""
    return {"{0}_{1}".format(PREFIX, f): (summary or {}).get(f) for f in FIELDS}


def readResults(path):
    """conduct.json, checked to be the format this Ambuild reads"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise RuntimeError("{0}: not {1} version {2} (format {3!r}, version {4!r}); this Ambuild reads only that".format(
            path, FORMAT, VERSION, data.get("format"), data.get("version")))
    return data


def summarise(data):
    """The figures Ambuild records from conduct.json"""
    axes = data.get("axes") or {}
    keys = ("sites", "sp_sites", "nitrogen_sites", "sp3_bridges", "domains", "largest_domain",
            "largest_domain_fraction", "percolating_domains", "percolates", "open_shell_domains", "radical_domains",
            "homo", "lumo", "gap", "median_domain_gap", "conductance", "conductance_min", "conjugated_conductance",
            "tunnelling_share")
    out = {k: data.get(k) for k in keys}
    out.update({"tier": data.get("tier"), "liminal_version": data.get("liminal_version"),
                "parameters": data.get("parameters"),
                "axes": {a: (axes.get(a) or {}).get("conductance") for a in "xyz"}})
    return out


def runLiminal(command, structure, out, tSp3, sp3Decay, maxBridge, maxDense, log):
    """Run `liminal conduct`; returns its exit code"""
    args = command + ["conduct", structure, "--out", out, "--t-sp3", str(tSp3), "--sp3-decay", str(sp3Decay),
                      "--max-bridge", str(maxBridge), "--max-dense", str(maxDense)]
    logger.info("Running liminal: %s", " ".join(shlex.quote(a) for a in args))
    with open(log, "w") as f:
        return subprocess.run(args, stdout=f, stderr=subprocess.STDOUT).returncode
