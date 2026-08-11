#!/usr/bin/env python3
"""Exercise nested and self dependencies through the real Zed CLI.

The harness creates an isolated file registry, publishes real package artifacts,
and verifies normal/frozen installs while transitioning between symlink and copy
materialization. It intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Iterable, Mapping

CLEAN_ENV_PREFIXES = ("ZED_PKG_",)
MODULES_DIR = "zed_modules"
MANIFEST_FILE = ".zpkg.toml"
LOCK_FILE = ".zpkg.lock"


class HarnessError(RuntimeError):
    pass


def remove_path(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_table(dependencies: Mapping[str, str]) -> str:
    if not dependencies:
        return ""
    lines = ["", "[dependencies]"]
    for key, requirement in sorted(dependencies.items()):
        lines.append(f'"{key}" = "{requirement}"')
    return "\n".join(lines) + "\n"


def manifest_text(
    org: str,
    name: str,
    version: str,
    dependencies: Mapping[str, str],
) -> str:
    return (
        f'''[package]
org = "{org}"
name = "{name}"
version = "{version}"
description = "version-solver-e2e nested/self fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/version-solver-e2e"
'''
        + dependency_table(dependencies)
        + '''
[publish]
tag_format = "v{version}"
exclude = [
  ".git/**",
  ".zed/**",
  ".zpkg.lock",
  "zed_modules/**",
]
'''
    )


class Harness:
    def __init__(self, zed: Path, root: Path) -> None:
        self.zed = zed.resolve()
        self.root = root.resolve()
        self.registry = self.root / "registry"
        self.home = self.root / "zed-home"
        self.sources = self.root / "sources"
        self.registry.mkdir(parents=True)
        self.home.mkdir(parents=True)
        self.sources.mkdir(parents=True)
        self.registry_uri = self.registry.resolve().as_uri()
        self.evidence: dict[str, object] = {
            "zed": str(self.zed),
            "registry": self.registry_uri,
            "platform": sys.platform,
            "scenarios": {},
        }

    def run(
        self,
        arguments: Iterable[os.PathLike[str] | str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(argument) for argument in arguments]
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(CLEAN_ENV_PREFIXES)
        }
        environment["ZED_PKG_INTERACTIVE"] = "false"
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        print(f"$ {' '.join(command)}", flush=True)
        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
        if check and completed.returncode != 0:
            raise HarnessError(
                f"command failed with exit code {completed.returncode}: {' '.join(command)}"
            )
        return completed

    def zed_command(self, *arguments: str) -> list[str]:
        return [
            str(self.zed),
            "--registry",
            self.registry_uri,
            "--home",
            str(self.home),
            *arguments,
        ]

    def create_package(
        self,
        parent: Path,
        org: str,
        name: str,
        version: str,
        dependencies: Mapping[str, str],
        payload: str,
    ) -> Path:
        directory = parent / f"{org}-{name}-{version.replace('.', '-')}"
        remove_path(directory)
        directory.mkdir(parents=True)
        (directory / MANIFEST_FILE).write_text(
            manifest_text(org, name, version, dependencies), encoding="utf-8"
        )
        (directory / "payload.txt").write_text(payload, encoding="utf-8")
        (directory / "README.md").write_text(
            f"# {org}/{name}\n\nFixture {version}.\n", encoding="utf-8"
        )
        self.run(["git", "init", "-b", "main"], cwd=directory)
        self.run(["git", "config", "user.name", "zed-pkg-test"], cwd=directory)
        self.run(
            ["git", "config", "user.email", "zed-pkg-test@users.noreply.github.com"],
            cwd=directory,
        )
        self.run(["git", "add", MANIFEST_FILE, "README.md", "payload.txt"], cwd=directory)
        self.run(["git", "commit", "-m", f"fixture: {org}/{name}@{version}"], cwd=directory)
        return directory

    def install(self, project: Path, mode: str, *, frozen: bool = False) -> None:
        arguments = self.zed_command(
            "install", "--install-mode", mode, "--adapter", "none"
        )
        if frozen:
            arguments.append("--frozen")
        self.run(arguments, cwd=project)

    def publish(self, project: Path, *, resolve_dependencies: bool = True) -> None:
        if resolve_dependencies:
            self.install(project, "copy")
        self.run(
            self.zed_command("publish", "--skip-vcs-checks"),
            cwd=project,
        )

    @staticmethod
    def lock_entries(project: Path) -> dict[str, dict[str, object]]:
        lock_path = project / LOCK_FILE
        if not lock_path.is_file():
            raise HarnessError(f"lockfile was not created: {lock_path}")
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise HarnessError(f"unexpected lock version in {lock_path}: {data.get('version')!r}")
        entries = data.get("package", [])
        if not isinstance(entries, list):
            raise HarnessError(f"invalid package entries in {lock_path}")
        indexed: dict[str, dict[str, object]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise HarnessError(f"invalid package table in {lock_path}")
            package_id = f"{entry.get('org')}/{entry.get('name')}"
            if package_id in indexed:
                raise HarnessError(f"duplicate lock entry for {package_id}")
            indexed[package_id] = entry
        return indexed

    @staticmethod
    def package_path(project: Path, package_id: str) -> Path:
        org, name = package_id.split("/", 1)
        return project / MODULES_DIR / org / name

    @staticmethod
    def assert_payload(project: Path, package_id: str, expected: str) -> None:
        path = Harness.package_path(project, package_id) / "payload.txt"
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            raise HarnessError(
                f"payload mismatch for {package_id}: expected {expected!r}, got {actual!r}"
            )

    @staticmethod
    def assert_materialization(project: Path, package_id: str, mode: str) -> None:
        path = Harness.package_path(project, package_id)
        if not path.exists() and not path.is_symlink():
            raise HarnessError(f"package was not materialized: {path}")
        if mode == "symlink" and os.name != "nt":
            if not path.is_symlink():
                raise HarnessError(f"symlink mode created a non-link: {path}")
            target = Path(os.readlink(path))
            if not target.is_absolute():
                raise HarnessError(f"symlink target is not absolute: {path} -> {target}")
        else:
            if path.is_symlink() or not path.is_dir():
                raise HarnessError(f"copy mode did not create an independent directory: {path}")

    def test_nested_chain(self, depth: int = 16) -> None:
        published_root = self.sources / "nested"
        for index in reversed(range(depth)):
            name = f"nested-{index:02d}"
            dependencies: dict[str, str] = {}
            if index + 1 < depth:
                dependencies[f"acme/nested-{index + 1:02d}"] = "=1.0.0"
            package = self.create_package(
                published_root,
                "acme",
                name,
                "1.0.0",
                dependencies,
                f"nested-payload-{index:02d}\n",
            )
            self.publish(package)

        consumer = self.create_package(
            self.root / "consumers",
            "consumer",
            "nested-chain",
            "0.1.0",
            {"acme/nested-00": "=1.0.0"},
            "consumer\n",
        )
        expected = {f"acme/nested-{index:02d}" for index in range(depth)}

        self.install(consumer, "symlink")
        entries = self.lock_entries(consumer)
        if set(entries) != expected:
            raise HarnessError(
                f"nested lock coverage drift: expected {sorted(expected)}, got {sorted(entries)}"
            )
        for package_id in sorted(expected):
            self.assert_materialization(consumer, package_id, "symlink")
        self.assert_payload(consumer, "acme/nested-00", "nested-payload-00\n")
        self.assert_payload(
            consumer, f"acme/nested-{depth - 1:02d}", f"nested-payload-{depth - 1:02d}\n"
        )
        lock_path = consumer / LOCK_FILE
        lock_digest = sha256(lock_path)

        self.install(consumer, "copy", frozen=True)
        if sha256(lock_path) != lock_digest:
            raise HarnessError("frozen symlink-to-copy transition changed the lockfile")
        for package_id in sorted(expected):
            self.assert_materialization(consumer, package_id, "copy")

        remove_path(self.home)
        remove_path(consumer / MODULES_DIR)
        self.home.mkdir(parents=True)
        self.install(consumer, "copy", frozen=True)
        if sha256(lock_path) != lock_digest:
            raise HarnessError("cold frozen copy replay changed the lockfile")
        self.assert_payload(
            consumer, f"acme/nested-{depth - 1:02d}", f"nested-payload-{depth - 1:02d}\n"
        )

        self.install(consumer, "symlink", frozen=True)
        if sha256(lock_path) != lock_digest:
            raise HarnessError("frozen copy-to-symlink transition changed the lockfile")
        for package_id in sorted(expected):
            self.assert_materialization(consumer, package_id, "symlink")

        self.evidence["scenarios"]["nested_chain"] = {
            "depth": depth,
            "lock_sha256": lock_digest,
            "packages": sorted(expected),
        }

    def test_published_self_loop(self) -> None:
        package = self.create_package(
            self.sources / "self-loop",
            "acme",
            "self-loop",
            "1.0.0",
            {"acme/self-loop": "=1.0.0"},
            "published-self-loop\n",
        )
        # The artifact must exist before its self-edge can be resolved. Publish
        # the manifest directly; the consumer install exercises the loop.
        self.publish(package, resolve_dependencies=False)

        consumer = self.create_package(
            self.root / "consumers",
            "consumer",
            "self-loop",
            "0.1.0",
            {"acme/self-loop": "=1.0.0"},
            "consumer\n",
        )
        self.install(consumer, "symlink")
        entries = self.lock_entries(consumer)
        if set(entries) != {"acme/self-loop"}:
            raise HarnessError(f"self-loop resolved more than once: {sorted(entries)}")
        if entries["acme/self-loop"].get("version") != "1.0.0":
            raise HarnessError(f"unexpected self-loop version: {entries['acme/self-loop']}")
        self.assert_materialization(consumer, "acme/self-loop", "symlink")
        self.assert_payload(consumer, "acme/self-loop", "published-self-loop\n")
        lock_path = consumer / LOCK_FILE
        lock_digest = sha256(lock_path)

        self.install(consumer, "copy", frozen=True)
        if sha256(lock_path) != lock_digest:
            raise HarnessError("frozen self-loop copy transition changed the lockfile")
        self.assert_materialization(consumer, "acme/self-loop", "copy")
        self.assert_payload(consumer, "acme/self-loop", "published-self-loop\n")

        self.evidence["scenarios"]["published_self_loop"] = {
            "lock_sha256": lock_digest,
            "packages": sorted(entries),
        }

    def write_workspace_root(self, workspace: Path) -> None:
        workspace.mkdir(parents=True)
        (workspace / MANIFEST_FILE).write_text(
            '''[package]
org = "workspace"
name = "published-self-test"
version = "0.0.0"
description = "workspace root for published self-test"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/version-solver-e2e"

[workspace]
members = ["packages/*"]
''',
            encoding="utf-8",
        )

    def test_workspace_published_self(self) -> None:
        published = self.create_package(
            self.sources / "workspace-self",
            "acme",
            "self-test",
            "1.0.0",
            {},
            "published-v1\n",
        )
        self.publish(published)

        workspace = self.root / "workspace"
        self.write_workspace_root(workspace)
        control = self.create_package(
            workspace / "packages",
            "acme",
            "workspace-control",
            "1.0.0",
            {},
            "workspace-control\n",
        )
        member = self.create_package(
            workspace / "packages",
            "acme",
            "self-test",
            "2.0.0",
            {
                "acme/self-test": "=1.0.0",
                "acme/workspace-control": "=1.0.0",
            },
            "workspace-v2\n",
        )

        self.install(member, "symlink")
        entries = self.lock_entries(member)
        if set(entries) != {"acme/self-test"}:
            raise HarnessError(
                "workspace source dependency leaked into the registry lock: "
                f"{sorted(entries)}"
            )
        self.assert_payload(member, "acme/self-test", "published-v1\n")
        self.assert_payload(member, "acme/workspace-control", "workspace-control\n")
        self.assert_materialization(member, "acme/self-test", "symlink")
        self.assert_materialization(member, "acme/workspace-control", "symlink")

        installed_self = self.package_path(member, "acme/self-test")
        installed_control = self.package_path(member, "acme/workspace-control")
        if os.name != "nt":
            if installed_self.resolve() == member.resolve():
                raise HarnessError("published self-test silently linked back to workspace source")
            if installed_control.resolve() != control.resolve():
                raise HarnessError("ordinary workspace dependency was not source-linked")

        lock_path = member / LOCK_FILE
        lock_digest = sha256(lock_path)
        self.install(member, "copy", frozen=True)
        if sha256(lock_path) != lock_digest:
            raise HarnessError("workspace frozen copy transition changed the lockfile")
        self.assert_materialization(member, "acme/self-test", "copy")
        self.assert_materialization(member, "acme/workspace-control", "copy")
        self.assert_payload(member, "acme/self-test", "published-v1\n")
        self.assert_payload(member, "acme/workspace-control", "workspace-control\n")

        self.evidence["scenarios"]["workspace_published_self"] = {
            "lock_sha256": lock_digest,
            "registry_packages": sorted(entries),
            "workspace_source": str(control.resolve()),
        }


def resolve_zed(directory: Path) -> Path:
    candidate = directory / ("zed.exe" if os.name == "nt" else "zed")
    if not candidate.is_file():
        raise HarnessError(f"compiled Zed binary not found: {candidate}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--zed-dir",
        type=Path,
        required=True,
        help="Directory containing the compiled zed or zed.exe binary",
    )
    parser.add_argument("--depth", type=int, default=16)
    arguments = parser.parse_args()
    if arguments.depth < 2:
        parser.error("--depth must be at least 2")

    zed = resolve_zed(arguments.zed_dir)
    with tempfile.TemporaryDirectory(prefix="zed-nested-self-e2e-") as temporary:
        harness = Harness(zed, Path(temporary))
        harness.test_nested_chain(arguments.depth)
        harness.test_published_self_loop()
        harness.test_workspace_published_self()
        print("\n=== immutable regression evidence ===")
        print(json.dumps(harness.evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"nested/self regression failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
