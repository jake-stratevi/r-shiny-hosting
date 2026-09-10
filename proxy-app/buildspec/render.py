#!/usr/bin/env python3
"""
Environment-variable validation and Dockerfile rendering for the P2a
self-service image pipeline.

The buildspec calls this once, in pre_build, after validate.py has produced a
clean app directory. It:

  1. checks every environment variable in the contract (README.md, "Env var
     contract") and fails with a message naming the offending variable,
  2. normalises the package list,
  3. substitutes the `__TOKEN__` placeholders in Dockerfile.template,
  4. writes the concrete Dockerfile and a JSON record of what it resolved.

This is Python rather than shell for one reason: the values here are
attacker-adjacent. PACKAGES and APP_KEY originate from a web form, and they
end up inside a Dockerfile and an `apt-get install` line. A regex allowlist
applied in one place beats quoting discipline spread across a buildspec.

Standard library only, CPython 3.8+.

Usage:
    python3 render.py --template Dockerfile.template --out /tmp/render/Dockerfile \
                      [--json-out /tmp/render/render.json]

Exit codes: 0 rendered, 2 bad input, 3 IO error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# --- the contract ---------------------------------------------------------

REQUIRED = ("APP_KEY", "ZIP_KEY", "RELEASE_TAG", "UPLOADS_BUCKET", "ECR_REPO_URI", "PACKAGES")

DEFAULTS = {
    "R_VERSION": "4.4.1",
    # Matches dashboard-app/Dockerfile. Bump deliberately, not accidentally.
    "P3M_SNAPSHOT": "2026-05-01",
    # rocker/r-ver:4.4.x is Ubuntu 22.04. Change this if the base moves.
    "P3M_DISTRO": "jammy",
    "EXTRA_SYSTEM_PACKAGES": "",
    "BUILD_ID": "local",
}

# Everything the dashboard image installs, plus the graphics/font/text stack
# that ggplot2, ragg, systemfonts, plotly, readxl and stringi need. Kept
# deliberately short of the geospatial (gdal/geos/proj) and database (unixodbc)
# families: those are large, rarely needed, and available through
# EXTRA_SYSTEM_PACKAGES when an app actually asks for them.
BASE_SYSTEM_PACKAGES = [
    "ca-certificates",
    "curl",              # used by the container health check
    "libcurl4-openssl-dev",
    "libssl-dev",
    "libxml2-dev",
    "zlib1g-dev",
    "libicu-dev",
    "libpng-dev",
    "libjpeg-dev",
    "libtiff5-dev",
    "libfontconfig1-dev",
    "libfreetype6-dev",
    "libharfbuzz-dev",
    "libfribidi-dev",
]

# Shiny is the one package the CMD cannot run without. If an app's declared
# list omits it (a scan of library() calls can miss it in a sourced file), add
# it rather than shipping an image that dies on start — and say so.
MANDATORY_PACKAGES = ("shiny",)

# R package names: letter first, then letters/digits/dot/underscore. This is
# stricter than CRAN needs and that is the point — anything else is either a
# typo or an injection attempt.
RE_PKG = re.compile(r"^[A-Za-z][A-Za-z0-9._]*$")
RE_APT = re.compile(r"^[a-z0-9][a-z0-9.+_-]*$")
# 3-30 chars, lowercase alnum and hyphen, no leading/trailing/double hyphen.
RE_APP_KEY = re.compile(r"^[a-z0-9](?!.*--)[a-z0-9-]{1,28}[a-z0-9]$")
RE_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
RE_R_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RE_SNAPSHOT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
RE_DISTRO = re.compile(r"^[a-z]+$")
# Restricted to shiny-* repositories on purpose: the CodeBuild role's ECR
# grant is scoped that way, and a mismatch should fail here with a clear
# message rather than as an opaque AccessDenied on push.
RE_ECR = re.compile(r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/shiny-[a-z0-9-]+$")
RE_S3_KEY = re.compile(r"^[A-Za-z0-9!_.*'()/-]{1,1024}$")
RE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
RE_BUILD_ID = re.compile(r"^[A-Za-z0-9:_.\\/-]{1,255}$")

# Uppercase-only so the literal `__linux__` in the P3M URL is not mistaken for
# an unsubstituted placeholder.
RE_LEFTOVER = re.compile(r"__[A-Z0-9_]+__")


class BadInput(Exception):
    pass


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def check(name: str, value: str, pattern: re.Pattern, hint: str) -> str:
    if not pattern.match(value):
        raise BadInput(f"{name}={value!r} is not valid — {hint}")
    return value


def split_packages(raw: str) -> list[str]:
    return [p for p in re.split(r"[,\s]+", raw.strip()) if p]


def resolve() -> dict:
    missing = [n for n in REQUIRED if not env(n)]
    if missing:
        raise BadInput(
            "required environment variable(s) not set or empty: "
            + ", ".join(missing)
            + " — see proxy-app/buildspec/README.md, 'Env var contract'"
        )

    app_key = check(
        "APP_KEY", env("APP_KEY"), RE_APP_KEY,
        "app keys are 3-30 characters of lowercase a-z, 0-9 and hyphen, with "
        "no leading, trailing or doubled hyphen",
    )
    release_tag = check(
        "RELEASE_TAG", env("RELEASE_TAG"), RE_TAG,
        "a release tag must be a valid Docker tag (expected the form r1, r2, …)",
    )
    ecr = check(
        "ECR_REPO_URI", env("ECR_REPO_URI"), RE_ECR,
        "expected <acct>.dkr.ecr.<region>.amazonaws.com/shiny-<key>; the build "
        "role may only push to shiny-* repositories",
    )
    bucket = check(
        "UPLOADS_BUCKET", env("UPLOADS_BUCKET"), RE_BUCKET, "not a valid S3 bucket name",
    )
    zip_key = check("ZIP_KEY", env("ZIP_KEY"), RE_S3_KEY, "not a valid S3 object key")

    r_version = check(
        "R_VERSION", env("R_VERSION") or DEFAULTS["R_VERSION"], RE_R_VERSION,
        "expected a full R release such as 4.4.1",
    )
    snapshot = check(
        "P3M_SNAPSHOT", env("P3M_SNAPSHOT") or DEFAULTS["P3M_SNAPSHOT"], RE_SNAPSHOT,
        "expected a Posit Package Manager snapshot date as YYYY-MM-DD",
    )
    distro = check(
        "P3M_DISTRO", env("P3M_DISTRO") or DEFAULTS["P3M_DISTRO"], RE_DISTRO,
        "expected an Ubuntu codename such as jammy",
    )
    build_id = check(
        "BUILD_ID", env("CODEBUILD_BUILD_ID") or env("BUILD_ID") or DEFAULTS["BUILD_ID"],
        RE_BUILD_ID, "unexpected characters in the build id",
    )

    # ECR repo name must agree with the app key. Getting this wrong ships an
    # app's image into another app's repository.
    repo_name = ecr.rsplit("/", 1)[1]
    if repo_name != f"shiny-{app_key}":
        raise BadInput(
            f"ECR_REPO_URI points at repository {repo_name!r} but APP_KEY is "
            f"{app_key!r} — expected 'shiny-{app_key}'"
        )

    declared = split_packages(env("PACKAGES"))
    if not declared:
        raise BadInput(
            "PACKAGES is empty — the wizard must pass the confirmed package "
            "list; the pipeline never guesses"
        )
    for pkg in declared:
        check(
            "PACKAGES", pkg, RE_PKG,
            f"{pkg!r} is not a valid R package name (letters, digits, '.' and "
            "'_', starting with a letter)",
        )

    packages: list[str] = []
    for pkg in declared:
        if pkg not in packages:
            packages.append(pkg)
    added = [p for p in MANDATORY_PACKAGES if p not in packages]
    packages = added + packages

    extra_apt = split_packages(env("EXTRA_SYSTEM_PACKAGES"))
    for pkg in extra_apt:
        check("EXTRA_SYSTEM_PACKAGES", pkg, RE_APT, f"{pkg!r} is not a valid apt package name")
    system_packages = BASE_SYSTEM_PACKAGES + [p for p in extra_apt if p not in BASE_SYSTEM_PACKAGES]

    return {
        "app_key": app_key,
        "release_tag": release_tag,
        "ecr_repo_uri": ecr,
        "image_uri": f"{ecr}:{release_tag}",
        "uploads_bucket": bucket,
        "zip_key": zip_key,
        "r_version": r_version,
        "p3m_snapshot": snapshot,
        "p3m_distro": distro,
        "build_id": build_id,
        "packages": packages,
        "packages_declared": declared,
        "packages_auto_added": added,
        "system_packages": system_packages,
    }


TOKENS = {
    "__R_VERSION__": "r_version",
    "__P3M_DISTRO__": "p3m_distro",
    "__P3M_SNAPSHOT__": "p3m_snapshot",
    "__APP_KEY__": "app_key",
    "__RELEASE_TAG__": "release_tag",
    "__BUILD_ID__": "build_id",
}


def render(template: str, values: dict) -> str:
    out = template
    for token, key in TOKENS.items():
        out = out.replace(token, values[key])
    out = out.replace("__PACKAGES__", " ".join(values["packages"]))
    out = out.replace("__SYSTEM_PACKAGES__", " ".join(values["system_packages"]))

    # Comment lines are skipped: the template's own header documents the
    # placeholder names, and that prose must not look like a rendering bug.
    live = "\n".join(l for l in out.splitlines() if not l.lstrip().startswith("#"))
    leftover = sorted(set(RE_LEFTOVER.findall(live)))
    if leftover:
        raise BadInput(
            "Dockerfile.template contains placeholder(s) render.py does not "
            "know how to fill: " + ", ".join(leftover)
        )
    return out


def main(argv: list[str]) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", default=os.path.join(here, "Dockerfile.template"))
    ap.add_argument("--out", required=True, help="path of the Dockerfile to write")
    ap.add_argument("--json-out", help="write the resolved values here")
    ap.add_argument(
        "--packages-out",
        help="write the resolved package list here, space-separated on one "
        "line, for `--build-arg R_PACKAGES=$(cat ...)`",
    )
    args = ap.parse_args(argv)

    try:
        values = resolve()
        with open(args.template, "r", encoding="utf-8") as fh:
            template = fh.read()
        rendered = render(template, values)

        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rendered)

        values["dockerfile"] = os.path.abspath(args.out)
        if args.packages_out:
            with open(args.packages_out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(" ".join(values["packages"]))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(values, fh, indent=2)
                fh.write("\n")

        print(f"rendered {args.out}")
        print(f"  image      {values['image_uri']}")
        print(f"  R          {values['r_version']} @ P3M {values['p3m_distro']}/{values['p3m_snapshot']}")
        print(f"  packages   {' '.join(values['packages'])}")
        if values["packages_auto_added"]:
            print(
                "  note       added mandatory package(s) missing from the "
                f"declared list: {', '.join(values['packages_auto_added'])}"
            )
        return 0

    except BadInput as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
