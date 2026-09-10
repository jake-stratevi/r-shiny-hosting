#!/usr/bin/env python3
"""
Bundle validation and safe extraction for the P2a self-service image pipeline.

Implements the "Validation" section of docs/design/portal-p2a.md:

    Zip <= 100 MB, <= 5000 entries, <= 250 MB extracted, no path escaping
    root, no symlinks, and an entrypoint (app.R, or ui.R + server.R) at the
    root or in exactly one wrapper directory (flatten it).

Deliberately NOT here: malware scanning. An R app is code we have chosen to
execute; scanning it is theater. Containment is the control (per-app IAM role
under a permissions boundary, per-app container, no NAT egress, capped
compute). See the spec.

Runs on stock CPython 3.8+ with the standard library only. CodeBuild's
`aws/codebuild/standard:7.0` image has python3 on PATH; no pip install, no
`unzip`, no extra runtime declaration is required.

The zip is never handed to an external extractor. Every member is inspected
and written by this script, so a malicious entry cannot win a race between
"check" and "extract".

Usage:
    python3 validate.py --zip bundle.zip --dest /tmp/appsrc [--json-out r.json]

Exit codes:
    0  bundle is valid; --dest contains the flattened app, entrypoint at root
    2  bundle rejected (a VALIDATION ERROR line on stderr names the problem
       and, where there is one, the offending file)
    3  usage / IO error
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import zipfile

# Spec limits. Overridable only for testing; the defaults are the contract.
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_EXTRACTED_BYTES = 250 * 1024 * 1024

# Archive noise that macOS and Windows add. Skipped entirely: not extracted,
# not counted, and not treated as a wrapper directory candidate.
IGNORED_PREFIXES = ("__MACOSX/",)
IGNORED_BASENAMES = (".DS_Store", "Thumbs.db", "desktop.ini")

ENTRYPOINT_SINGLE = "app.R"
ENTRYPOINT_PAIR = ("ui.R", "server.R")


class Rejected(Exception):
    """A bundle that fails the spec. The message must name the problem."""


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def is_ignored(name: str) -> bool:
    if any(name.startswith(p) for p in IGNORED_PREFIXES):
        return True
    return os.path.basename(name.rstrip("/")) in IGNORED_BASENAMES


def normalise(name: str) -> str:
    """Zip names are always '/'-separated. Backslashes are a Windows tool bug
    (or an escape attempt); treat them as separators so the traversal check
    below cannot be side-stepped with 'a\\..\\..\\etc'."""
    return name.replace("\\", "/")


def check_path(name: str) -> None:
    """Reject anything that does not resolve strictly inside the root."""
    if not name or name.strip() == "":
        raise Rejected("the archive contains an entry with an empty name")
    if "\x00" in name:
        raise Rejected(f"entry name contains a NUL byte: {name!r}")
    if name.startswith("/"):
        raise Rejected(
            f"absolute path in archive (zip-slip): {name!r} — "
            "bundle paths must be relative to the bundle root"
        )
    if len(name) > 1 and name[1] == ":":
        raise Rejected(
            f"drive-qualified path in archive (zip-slip): {name!r} — "
            "re-zip from inside the app folder, not from a drive root"
        )
    parts = [p for p in name.split("/") if p not in ("", ".")]
    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                raise Rejected(
                    f"path escapes the bundle root (zip-slip): {name!r}"
                )
        else:
            depth += 1


def scan(zf: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], int]:
    """First pass: every rule that can be decided from the central directory."""
    members: list[zipfile.ZipInfo] = []
    total = 0

    for info in zf.infolist():
        name = normalise(info.filename)
        if is_ignored(name):
            continue

        check_path(name)

        # Zips written by Windows tools carry no Unix mode at all, and
        # ZipFile.writestr sets permission bits with no file-type nibble.
        # Only judge the type when the archive actually recorded one.
        mode = info.external_attr >> 16
        ftype = stat.S_IFMT(mode)
        if ftype == stat.S_IFLNK:
            raise Rejected(
                f"symlink in bundle: {name!r} — symlinks are rejected because "
                "they can point outside the image; replace it with the real file"
            )
        if ftype not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise Rejected(
                f"not a regular file or directory: {name!r} "
                f"(mode {ftype:#o}) — only files and folders may be uploaded"
            )

        info.filename = name
        members.append(info)

        if not name.endswith("/"):
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise Rejected(
                    f"bundle expands to more than {human(MAX_EXTRACTED_BYTES)} "
                    f"(limit exceeded while reading {name!r}) — "
                    "move large data files to the app's S3 data prefix"
                )

    file_count = sum(1 for m in members if not m.filename.endswith("/"))
    if len(members) > MAX_ENTRIES:
        raise Rejected(
            f"bundle contains {len(members)} entries, more than the "
            f"{MAX_ENTRIES} allowed — this is almost always a stray "
            "renv/ library, .git/ or node_modules/ folder that should not be "
            "in the upload"
        )
    if file_count == 0:
        raise Rejected("the archive contains no files")

    return members, total


def extract(zf: zipfile.ZipFile, members: list[zipfile.ZipInfo], dest: str) -> int:
    """Second pass: write members ourselves, re-checking containment against
    the realpath of the destination. Nothing is executed and no mode bit from
    the archive is honoured — files land 0644, directories 0755."""
    root = os.path.realpath(dest)
    os.makedirs(root, exist_ok=True)
    written = 0

    for info in members:
        name = info.filename
        target = os.path.realpath(os.path.join(root, name))
        if target != root and not target.startswith(root + os.sep):
            raise Rejected(f"path escapes the bundle root on extraction: {name!r}")

        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 256)
        os.chmod(target, 0o644)
        written += os.path.getsize(target)

    return written


def find_entrypoint(directory: str) -> str | None:
    names = set(os.listdir(directory))
    if ENTRYPOINT_SINGLE in names:
        return ENTRYPOINT_SINGLE
    if all(n in names for n in ENTRYPOINT_PAIR):
        return "ui.R+server.R"
    return None


def half_pair(directory: str) -> str | None:
    names = set(os.listdir(directory))
    present = [n for n in ENTRYPOINT_PAIR if n in names]
    if len(present) == 1:
        return present[0]
    return None


def flatten(wrapper: str, root: str) -> None:
    """Move the wrapper directory's contents up to the bundle root."""
    for entry in os.listdir(wrapper):
        src = os.path.join(wrapper, entry)
        dst = os.path.join(root, entry)
        if os.path.exists(dst):
            raise Rejected(
                f"cannot flatten wrapper directory {os.path.basename(wrapper)!r}: "
                f"it contains {entry!r}, which already exists at the bundle root"
            )
        shutil.move(src, dst)
    os.rmdir(wrapper)


