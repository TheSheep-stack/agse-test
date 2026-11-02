#!/usr/bin/env python3
"""Check a requirements.txt against PyPI.

Prints:
  1. Dependencies checked (number of requirement lines)
  2. Packages not found on PyPI
  3. Packages incompatible with Python 3.11
  4. Package with the highest minimum Python version

Usage: python check_requirements.py [requirements.txt]
"""
import json
import sys
import urllib.error
import urllib.request

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

TARGET_PYTHON = Version("3.11")
PYPI_URL = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_URL = "https://pypi.org/pypi/{name}/{version}/json"


def read_requirements(path):
    """Yield (line, Requirement) for each requirement line in the file."""
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith(("#", "-")):  # comments, -r, -e, --index-url ...
                continue
            try:
                yield line, Requirement(line)
            except InvalidRequirement:
                print(f"  ! Skipping unparsable line: {line}", file=sys.stderr)


def fetch_json(url):
    """Return parsed JSON from a PyPI URL, or None on 404."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def resolve_version(data, req):
    """Newest non-yanked release that satisfies the requirement's specifier."""
    latest = data["info"]["version"]
    if not req.specifier or Version(latest) in req.specifier:
        return latest

    candidates = []
    for ver_str, files in data.get("releases", {}).items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            ver = Version(ver_str)
        except InvalidVersion:
            continue
        if ver.is_prerelease and not req.specifier.prereleases:
            continue
        if ver in req.specifier:
            candidates.append(ver)
    return str(max(candidates)) if candidates else latest


def pick_version(data, req):
    """Return (version, requires_python) for the version this requirement resolves to.

    requires_python is read from https://pypi.org/pypi/<name>/<version>/json.
    """
    version = resolve_version(data, req)
    if version == data["info"]["version"]:
        return version, data["info"].get("requires_python")  # already have it
    ver_data = fetch_json(PYPI_VERSION_URL.format(name=req.name, version=version))
    if ver_data is None:
        return version, None
    return version, ver_data["info"].get("requires_python")


def min_python(spec):
    """Lowest Python version allowed by a SpecifierSet (from >=, >, ~=, ==)."""
    lows = []
    for s in spec:
        if s.operator in (">=", ">", "~=", "==", "==="):
            try:
                lows.append(Version(s.version.rstrip(".*")))
            except InvalidVersion:
                pass
    return max(lows) if lows else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "requirements.txt"
    requirements = list(read_requirements(path))

    not_found, incompatible = [], []
    highest = None  # (min_version, package, package_version, requires_python)

    for line, req in requirements:
        data = fetch_json(PYPI_URL.format(name=req.name))
        if data is None:
            not_found.append(req.name)
            continue

        pkg_version, requires_python = pick_version(data, req)
        if not requires_python:
            continue
        try:
            spec = SpecifierSet(requires_python)
        except InvalidSpecifier:
            continue

        if not spec.contains(TARGET_PYTHON, prereleases=True):
            incompatible.append(f"{req.name}=={pkg_version} (requires-python {requires_python})")

        low = min_python(spec)
        if low and (highest is None or low > highest[0]):
            highest = (low, req.name, pkg_version, requires_python)

    print(f"1. Dependencies checked: {len(requirements)}")
    print(f"2. Not found on PyPI: {len(not_found)}")
    print(f"3. Incompatible with Python {TARGET_PYTHON}: {len(incompatible)}")
    if highest:
        low, name, ver, rp = highest
        print(f"4. Highest requirement: {name}=={ver} needs Python >= {low} ({rp})")
    else:
        print("4. Highest requirement: none of the packages declare requires-python")


if __name__ == "__main__":
    main()