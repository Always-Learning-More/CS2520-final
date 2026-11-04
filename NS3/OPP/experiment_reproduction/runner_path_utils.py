#!/usr/bin/env python3
"""Path helpers for experiment reproduction runners."""

import os
import pathlib
import shutil
import sys


# ns-3.17's bundled waf only runs under Python 2. The runners launch it with
# this interpreter. Override it without editing code, e.g.:
#   export OPP_PYTHON2=/opt/python2.7/bin/python2.7
PYTHON2 = os.environ.get("OPP_PYTHON2", "python2")


def require_python2():
    """Stop early with a clear message if the waf interpreter is missing.

    Without this check every ns-3 task fails instantly and is only reported as
    status=exception.
    """
    if shutil.which(PYTHON2) is None:
        sys.exit(
            "error: cannot find the Python 2 interpreter %r needed to run ns-3's waf.\n"
            "  On Ubuntu 22.04+ / WSL, run inside the bundled container instead:\n"
            "    wsl/opp.sh python3 <runner.py> ...\n"
            "  or point OPP_PYTHON2 at a Python 2.7 binary." % PYTHON2)


CONFIG_PATH_KEYS = {
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "OPP_PROBE_DELAY_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "FLOW_COVERAGE_OUTPUT_FILE",
    "QLEN_MON_FILE",
    "RDPROBE_DIAG_OUTPUT_FILE",
    "RDPROBE_DIAG_RESULT_OUTPUT_FILE",
    "RPINGMESH_DIAG_RESULT_OUTPUT_FILE",
    "OPP_DIAG_RESULT_OUTPUT_FILE",
    "SWITCH_QLEN_TRACE_OUTPUT_FILE",
    "QLEN_TRACE_OUTPUT_FILE",
    "OPP_LOOPBACK_TIMESERIES_OUTPUT_FILE",
    "OPP_LOOPBACK_PEAK_BUCKET_OUTPUT_FILE",
    "OPP_LOOPBACK_RATE_TRACE_OUTPUT_FILE",
}


def path_relative_to(path, base):
    """Return a portable path string relative to base."""
    return os.path.relpath(str(pathlib.Path(path)), str(pathlib.Path(base)))


def relativize_config_lines(lines, base):
    """Convert absolute path values in ns-3 config lines to paths relative to base."""
    output = []
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in CONFIG_PATH_KEYS:
            value = parts[1].strip()
            if value and pathlib.Path(value).is_absolute():
                line = "%s %s" % (parts[0], path_relative_to(value, base))
        output.append(line)
    return output