def resolve_entrypoint(root: str) -> tuple[str, str | None]:
    """Returns (entrypoint_kind, flattened_wrapper_name_or_None)."""
    found = find_entrypoint(root)
    if found:
        return found, None

    dangling = half_pair(root)
    if dangling:
        other = [n for n in ENTRYPOINT_PAIR if n != dangling][0]
        raise Rejected(
            f"found {dangling} at the bundle root but no {other} — a two-file "
            f"Shiny app needs both ui.R and server.R, or a single app.R"
        )

    entries = sorted(os.listdir(root))
    dirs = [d for d in entries if os.path.isdir(os.path.join(root, d))]
    candidates = [d for d in dirs if find_entrypoint(os.path.join(root, d))]

    if len(candidates) == 1:
        wrapper = candidates[0]
        flatten(os.path.join(root, wrapper), root)
        found = find_entrypoint(root)
        if not found:  # unreachable; belt and braces
            raise Rejected(
                f"entrypoint disappeared while flattening wrapper directory {wrapper!r}"
            )
        return found, wrapper

    if len(candidates) > 1:
        raise Rejected(
            "more than one directory looks like the app: "
            + ", ".join(repr(c) for c in candidates)
            + " — zip only the app folder, or put app.R at the bundle root"
        )

    listing = ", ".join(entries[:20]) or "(empty)"
    if len(entries) > 20:
        listing += f", … ({len(entries)} entries total)"
    raise Rejected(
        "no app.R or ui.R/server.R found at the bundle root or in a single "
        f"wrapper directory — the bundle's top level contains: {listing}"
    )


