#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
import re
from typing import Dict, List

VERSION_DIR_RE = re.compile(r"^v\d+\.\d+(\.\d+)?")


def ensure_trailing_slash(path: str) -> str:
    return path if path.endswith("/") else f"{path}/"


def version_sort_key(version: str) -> tuple:
    cleaned = version.lstrip("v")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", cleaned)
    if not match:
        return (-1, -1, -1, -1, cleaned)

    major = int(match.group(1) or 0)
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    suffix = match.group(4) or ""
    is_release = 1 if suffix == "" else 0
    return (major, minor, patch, is_release, suffix)


def load_versions(output_path: str) -> Dict[str, dict]:
    if not os.path.exists(output_path):
        return {}

    with open(output_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    entries: Dict[str, dict] = {}
    for entry in data.get("versions", []):
        if not isinstance(entry, dict):
            continue
        version = entry.get("version")
        if not version:
            continue
        entry = dict(entry)
        entry["path"] = ensure_trailing_slash(entry.get("path") or entry.get("url") or f"{version}/")
        entries[version] = entry

    return entries


def discover_versions(root_dir: str, entries: Dict[str, dict]) -> None:
    if not os.path.isdir(root_dir):
        return

    for name in os.listdir(root_dir):
        if not VERSION_DIR_RE.match(name):
            continue
        version_path = os.path.join(root_dir, name)
        if not os.path.isdir(version_path):
            continue
        if not os.path.exists(os.path.join(version_path, "index.html")):
            continue
        entries.setdefault(name, {"version": name, "path": ensure_trailing_slash(name)})


def write_versions(output_path: str, versions: List[dict]) -> None:
    payload = {
        "latest": versions[0]["version"] if versions else None,
        "versions": versions,
    }

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update versions.json for docs publishing.")
    parser.add_argument("--version", required=True, help="Version tag to publish (e.g. v1.2.3).")
    parser.add_argument("--output", default="versions.json", help="Path to versions.json in gh-pages.")
    args = parser.parse_args()

    output_path = os.path.abspath(args.output)
    root_dir = os.path.dirname(output_path)

    entries = load_versions(output_path)
    discover_versions(root_dir, entries)

    entry = entries.get(args.version, {"version": args.version})
    entry["path"] = ensure_trailing_slash(entry.get("path") or f"{args.version}/")
    entries[args.version] = entry

    versions = sorted(entries.values(), key=lambda item: version_sort_key(item["version"]), reverse=True)
    write_versions(output_path, versions)


if __name__ == "__main__":
    main()
