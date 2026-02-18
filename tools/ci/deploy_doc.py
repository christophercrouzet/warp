# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deploy versioned Sphinx documentation to GitHub Pages.

Takes the HTML output from a Sphinx build (in ``docs/_build/html/``) and
commits it to the local ``gh-pages`` branch under a versioned folder
(e.g. ``/latest/``, ``/v1.10/``).  The caller is responsible for pushing
the branch to the remote.

The script also maintains:
  - /stable/        full copy of the highest MAJOR.MINOR version
  - /versions.json  version switcher data for the PyData Sphinx theme
  - /index.html     redirect to /stable/ (or /latest/ if no releases exist)
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import List, Optional

BASE_URL = "https://nvidia.github.io/warp"
HTML_DIR = Path("docs/_build/html")

# Sphinx writes pickled doctrees to ``<outdir>/.doctrees/`` by default — large
# binary state (env.pickle alone is ~10 MB, with absolute paths embedded so it
# regenerates with different bytes every build). No user agent ever loads it,
# so excluding it from the deploy keeps gh-pages from accumulating tens of
# megabytes of churn per push (the failure mode that bloated newton-physics).
# ``__pycache__`` shows up in builds that import generators with stale .pyc.
_DEPLOY_EXCLUDE = shutil.ignore_patterns(".doctrees", "__pycache__", "*.pyc")


# Git Helpers
# ---------------------------------------------------------------------------

def git_run_cmd(*args: str, cwd: Optional[Path] = None) -> str:
    """Run a git command, printing it for CI visibility.

    Returns stdout. Raises ``subprocess.CalledProcessError`` on non-zero exit;
    stderr is printed before the exception is raised so the underlying error
    is visible (the default ``capture_output=True`` would otherwise swallow it).
    """
    cmd = ("git", *args)
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, flush=True)
        result.check_returncode()
    return result.stdout.strip()


# Version Helpers
# ---------------------------------------------------------------------------

def resolve_version_folder(version: str) -> str:
    """Map a --version value to the target folder name on gh-pages.

    "latest" -> "latest", "1.10" -> "v1.10".
    """
    if version == "latest":
        return "latest"

    return f"v{version}"


def discover_versions(gh_pages_dir: Path) -> List[str]:
    """Return deployed MAJOR.MINOR versions sorted highest-first."""
    versions = []
    for entry in gh_pages_dir.iterdir():
        if entry.is_dir():
            m = re.match(r"^v(\d+\.\d+)$", entry.name)
            if m:
                versions.append(m.group(1))

    versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
    return versions


# Content Generation
# ---------------------------------------------------------------------------

def generate_versions_json(gh_pages_dir: Path, versions: List[str], has_latest: bool) -> None:
    """Write versions.json for the PyData Sphinx theme version switcher."""
    entries = []

    if has_latest:
        entries.append({
            "name": "latest (main)",
            "version": "latest",
            "url": f"{BASE_URL}/latest/",
        })

    for i, ver in enumerate(versions):
        entry: dict = {
            "version": ver,
            "url": f"{BASE_URL}/v{ver}/",
        }
        if i == 0:
            entry["name"] = f"{ver} (stable)"
            entry["preferred"] = True
        else:
            entry["name"] = ver
        entries.append(entry)

    path = gh_pages_dir / "versions.json"
    path.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"Wrote {path} with {len(entries)} entries.")


def generate_root_redirect(gh_pages_dir: Path, target: str) -> None:
    """Write a root index.html that redirects to *target* (e.g. "stable/")."""
    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="refresh" content="0; url={target}" />
  <script>window.location.href = "{target}";</script>
</head>
<body>
  <p>Redirecting to <a href="{target}">{target}</a>...</p>
</body>
</html>
"""
    (gh_pages_dir / "index.html").write_text(html)
    print(f"Root redirect -> {target}")


def generate_404_redirect(gh_pages_dir: Path, target: str) -> None:
    """Write 404.html that rewrites pre-versioning paths under *target*.

    GitHub Pages has no server-side redirect mechanism: it serves /404.html
    for any path that doesn't resolve. This catches old bookmarks like
    ``/user_guide/installation.html`` and rewrites them to
    ``/<target>user_guide/installation.html``. Paths already under a known
    version folder (``stable/``, ``latest/``, ``vX.Y/``) fall through to the
    default 404 to avoid redirect loops on genuinely missing pages.
    """
    # Project Pages live under a path prefix (e.g. "/warp/"); strip it before
    # matching so the version-folder regex sees a relative path.
    prefix = urllib.parse.urlparse(BASE_URL).path.rstrip("/") + "/"

    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Redirecting...</title>
  <script>
(function () {{
  var prefix = {json.dumps(prefix)};
  var path = location.pathname;
  if (path.indexOf(prefix) === 0) {{
    path = path.slice(prefix.length);
  }}
  if (/^(stable|latest|v\\d+\\.\\d+)(\\/|$)/.test(path)) {{
    return;
  }}
  location.replace(prefix + {json.dumps(target)} + path + location.search + location.hash);
}})();
  </script>
</head>
<body>
  <p>Redirecting to <a href="{target}">{target}</a>...</p>
</body>
</html>
"""
    (gh_pages_dir / "404.html").write_text(html)
    print(f"404 fallback -> {target}")


