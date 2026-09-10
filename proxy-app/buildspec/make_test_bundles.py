#!/usr/bin/env python3
"""
Generate the reference and adversarial bundles for testing validate.py.

This exists so the README's "Known-good reference test" is reproducible by
anyone, not folklore. It writes:

    dashboard-root.zip      the real dashboard-app/, files at the zip root
    dashboard-wrapped.zip   the same, inside a dashboard-app/ wrapper dir
    macjunk.zip             a wrapper dir plus __MACOSX/ and .DS_Store noise
    slip.zip                contains ../../etc/evil.R
    absolute.zip            contains /etc/passwd
    symlink.zip             contains a symlink entry
    noentry.zip             no app.R and no ui.R/server.R
    twodirs.zip             two top-level directories, each with an app.R
    halfpair.zip            ui.R with no server.R
    toomany.zip             5201 entries

Expected outcome: the first three validate clean, the rest are rejected with
the messages in README.md's validator table.

    python3 make_test_bundles.py --out /tmp/bundles
    for z in /tmp/bundles/*.zip; do
      echo "== $z"; python3 validate.py --zip "$z" --dest /tmp/x || true
    done
"""

from __future__ import annotations

import argparse
import os
import pathlib
import zipfile

REAL_FILES = [
    "app.R",
    "access.R",
    "Tx_sequence_after_2022_tarpeyo.xlsx",
    "Dockerfile",
    "README.md",
    ".dockerignore",
]

STUB_APP = "shinyApp(ui = fluidPage('hello'), server = function(input, output, session) {})\n"


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="directory to write the zips into")
    ap.add_argument(
        "--dashboard-app",
        default=str(here.parent.parent / "dashboard-app"),
        help="path to the real dashboard bundle source",
    )
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    src = pathlib.Path(args.dashboard_app)

    if not (src / "app.R").is_file():
        print(f"dashboard-app not found at {src}; skipping the two real bundles")
    else:
        for name, prefix in (("dashboard-root.zip", ""), ("dashboard-wrapped.zip", "dashboard-app/")):
            with zipfile.ZipFile(out / name, "w", zipfile.ZIP_DEFLATED) as z:
                for f in REAL_FILES:
                    if (src / f).is_file():
                        z.write(src / f, prefix + f)

    with zipfile.ZipFile(out / "macjunk.zip", "w") as z:
        z.writestr("myapp/app.R", STUB_APP)
        z.writestr("__MACOSX/._app.R", "junk")
        z.writestr(".DS_Store", "junk")

    with zipfile.ZipFile(out / "slip.zip", "w") as z:
        z.writestr("app.R", STUB_APP)
        z.writestr("../../etc/evil.R", "system('id')")

    with zipfile.ZipFile(out / "absolute.zip", "w") as z:
        z.writestr("app.R", STUB_APP)
        z.writestr("/etc/passwd", "root:x:0:0")

    with zipfile.ZipFile(out / "symlink.zip", "w") as z:
        z.writestr("app.R", STUB_APP)
        link = zipfile.ZipInfo("secrets")
        link.external_attr = 0xA1FF << 16  # S_IFLNK | 0777
        z.writestr(link, "/etc/passwd")

    with zipfile.ZipFile(out / "noentry.zip", "w") as z:
        z.writestr("helpers.R", "f <- function() 1")
        z.writestr("data/thing.csv", "a,b\n1,2\n")

    with zipfile.ZipFile(out / "twodirs.zip", "w") as z:
        z.writestr("one/app.R", STUB_APP)
        z.writestr("two/app.R", STUB_APP)

    with zipfile.ZipFile(out / "halfpair.zip", "w") as z:
        z.writestr("ui.R", "fluidPage('hi')")
        z.writestr("helpers.R", "f <- function() 1")

    with zipfile.ZipFile(out / "toomany.zip", "w") as z:
        z.writestr("app.R", STUB_APP)
        for i in range(5200):
            z.writestr(f"renv/library/pkg{i}.R", "x")

    for p in sorted(out.glob("*.zip")):
        print(f"{p.name:24} {p.stat().st_size:>9} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
