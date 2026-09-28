#!/usr/bin/env python3
"""Bump rushteam/homebrew-tap's AiOpt cask from the latest published Release.

The cask body comes from rushteam/aiopt packaging/homebrew/aiopt.rb on main.
Only `version` and `sha256` are filled in here. A newer Release whose dmg
sha256 matches GitHub's asset digest, and whose cask body otherwise matches
the tap, is written over Casks/aiopt.rb for a direct commit. Any other
difference is written the same way but reported as a review, so the workflow
opens a pull request instead of pushing to main. Drafts, prereleases, and a
Release older than the tap are ignored.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

AIOPT_REPO = "rushteam/aiopt"
CASK_PATH = "packaging/homebrew/aiopt.rb"
ASSET_TEMPLATE = "AiOpt-{version}-arm64.dmg"
MAX_DMG_BYTES = 400 * 1024 * 1024

VERSION_LINE = re.compile(r'^([ \t]*)version[ \t]+"([^"]+)"[ \t]*$', re.M)
SHA_LINE = re.compile(r'^([ \t]*)sha256[ \t]+"([0-9a-fA-F]{64})"[ \t]*$', re.M)
STABLE = re.compile(r"^\d+\.\d+\.\d+$")


class SyncError(Exception):
    pass


def parse_stable(version: str) -> tuple[int, int, int]:
    if not STABLE.fullmatch(version):
        raise SyncError(f"not a stable version: {version}")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def replace_field(pattern: re.Pattern[str], text: str, keyword: str, value: str) -> str:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SyncError(f"expected one {keyword} line, found {len(matches)}")
    match = matches[0]
    indent = match.group(1)
    return text[: match.start()] + f'{indent}{keyword} "{value}"' + text[match.end() :]


def cask_version(text: str) -> str:
    matches = list(VERSION_LINE.finditer(text))
    if len(matches) != 1:
        raise SyncError(f"expected one version line, found {len(matches)}")
    return matches[0].group(2)


def cask_sha(text: str) -> str:
    matches = list(SHA_LINE.finditer(text))
    if len(matches) != 1:
        raise SyncError(f"expected one sha256 line, found {len(matches)}")
    return matches[0].group(2).lower()


def fill(template: str, version: str, sha: str) -> str:
    parse_stable(version)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SyncError("sha256 is not 64 hex characters")
    text = replace_field(VERSION_LINE, template, "version", version)
    return replace_field(SHA_LINE, text, "sha256", sha)


def normalize(text: str) -> str:
    text = replace_field(VERSION_LINE, text, "version", "VERSION")
    return replace_field(SHA_LINE, text, "sha256", "SHA")


def decide(current: str, template: str, version: str, sha: str) -> tuple[str, str | None]:
    """Return (mode, new_text). mode is older, current, bump, or review."""
    if parse_stable(version) < parse_stable(cask_version(current)):
        return "older", None
    new = fill(template, version, sha)
    if new == current:
        return "current", None
    if normalize(new) == normalize(current):
        return "bump", new
    return "review", new


def parse_tag(tag: str) -> str:
    if not tag.startswith("v"):
        raise SyncError(f"release tag has no v prefix: {tag}")
    version = tag[1:]
    parse_stable(version)
    return version


def parse_digest(digest: str | None) -> str:
    if not digest or not digest.startswith("sha256:"):
        raise SyncError("release asset has no sha256 digest")
    hexdigest = digest.removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", hexdigest):
        raise SyncError("release asset digest is not sha256")
    return hexdigest


def find_asset(assets: list[dict], version: str) -> dict:
    name = ASSET_TEMPLATE.format(version=version)
    matches = [asset for asset in assets if asset.get("name") == name]
    if len(matches) != 1:
        raise SyncError(f"expected one {name} asset, found {len(matches)}")
    return matches[0]


def gh_json(path: str) -> dict:
    result = subprocess.run(
        ["gh", "api", path],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SyncError(f"gh api {path} failed: {detail}")
    return json.loads(result.stdout)


def fetch_template() -> str:
    meta = gh_json(f"repos/{AIOPT_REPO}/contents/{CASK_PATH}?ref=main")
    content = meta.get("content")
    if not isinstance(content, str):
        raise SyncError("cask template response has no content")
    return base64.b64decode(content).decode("utf-8")


def download_asset(asset_id: int, dest: str) -> str:
    with open(dest, "wb") as handle:
        result = subprocess.run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/octet-stream",
                f"repos/{AIOPT_REPO}/releases/assets/{asset_id}",
            ],
            check=False,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SyncError(f"asset download failed: {detail}")
    size = os.path.getsize(dest)
    if size == 0 or size > MAX_DMG_BYTES:
        raise SyncError(f"asset size {size} is outside the expected range")
    digest = hashlib.sha256()
    with open(dest, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def emit(mode: str, version: str) -> None:
    print(f"mode={mode}")
    if version:
        print(f"version={version}")
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"mode={mode}\n")
        handle.write(f"version={version}\n")


def sync(cask_file: str, dry_run: bool) -> int:
    release = gh_json(f"repos/{AIOPT_REPO}/releases/latest")
    if release.get("draft") or release.get("prerelease"):
        raise SyncError("latest release is a draft or prerelease")
    version = parse_tag(release.get("tag_name") or "")
    asset = find_asset(release.get("assets") or [], version)
    digest = parse_digest(asset.get("digest"))
    template = fetch_template()
    current = open(cask_file, encoding="utf-8").read()

    # Same version and the cask checksum already matches GitHub's digest: the
    # bytes were verified when that checksum was written. Skip the dmg download.
    if version == cask_version(current) and digest == cask_sha(current):
        mode, new = decide(current, template, version, digest)
        if mode in ("current", "older"):
            emit(mode, version)
            return 0
    else:
        with tempfile.TemporaryDirectory() as tmp:
            downloaded = download_asset(int(asset["id"]), os.path.join(tmp, "AiOpt.dmg"))
        if downloaded != digest:
            raise SyncError("downloaded dmg sha256 does not match the asset digest")
        mode, new = decide(current, template, version, downloaded)

    if mode in ("current", "older") or new is None:
        emit(mode, version)
        return 0
    print(f"would write {cask_file} ({mode})")
    if not dry_run:
        with open(cask_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(new)
    emit(mode, version)
    return 0


def self_test() -> None:
    current = 'cask "aiopt" do\n  version "1.0.2"\n  sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n  url "https://example.invalid/v#{version}"\nend\n'
    sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    template = current
    mode, new = decide(current, template, "1.0.3", sha)
    assert mode == "bump" and new is not None
    assert 'version "1.0.3"' in new and f'sha256 "{sha}"' in new
    assert "example.invalid" in new
    assert decide(new, template, "1.0.3", sha)[0] == "current"
    assert decide(new, template, "1.0.2", "a" * 64)[0] == "older"
    changed = template.replace("example.invalid", "elsewhere.invalid")
    mode, reviewed = decide(current, changed, "1.0.3", sha)
    assert mode == "review" and reviewed is not None and "elsewhere.invalid" in reviewed
    same_sha = "a" * 64
    mode, fixed = decide(current, template, "1.0.2", sha)
    assert mode == "bump" and fixed is not None and f'sha256 "{sha}"' in fixed
    assert decide(current, template, "1.0.2", same_sha)[0] == "current"
    try:
        fill(current + '  version "9.9.9"\n', "1.0.3", sha)
        raise AssertionError("two version lines should fail")
    except SyncError:
        pass
    assert parse_tag("v1.0.5") == "1.0.5"
    try:
        parse_tag("v1.0.5-rc.1")
        raise AssertionError("prerelease tag should fail")
    except SyncError:
        pass
    assert parse_digest("sha256:" + sha) == sha
    asset = find_asset([{"name": "AiOpt-1.0.3-arm64.dmg", "id": 1}], "1.0.3")
    assert asset["id"] == 1
    try:
        find_asset([{"name": "AiOpt-1.0.3-arm64.dmg"}, {"name": "other"}], "1.0.2")
        raise AssertionError("missing asset should fail")
    except SyncError:
        pass
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cask", default="Casks/aiopt.rb")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        return sync(args.cask, args.dry_run)
    except (SyncError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
