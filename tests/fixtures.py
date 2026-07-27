"""Validation fixtures with externally known values.

BCG: Colditz et al. 1994, 13 trials of BCG vaccine against tuberculosis. This is
the ``dat.bcg`` dataset shipped with R's metadat/metafor, so the raw 2x2 counts
are externally fixed and citable:
https://search.r-project.org/CRAN/refmans/metadat/html/dat.bcg.html

Columns are (trial, tpos, tneg, cpos, cneg) = experimental events,
experimental non-events, control events, control non-events.
"""
from __future__ import annotations

from rie import Continuous, Dichotomous

BCG_RAW = [
    ("Aronson 1948", 4, 119, 11, 128),
    ("Ferguson & Simes 1949", 6, 300, 29, 274),
    ("Rosenthal 1960", 3, 228, 11, 209),
    ("Hart & Sutherland 1977", 62, 13536, 248, 12619),
    ("Frimodt-Moller 1973", 33, 5036, 47, 5761),
    ("Stein & Aronson 1953", 180, 1361, 372, 1079),
    ("Vandiviere 1973", 8, 2537, 10, 619),
    ("TPT Madras 1980", 505, 87886, 499, 87892),
    ("Coetzee & Berjak 1968", 29, 7470, 45, 7232),
    ("Rosenthal 1961", 17, 1699, 65, 1600),
    ("Comstock 1974", 186, 50448, 141, 27197),
    ("Comstock 1976", 5, 2493, 3, 2338),
    ("Comstock 1974b", 27, 16886, 29, 17825),
]


def bcg() -> list[Dichotomous]:
    return [
        Dichotomous(study_id=name, events1=tpos, total1=tpos + tneg,
                    events2=cpos, total2=cpos + cneg)
        for name, tpos, tneg, cpos, cneg in BCG_RAW
    ]


#: Zero-cell stress cases. Each entry is (label, table) where the table is
#: (events1, total1, events2, total2).
ZERO_CELL_CASES = [
    ("single_zero_experimental", (0, 50, 6, 50)),
    ("single_zero_control", (7, 50, 0, 50)),
    ("double_zero_events", (0, 40, 0, 40)),
    ("double_zero_non_events", (30, 30, 30, 30)),
    ("all_events_experimental", (25, 25, 10, 30)),
    ("unequal_arms_single_zero", (0, 200, 9, 25)),
]


def zero_cell_studies() -> list[Dichotomous]:
    return [
        Dichotomous(study_id=label, events1=e1, total1=t1, events2=e2, total2=t2)
        for label, (e1, t1, e2, t2) in ZERO_CELL_CASES
    ]


#: A small continuous dataset for MD / SMD checks.
CONTINUOUS_RAW = [
    # (study, n1, mean1, sd1, n2, mean2, sd2)
    ("Trial A", 30, 12.4, 3.1, 28, 14.9, 3.6),
    ("Trial B", 45, 9.8, 2.4, 44, 11.1, 2.9),
    ("Trial C", 18, 15.2, 5.0, 20, 15.8, 4.4),
    ("Trial D", 120, 7.6, 2.0, 118, 8.9, 2.2),
]


def continuous() -> list[Continuous]:
    return [
        Continuous(study_id=s, n1=n1, mean1=m1, sd1=sd1, n2=n2, mean2=m2, sd2=sd2)
        for s, n1, m1, sd1, n2, m2, sd2 in CONTINUOUS_RAW
    ]
