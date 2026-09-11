"""Regression tests for ``buildspec/validate.py``'s path handling.

Loaded by path for the same reason ``render.py`` is in test_provision.py:
it is a standalone script that runs inside the CodeBuild container, not an
importable package member -- but its behaviour is a contract the platform
depends on, so the test reads the real file rather than a copy.

Why these tests exist, specifically: on 2026-09-10 a real model bundle was
zipped on Windows with PowerShell 5.1's ``Compress-Archive``, which writes
BACKSLASH path separators in violation of the ZIP spec (APPNOTE 4.4.17.1
requires forward slashes). Read on Linux, ``Inputs\\cpi.csv`` is not a file
in a directory -- it is one file whose name contains a backslash. An
extractor that trusts the stored name produces a flat pile of oddly-named
files at the bundle root, ``Inputs/`` never exists, and the app dies at
RUNTIME with "cannot open file 'Inputs/cpi.csv'" long after validation said
the bundle was fine.

``validate.py`` gets this right, but only because ``scan()`` writes the
normalised name back onto each ZipInfo before ``extract()`` sees it. That is
one easily-deleted line. These tests pin the behaviour so a refactor cannot
quietly reintroduce a failure that only shows up in production.

Note you cannot build the pathological archive with ``zipfile.writestr`` on
Windows: ZipInfo rewrites ``os.sep`` to "/" on construction. Reading does no
such thing -- which is the real case, since CodeBuild reads an archive some
Windows tool wrote. So these tests flip the separators on the ZipInfo
objects after opening, which is exactly the state a genuine Compress-Archive
bundle arrives in.
"""

from __future__ import annotations

import importlib.util
import tempfile
import zipfile
from pathlib import Path

import pytest

BACKSLASH = chr(92)


@pytest.fixture()
def tmp_path():
    """Shadows pytest's own ``tmp_path``.

    pytest's fixture cannot run on this machine -- ``%TEMP%\\pytest-of-<user>``
    is permission-denied -- so the suite uses ``tempfile`` directly. Named
    the same so the tests below read normally.
    """
    with tempfile.TemporaryDirectory(prefix="validate-test-") as d:
        yield Path(d)


def _validate_module():
    path = Path(__file__).resolve().parents[1] / "buildspec" / "validate.py"
    spec = importlib.util.spec_from_file_location("_buildspec_validate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = _validate_module()


def _bundle(tmp: Path, *, wrapper: str = "") -> Path:
    """A minimal but realistic bundle: entrypoint pair plus a data folder."""
    prefix = f"{wrapper}/" if wrapper else ""
    zip_path = tmp / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{prefix}ui.R", "# ui\n")
        zf.writestr(f"{prefix}server.R", "# server\n")
        zf.writestr(f"{prefix}Inputs/cpi.csv", "year,value\n2024,1.0\n")
        zf.writestr(f"{prefix}Inputs/nested/deep.csv", "a\n1\n")
    return zip_path


def _to_backslashes(zf: zipfile.ZipFile) -> int:
    """Put the archive into the state a Compress-Archive zip arrives in."""
    flipped = 0
    for info in zf.infolist():
        if "/" in info.filename:
            info.filename = info.filename.replace("/", BACKSLASH)
            flipped += 1
    return flipped


def test_backslash_separators_extract_as_real_directories(tmp_path):
    """The whole point: Inputs/cpi.csv must be a file in a directory."""
    dest = tmp_path / "out"
    with zipfile.ZipFile(_bundle(tmp_path)) as zf:
        assert _to_backslashes(zf) == 2  # the two Inputs/ entries
        members, _ = validate.scan(zf)
        validate.extract(zf, members, str(dest))

    assert (dest / "Inputs").is_dir()
    assert (dest / "Inputs" / "cpi.csv").is_file()
    assert (dest / "Inputs" / "nested" / "deep.csv").is_file()
    assert [p.name for p in dest.iterdir() if BACKSLASH in p.name] == []


def test_scan_hands_extract_normalised_names(tmp_path):
    """The mechanism, pinned directly.

    ``extract()`` reads ``info.filename``; if ``scan()`` stops rewriting it
    the previous test still passes on a forward-slash bundle and breaks only
    on a Windows one. Assert the normalisation where it happens.
    """
    with zipfile.ZipFile(_bundle(tmp_path)) as zf:
        _to_backslashes(zf)
        members, _ = validate.scan(zf)

    assert all(BACKSLASH not in m.filename for m in members)
    assert any(m.filename == "Inputs/cpi.csv" for m in members)


def test_a_backslash_wrapper_directory_is_still_found_and_flattened(tmp_path):
    """Windows zips are usually made by right-clicking the app FOLDER, so
    the wrapper case and the backslash case arrive together."""
    dest = tmp_path / "out"
    with zipfile.ZipFile(_bundle(tmp_path, wrapper="MyApp")) as zf:
        _to_backslashes(zf)
        members, _ = validate.scan(zf)
        validate.extract(zf, members, str(dest))
        kind, flattened = validate.resolve_entrypoint(str(dest))

    assert kind == "ui.R+server.R"
    assert flattened == "MyApp"
    assert (dest / "ui.R").is_file()
    assert (dest / "Inputs" / "cpi.csv").is_file()


def test_traversal_dressed_up_in_backslashes_is_still_rejected(tmp_path):
    """Normalising must not become a way to smuggle a path escape past the
    check -- the reason validate.normalise() exists at all."""
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ui.R", "# ui\n")
        zf.writestr("server.R", "# server\n")

    with zipfile.ZipFile(zip_path) as zf:
        zf.infolist()[0].filename = f"..{BACKSLASH}..{BACKSLASH}etc{BACKSLASH}passwd"
        with pytest.raises(validate.Rejected):
            validate.scan(zf)