def update_stable(gh_pages_dir: Path, stable_version: str) -> None:
    """Replace /stable/ with a full copy of the highest version folder.

    We deliberately do a real recursive copy (not redirect stubs): downstream
    Sphinx projects use ``intersphinx_mapping`` against
    ``https://nvidia.github.io/warp/stable/objects.inv``, and pytorch/pytorch#182007
    showed what happens when an alias drops ``objects.inv`` /
    ``searchindex.js`` / ``.buildinfo`` — every dependent project's docs build
    breaks. The ``.doctrees`` exclusion in ``_DEPLOY_EXCLUDE`` already
    happened upstream of here (the v{X.Y} folder doesn't contain them).
    """
    stable_dir = gh_pages_dir / "stable"
    source_dir = gh_pages_dir / f"v{stable_version}"

    if stable_dir.exists():
        shutil.rmtree(stable_dir)
    shutil.copytree(source_dir, stable_dir)
    print(f"/stable/ -> v{stable_version}")


# Main
# ---------------------------------------------------------------------------

def run(version: str) -> None:
    folder = resolve_version_folder(version)
    print(f"Deploying to /{folder}/")

    if not HTML_DIR.exists():
        raise FileNotFoundError(f"Built docs not found at {HTML_DIR}")

    repo_dir = Path(git_run_cmd("rev-parse", "--show-toplevel"))
    branch_exists = bool(git_run_cmd("branch", "--list", "gh-pages"))

    # Refuse to silently orphan when origin already has a gh-pages branch.
    # Without this, a local run on a fresh clone would create an empty branch,
    # the workflow's `git push --force origin gh-pages` would then wipe history.
    if not branch_exists:
        remote = subprocess.run(
            ("git", "ls-remote", "--heads", "origin", "gh-pages"),
            capture_output=True, text=True, check=False,
        )
        if remote.returncode == 0 and remote.stdout.strip():
            raise RuntimeError(
                "origin has a gh-pages branch but it is not present locally. "
                "Refusing to orphan and overwrite remote history. Fetch first:\n"
                "  git fetch origin gh-pages:gh-pages"
            )

    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp) / "gh-pages"
        gh.mkdir(parents=True)
        git_run_cmd("init", str(gh))

        # 1. Populate the working directory from the existing gh-pages branch,
        #    or start fresh.
        if branch_exists:
            git_run_cmd("fetch", str(repo_dir), "gh-pages", cwd=gh)
            git_run_cmd("checkout", "FETCH_HEAD", cwd=gh)
            git_run_cmd("checkout", "-B", "gh-pages", cwd=gh)
        else:
            git_run_cmd("checkout", "--orphan", "gh-pages", cwd=gh)

        git_run_cmd("config", "user.email", "actions@github.com", cwd=gh)
        git_run_cmd("config", "user.name", "GitHub Actions", cwd=gh)
        # Auto-generated build artifacts; signing them would require a key in
        # the CI runner / local agent. Local-only config: doesn't touch the
        # operator's global `commit.gpgsign`.
        git_run_cmd("config", "commit.gpgsign", "false", cwd=gh)

        # 2. Deploy the built HTML into the target version folder.
        target = gh / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(HTML_DIR, target, ignore=_DEPLOY_EXCLUDE)
        print(f"Copied {HTML_DIR} -> {target}")

        # 3. Discover all deployed release versions.
        versions = discover_versions(gh)
        has_latest = (gh / "latest").is_dir()
        print(f"Deployed versions: {versions}  (has latest: {has_latest})")

        # 4. Update /stable/ and root + 404 redirects.
        if versions:
            update_stable(gh, versions[0])
            generate_root_redirect(gh, "stable/")
            generate_404_redirect(gh, "stable/")
        else:
            stable_dir = gh / "stable"
            if stable_dir.exists():
                shutil.rmtree(stable_dir)
                print("Removed stale /stable/ (no versioned folders left)")
            if has_latest:
                generate_root_redirect(gh, "latest/")
                generate_404_redirect(gh, "latest/")

        # 5. Generate versions.json for the version switcher.
        generate_versions_json(gh, versions, has_latest)

        # 6. Ensure .nojekyll exists at the root.
        (gh / ".nojekyll").touch()

        # 7. Commit and update the local gh-pages branch.
        git_run_cmd("add", "-A", cwd=gh)
        if not git_run_cmd("status", "--porcelain", cwd=gh):
            print("No changes to commit.")
            return

        git_run_cmd("commit", "-m", f"Deploy docs: {folder}", cwd=gh)
        git_run_cmd("fetch", str(gh), "+gh-pages:gh-pages", cwd=repo_dir)

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deploy versioned Sphinx documentation to GitHub Pages.",
    )
    parser.add_argument(
        "--version",
        required=True,
        help=(
            "Version identifier for this deployment. "
            "'latest' deploys to /latest/; a MAJOR.MINOR string (e.g. '1.10') "
            "deploys to /vMAJOR.MINOR/."
        ),
    )
    args = parser.parse_args()
    try:
        run(args.version)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