def apply_limits(args: argparse.Namespace) -> None:
    """Test hooks only. Unset flags leave the spec defaults in place."""
    global MAX_ENTRIES, MAX_EXTRACTED_BYTES, MAX_ZIP_BYTES
    if args.max_entries is not None:
        MAX_ENTRIES = args.max_entries
    if args.max_extracted_bytes is not None:
        MAX_EXTRACTED_BYTES = args.max_extracted_bytes
    if args.max_zip_bytes is not None:
        MAX_ZIP_BYTES = args.max_zip_bytes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True, help="path to the uploaded bundle")
    ap.add_argument("--dest", required=True, help="directory to extract into (created)")
    ap.add_argument("--json-out", help="write a machine-readable result here")
    ap.add_argument("--max-entries", type=int, default=None)
    ap.add_argument("--max-extracted-bytes", type=int, default=None)
    ap.add_argument("--max-zip-bytes", type=int, default=None)
    args = ap.parse_args(argv)

    apply_limits(args)

    result: dict = {"ok": False, "zip": args.zip, "app_dir": os.path.abspath(args.dest)}

    def finish(code: int) -> int:
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(result, fh)
                fh.write("\n")
        return code

    try:
        if not os.path.isfile(args.zip):
            raise Rejected(f"upload not found at {args.zip}")

        size = os.path.getsize(args.zip)
        result["zip_bytes"] = size
        if size > MAX_ZIP_BYTES:
            raise Rejected(
                f"upload is {human(size)}, larger than the "
                f"{human(MAX_ZIP_BYTES)} limit"
            )
        if size == 0:
            raise Rejected("upload is empty (0 bytes)")

        if os.path.exists(args.dest):
            shutil.rmtree(args.dest)

        if not zipfile.is_zipfile(args.zip):
            raise Rejected(
                "upload is not a zip archive — .rar, .7z and .tar.gz are not "
                "accepted; re-save as .zip"
            )

        with zipfile.ZipFile(args.zip) as zf:
            # scan() first: it reads only the central directory, so a
            # decompression bomb is rejected before anything is inflated.
            members, declared = scan(zf)
            bad = zf.testzip()
            if bad is not None:
                raise Rejected(f"corrupt entry in archive (CRC mismatch): {bad!r}")
            result["entries"] = len(members)
            result["declared_bytes"] = declared
            written = extract(zf, members, args.dest)
            result["extracted_bytes"] = written

        if written > MAX_EXTRACTED_BYTES:  # declared sizes can lie
            raise Rejected(
                f"bundle extracted to {human(written)}, more than the "
                f"{human(MAX_EXTRACTED_BYTES)} limit"
            )

        kind, wrapper = resolve_entrypoint(os.path.abspath(args.dest))
        result["entrypoint"] = kind
        result["flattened_wrapper"] = wrapper

        notes = []
        root = os.path.abspath(args.dest)
        if os.path.exists(os.path.join(root, "Dockerfile")):
            notes.append(
                "the bundle contains its own Dockerfile; it is ignored — the "
                "platform renders the image definition from its own template"
            )
        if os.path.exists(os.path.join(root, "renv.lock")):
            notes.append(
                "renv.lock present; the wizard resolves packages from it, but "
                "the build installs exactly the confirmed PACKAGES list"
            )
        result["notes"] = notes
        result["ok"] = True

        print(
            f"bundle OK: {result['entries']} entries, "
            f"{human(written)} extracted, entrypoint {kind}"
            + (f", flattened wrapper directory '{wrapper}'" if wrapper else "")
        )
        for note in notes:
            print(f"note: {note}")
        return finish(0)

    except Rejected as exc:
        result["error"] = str(exc)
        print(f"VALIDATION ERROR: {exc}", file=sys.stderr)
        return finish(2)
    except zipfile.BadZipFile as exc:
        result["error"] = f"unreadable zip: {exc}"
        print(f"VALIDATION ERROR: unreadable zip archive: {exc}", file=sys.stderr)
        return finish(2)
    except OSError as exc:
        result["error"] = f"io error: {exc}"
        print(f"ERROR: {exc}", file=sys.stderr)
        return finish(3)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
