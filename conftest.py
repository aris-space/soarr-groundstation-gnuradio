"""pytest setup: test the checkout's sources, not an installed copy.

The tests import the module as `gnuradio.soarr`, which normally resolves to
the installed package in site-packages. Registering `python/soarr/` under
that name before any test is collected makes every test run against the
code in this repository, with no install and no environment changes.
GNU Radio itself must still be importable (e.g. an activated radioconda
environment).
"""

import importlib.util
import sys
from pathlib import Path

import gnuradio

_PACKAGE_DIR = Path(__file__).resolve().parent / "python" / "soarr"

_spec = importlib.util.spec_from_file_location(
    "gnuradio.soarr",
    _PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(_PACKAGE_DIR)],
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["gnuradio.soarr"] = _module
gnuradio.soarr = _module
_spec.loader.exec_module(_module)
