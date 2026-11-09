#!/usr/bin/env python3
"""Validate and build the QGIS plugin ZIP for the official repository."""
from configparser import ConfigParser
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "FlexScanQGIS"
REQUIRED = ("name", "qgisMinimumVersion", "description", "about", "version", "author", "email")
FOLDER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def validate_source():
    if not FOLDER_PATTERN.fullmatch(PLUGIN_DIR.name):
        raise ValueError(f"QGIS plugin folder name is invalid: {PLUGIN_DIR.name}")
    for filename in ("metadata.txt", "__init__.py", "LICENSE", "icon.png", "icon.svg", "plugin.py", "scan_engine.py"):
        if not (PLUGIN_DIR / filename).is_file():
            raise FileNotFoundError(f"Required plugin file is missing: {filename}")

    parser = ConfigParser(interpolation=None)
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    if not parser.has_section("general"):
        raise ValueError("metadata.txt must contain [general]")
    metadata = parser["general"]
    missing = [key for key in REQUIRED if not metadata.get(key, "").strip()]
    if missing:
        raise ValueError("Required metadata is missing: " + ", ".join(missing))
    for key in ("repository", "tracker", "homepage"):
        if not metadata.get(key, "").startswith("https://"):
            raise ValueError(f"Metadata must include a valid HTTPS {key} URL")
    if "/" in metadata["author"] or "\\" in metadata["author"]:
        raise ValueError("Author metadata cannot contain slashes")
    if metadata.get("icon") != "icon.png":
        raise ValueError("The repository listing icon must be a PNG file")
    if not metadata["version"].strip():
        raise ValueError("Plugin version is empty")
    return metadata["version"].strip()


def build_archive(destination):
    version = validate_source()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(destination) as archive:
        names = archive.namelist()
        if not names or any(not name.startswith(PLUGIN_DIR.name + "/") for name in names):
            raise ValueError("ZIP must contain exactly one top-level plugin directory")
        if f"{PLUGIN_DIR.name}/LICENSE" not in names:
            raise ValueError("ZIP does not contain the required LICENSE file")
        if f"{PLUGIN_DIR.name}/metadata.txt" not in names:
            raise ValueError("ZIP does not contain metadata.txt")
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
    print(f"Built and validated {destination} (plugin version {version})")


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"GISPHN_Flexible_Scan_QGIS_{validate_source()}.zip"
    build_archive(output)
