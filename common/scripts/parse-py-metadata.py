#!/usr/bin/python3

# vim: set ts=4 sw=4 et:
"""
Usage:

./parse-py-metadata.py -S "$DESTDIR/$py3_sitelib" provides -v "$version"

    extract the names of top-level packages from:
      - $DESTDIR/$py3_sitelib/*.dist-info/METADATA
      - $DESTDIR/$py3_sitelib/*.egg-info/PKG-INFO

./parse-py-metadata.py -S "$DESTDIR/$py3_sitelib" [-s] [-C] depends -e "extra1 extra2 ..."
    -D "$XBPS_STATEDIR/$pkgname-rdeps" -V <( xbps-query -R -p provides -s "py3:" )

    check that the dependencies of a package match what's listed in the python
    package metadata, using the virtual package provides entries generated by
    `parse-py-metadata.py provides`.

This script requires python3-packaging-bootstrap to be installed in the chroot
to run (which should be taken care of by the python3-module and python3-pep517
build styles).
"""

import argparse
from pathlib import Path
from sys import stderr
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packaging.metadata import Metadata
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

# packages to always ignore
global_ignore = ["tzdata"]


def msg_err(msg: str, *, nocolor: bool = False, strict: bool = False):
    if nocolor:
        print(msg, flush=True)
    else:
        color = "31" if strict else "33"
        print(f"\033[1m\033[{color}m{msg}\033[m", file=stderr, flush=True)


def vpkgname(val: "str | Requirement", *, version: str | None = None) -> str:
    sfx = ""
    if version is not None:
        sfx = f"-{version}"
    if isinstance(val, Requirement):
        name = val.name
    else:
        name = val
    return f"py3:{canonicalize_name(name)}{sfx}"


def getpkgname(pkgver: str) -> str:
    return pkgver.rpartition("-")[0]


def getpkgversion(pkgver: str) -> str:
    return pkgver.rpartition("-")[2]


def getpkgdepname(pkgdep: str) -> str:
    if "<" in pkgdep:
        return pkgdep.partition("<")[0]
    elif ">" in pkgdep:
        return pkgdep.partition(">")[0]
    else:
        return pkgdep.rpartition("-")[0]


def match_markers(req: "Requirement", extras: set[str]) -> bool:
    # unconditional requirement
    if req.marker is None:
        return True

    # check the requirement for each extra we want and without any extras
    if extras:
        return req.marker.evaluate() or any(req.marker.evaluate({"extra": e}) for e in extras)

    return req.marker.evaluate()


def find_metadata_files(sitepkgs: Path) -> list[Path]:
    metafiles = list(sitepkgs.glob("*.dist-info/METADATA"))
    metafiles.extend(sitepkgs.glob("*.egg-info/PKG-INFO"))
    return metafiles


def parse_provides(args):
    out = set()

    for metafile in find_metadata_files(args.sitepkgs):
        with metafile.open() as f:
            raw = f.read()

        meta = Metadata.from_email(raw, validate=False)

        out.add(vpkgname(meta.name, version=getpkgversion(args.pkgver)))
        if meta.provides_dist is not None:
            out.update(map(lambda n: vpkgname(n, version=getpkgversion(args.pkgver)), meta.provides_dist))
        # deprecated but may be used
        if meta.provides is not None:
            out.update(map(lambda n: vpkgname(n, version=getpkgversion(args.pkgver)), meta.provides))

        print("\n".join(out), flush=True)


def parse_depends(args):
    depends = dict()
    vpkgs = dict()
    extras = set(args.extras.split())

    with args.vpkgs.open() as f:
        for ln in f.readlines():
            if not ln.strip():
                continue
            pkgver, _, rest = ln.partition(":")
            vpkgvers, _, _ = rest.strip().partition("(")
            pkg = getpkgname(pkgver)
            vpkg = map(getpkgname, vpkgvers.split())
            for v in vpkg:
                vpkgs[v] = pkg

    if args.rdeps.exists():
        with args.rdeps.open() as f:
            rdeps = list(map(getpkgdepname, f.read().split()))
    else:
        rdeps = []

    for metafile in find_metadata_files(args.sitepkgs):
        with metafile.open() as f:
            raw = f.read()

        meta = Metadata.from_email(raw, validate=False)

        if meta.requires_dist is not None:
            depends.update(map(lambda p: (vpkgname(p), None),
                               filter(lambda r: match_markers(r, extras), meta.requires_dist)))
        # deprecated but may be used
        if meta.requires is not None:
            depends.update(map(lambda p: (vpkgname(p), None), meta.requires))

    err = False
    unknown = False
    missing = []
    for k in depends.keys():
        if k in vpkgs.keys():
            pkgname = vpkgs[k]
            if pkgname in rdeps:
                print(f"   PYTHON: {k} <-> {pkgname}", flush=True)
            elif pkgname in global_ignore:
                print(f"   PYTHON: {k} <-> {pkgname} (ignored)", flush=True)
            else:
                msg_err(f"   PYTHON: {k} <-> {pkgname} NOT IN depends PLEASE FIX!",
                        nocolor=args.nocolor, strict=args.strict)
                missing.append(pkgname)
                err = True
        else:
            msg_err(f"   PYTHON: {k} <-> UNKNOWN PKG PLEASE FIX!",
                    nocolor=args.nocolor, strict=args.strict)
            unknown = True
            err = True

    if missing or unknown:
        msg_err(f"=> {args.pkgver}: missing dependencies detected!",
                nocolor=args.nocolor, strict=args.strict)
    if missing:
        msg_err(f"=> {args.pkgver}: please add these packages to depends: {' '.join(sorted(missing))}",
                nocolor=args.nocolor, strict=args.strict)

    if err and args.strict:
        exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-S", dest="sitepkgs", type=Path)
    parser.add_argument("-v", dest="pkgver")
    parser.add_argument("-s", dest="strict", action="store_true")
    parser.add_argument("-C", dest="nocolor", action="store_true")
    subparsers = parser.add_subparsers()

    prov_parser = subparsers.add_parser("provides")
    prov_parser.set_defaults(func=parse_provides)

    deps_parser = subparsers.add_parser("depends")
    deps_parser.add_argument("-e", dest="extras", default="")
    deps_parser.add_argument("-V", dest="vpkgs", type=Path)
    deps_parser.add_argument("-D", dest="rdeps", type=Path)
    deps_parser.set_defaults(func=parse_depends)

    args = parser.parse_args()

    try:
        from packaging.metadata import Metadata
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
    except ImportError:
        msg_err(f"=> WARNING: {args.pkgver}: missing packaging module!\n"
                f"=> WARNING: {args.pkgver}: please add python3-packaging-bootstrap to hostmakedepends to run this check",
                nocolor=args.nocolor)
        exit(0)

    args.func(args)
