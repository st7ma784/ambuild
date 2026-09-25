import logging
import os
import re
from ambuild.ab_util import run_command


NAME_STEM = "poreblazer"

UFF_ATOMS = """29
C       3.431   52.8    12.0
O       3.118   30.2    16.0
H       2.571   22.14   1.0
N    3.261    34.70    14.007
F    2.997    25.14    18.998
Na    2.658    15.09    22.99
Mg    2.691    55.82    24.305
Al    4.008    253.94    26.982
Si    3.826    202.15    28.085
P    3.695    153.37    30.974
S    3.595    137.78    32.06
Cl    3.516    114.15    35.45
K    3.396    17.60    39.098
Ca    3.028    119.68    40.078
Sc    2.936    9.55    44.956
Ti    2.829    8.55    47.867
V    2.801    8.05    50.942
Cr    2.693    7.54    51.996
Mn    2.638    6.54    54.938
Fe    2.594    6.54    55.845
Co    2.559    7.04    58.933
Ni    2.525    7.54    58.693
Cu    3.114    2.51    63.546
Zn      2.462   62.38   65.39
Zr    2.783    34.70    91.224
Mo    2.719    28.16    95.96
Re    2.632    33.19    186.21
Br    4.189    126.31   79.904
Pd    2.899    0.048    106.42

! name of framework atom, diameter (LJ sigma) in A, epsilon in K, mol weight

"""

DEFAULTS_DAT = """UFF.atoms
2.58, 10.22, 3.314, 298
12.8, 500
0.2
20.0, 0.25
21908391
2

! Default forcefield: UFF
! Helium atom sigma (A), helium atom epsilon (K), nitrogen atom sigma (A), temperature (K)
! Cutoff distance (A), accessible surface area coefficient (1.0 for hard sphere
! surface, 1.122 for potential minimum surface), number of trials for surface area
! calculation
! 0.2: Cubelet size (A)
! Largest anticipated pore diameter (A), size of the bin for PSD (A)
! Random number seed

! Do not change these values unless you know what you are doing
"""

logger = logging.getLogger()


def write_input_dat(xyzin, A, B, C, directory=None):
    d = {"xyzin": xyzin, "A": A, "B": B, "C": C}
    input_dat = """{xyzin}
{A:.4}  {B:.4}  {C:.4}
90.00  90.00 90.00
""".format(
        **d
    )
    with open(os.path.join(directory or os.getcwd(), "input.dat"), "w") as w:
        w.write(input_dat)
    return input_dat


def run_poreblazer(poreblazer_exe, input_dat, directory=None):
    logger.info("Running poreblazer using executable: {}".format(poreblazer_exe))
    directory = directory or os.getcwd()
    with open(os.path.join(directory, "defaults.dat"), "w") as w:
        w.write(DEFAULTS_DAT)
    with open(os.path.join(directory, "UFF.atoms"), "w") as w:
        w.write(UFF_ATOMS)
    return run_command(
        [poreblazer_exe],
        stdin=input_dat,
        logfile=os.path.join(directory, "poreblazer.log"),
        directory=directory,
    )


# Scalar results printed to the Poreblazer log, as (key, label). The value is the
# number following the label. Checked against Poreblazer v3.0.5.
_LOG_VALUES = [
    ("system_volume_A3", "System volume in A^3:"),
    ("system_mass_g_mol", "System mass, g/mol:"),
    ("system_density_g_cm3", "System density, g/cm^3:"),
    ("helium_volume_A3", "Helium volume in A^3:"),
    ("helium_volume_cm3_g", "Helium volume in cm^3/g:"),
    ("geometric_volume_A3", "Geometric (point accessible) volume in A^3:"),
    ("geometric_volume_cm3_g", "Geometric (point accessible) volume in cm^3/g:"),
    ("surface_area_A2", "Accessible surface area in A^2:"),
    ("surface_area_m2_cm3", "Accessible surface area per volume in m^2/cm^3:"),
    ("surface_area_m2_g", "Accessible surface area per mass in m^2/g:"),
    ("pore_limiting_diameter_A", "Pore limiting diameter in A:"),
    ("maximum_pore_diameter_A", "Maximum pore diameter in A:"),
]
_NUMBER = r"\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"


def parse_log(text):
    """Return a dict of the scalar results in a Poreblazer log; missing values are None"""
    results = {}
    for key, label in _LOG_VALUES:
        m = re.search(re.escape(label) + _NUMBER, text)
        results[key] = float(m.group(1)) if m else None
    m = re.search(r"percolated in\s+([0-9]+)\s+dimension", text)
    results["percolated_dimensions"] = int(m.group(1)) if m else None
    m = re.search(r"Poreblazer_v([0-9][0-9.]*[0-9])", text)
    results["version"] = m.group(1) if m else None
    return results


def parse_distribution(path):
    """Return [[diameter, value], ...] from a Poreblazer psd file, or None if it is missing"""
    if not os.path.isfile(path):
        return None
    rows = []
    with open(path) as f:
        for line in f:
            fields = line.split()
            if len(fields) == 2 and not line.lstrip().startswith("#"):
                rows.append([float(fields[0]), float(fields[1])])
    return rows


def parse_output(directory):
    """Parse the results of a Poreblazer run in directory.

    Returns a dict with the scalar values from poreblazer.log (see _LOG_VALUES),
    percolated_dimensions, version, and the pore size distributions psd and
    psd_cumulative as lists of [diameter (A), value]. Anything Poreblazer did not
    produce is None.
    """
    logfile = os.path.join(directory, "poreblazer.log")
    text = ""
    if os.path.isfile(logfile):
        with open(logfile) as f:
            text = f.read()
    results = parse_log(text)
    results["psd"] = parse_distribution(os.path.join(directory, "psd.txt"))
    results["psd_cumulative"] = parse_distribution(
        os.path.join(directory, "psd_cumulative.txt")
    )
    return results
