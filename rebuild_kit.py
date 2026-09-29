#!/usr/bin/env python3
"""Daily rebuild-kit snapshots for a set of services.

Writes a dated folder locally, verifies it, mirrors it to a dock USB,
and can copy the latest verified day to a second Friday stick.

This is a rebuild kit, not a second git history. Source files keep their
original names. Secrets and dependency/build folders are skipped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

VERSION = "1.2.0"
DAY_FMT = "%Y-%m-%d"
ROLE_FILE = "REBUILD-KIT-ROLE.txt"

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vs",
    ".venv",
    "venv",
    "node_modules",
    "bower_components",
    "vendor",
    "dist",
    "build",
    "target",
    "out",
    "coverage",
    "__pycache__",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "bin",
    "obj",
    ".gradle",
    "pods",
}

DEFAULT_EXCLUDE_GLOBS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.kdbx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_dsa",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "*.secret",
    ".npmrc",
    ".pypirc",
]

TEXT_EXTENSIONS = {
    ".cs", ".csproj", ".sln", ".fs", ".vb",
    ".java", ".kt", ".kts", ".gradle", ".scala",
    ".py", ".pyi", ".rb", ".php", ".go", ".rs", ".swift",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".json", ".jsonc", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".htm", ".css", ".scss", ".less", ".svg",
    ".md", ".txt", ".rst", ".csv", ".tsv",
    ".sql", ".proto", ".graphql", ".gql",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".dockerfile", ".env.example",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".mm",
    ".lua", ".pl", ".r", ".dart", ".ex", ".exs", ".erl",
    ".tf", ".tfvars", ".hcl",
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
}

TEXT_NAMES = {
    "dockerfile", "makefile", "gemfile", "rakefile", "procfile",
    "license", "readme", "changelog", "authors", "notice",
    "requirements.txt", "pipfile", "go.mod", "go.sum",
    "cargo.toml", "cargo.lock", "package.json", "composer.json",
}


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(path: Path | None) -> dict:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a JSON object: {path}")
    return data


def exclude_dirs(config: dict) -> set[str]:
    extra = config.get("exclude_dirs") or []
    return set(DEFAULT_EXCLUDE_DIRS) | {str(name) for name in extra}


def exclude_globs(config: dict) -> list[str]:
    extra = config.get("exclude_globs") or []
    return DEFAULT_EXCLUDE_GLOBS + [str(pattern) for pattern in extra]


def is_secret(name: str, globs: list[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in globs)


def looks_text(path: Path) -> bool:
    name = path.name.lower()
    if name in TEXT_NAMES or path.suffix.lower() in TEXT_EXTENSIONS:
        try:
            with path.open("rb") as handle:
                sample = handle.read(8192)
        except OSError:
            return False
        return b"\0" not in sample
    return False


def safe_relative(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise SystemExit(f"refusing to snapshot outside source root: {path}")
    return resolved.relative_to(root_resolved)


def build_tree(rel_paths: list[str]) -> str:
    root: dict = {}
    for rel in rel_paths:
        node = root
        parts = Path(rel).parts
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None

    lines = ["."]

    def walk(node: dict, prefix: str) -> None:
        names = sorted(node, key=lambda item: (node[item] is None, item.lower()))
        for index, name in enumerate(names):
            last = index == len(names) - 1
            branch = "└── " if last else "├── "
            lines.append(prefix + branch + name)
            child = node[name]
            if isinstance(child, dict):
                walk(child, prefix + ("    " if last else "│   "))

    walk(root, "")
    return "\n".join(lines) + "\n"


def write_bundle(source_root: Path, entries: list[dict], bundle_path: Path, max_bytes: int) -> int:
    included = 0
    with bundle_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# rebuild-kit LLM bundle\n")
        handle.write("# Text files only. Original source/ is the restore copy.\n\n")
        for entry in entries:
            if not entry.get("text"):
                continue
            if entry["size"] > max_bytes:
                handle.write(f"\n----- SKIP {entry['path']} (over bundle cap) -----\n")
                continue
            handle.write(f"\n----- FILE {entry['path']} -----\n")
            text = (source_root / entry["path"]).read_text(encoding="utf-8", errors="replace")
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            included += 1
    return included


def common_root(paths: list[Path]) -> Path:
    resolved = [path.resolve() for path in paths]
    if len(resolved) == 1:
        return resolved[0]
    shared = Path(os.path.commonpath([str(path) for path in resolved]))
    if shared.is_file():
        shared = shared.parent
    return shared


def covering_root(paths: list[Path]) -> Path | None:
    """A selected path that already contains every other selected path."""
    resolved = [path.resolve() for path in paths]
    for path in resolved:
        if all(other == path or other.is_relative_to(path) for other in resolved):
            return path
    return None


def snapshot_workspace(entries_in: list[dict], day_dir: Path, config: dict) -> dict:
    """Copy every selected path under one root and append each path to MAP.txt.

    A selected top root is mapped and copied whole. Otherwise every selected
    path is copied under their shared parent. Routes, middleware, and other
    shared paths are appended as their own trees in the map.
    """
    if not entries_in:
        raise SystemExit("add a top root, or the service paths to snapshot")
    labeled = []
    for item in entries_in:
        source = Path(item["path"]).expanduser().resolve()
        if not source.is_dir():
            raise SystemExit(f"path is not a directory: {source}")
        labeled.append({
            "name": item.get("name") or source.name,
            "kind": item.get("kind") or "service",
            "path": source,
        })

    top = covering_root([item["path"] for item in labeled])
    root = top or common_root([item["path"] for item in labeled])
    source_out = day_dir / "source"
    source_out.mkdir(parents=True, exist_ok=True)

    max_file = int(config.get("max_file_bytes", 20 * 1024 * 1024))
    max_bundle = int(config.get("max_bundle_file_bytes", 500 * 1024))
    dir_names = exclude_dirs(config)
    globs = exclude_globs(config)
    files = []
    skipped = []
    seen = set()
    copy_roots = [top] if top is not None else [item["path"] for item in labeled]
    for copy_root in copy_roots:
        for full in walk_source(copy_root, dir_names, globs, skipped):
            rel = safe_relative(full, root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            size = full.stat().st_size
            if size > max_file:
                skipped.append(f"{full} (over {max_file} bytes)")
                continue
            dest = source_out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)
            files.append({
                "path": rel,
                "size": size,
                "sha256": sha256_file(dest),
                "text": looks_text(dest),
            })

    files.sort(key=lambda item: item["path"])
    sections = []
    map_lines = [
        f"# rebuild-kit map",
        f"root: {root}",
        f"mode: {'top-root' if top is not None else 'shared-root'}",
        "",
    ]
    for item in labeled:
        prefix = "." if item["path"] == root else safe_relative(item["path"], root).as_posix()
        owned = []
        for entry in files:
            if prefix == "." or entry["path"] == prefix or entry["path"].startswith(prefix + "/"):
                owned.append(entry["path"] if prefix == "." else str(Path(entry["path"]).relative_to(prefix)))
        tree = build_tree(owned)
        sections.append({"name": item["name"], "kind": item["kind"], "path": str(item["path"]), "relative": prefix, "file_count": len(owned)})
        map_lines.append(f"## {item['kind']} {item['name']}")
        map_lines.append(f"source: {item['path']}")
        map_lines.append(f"relative: {prefix}")
        map_lines.append(tree.rstrip())
        map_lines.append("")
    (day_dir / "MAP.txt").write_text("\n".join(map_lines) + "\n", encoding="utf-8")
    included = write_bundle(source_out, files, day_dir / "BUNDLE.llm.txt", max_bundle)
    manifest = {
        "tool": "rebuild-kit",
        "version": VERSION,
        "service": root.name,
        "root": str(root),
        "mode": "top-root" if top is not None else "shared-root",
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "file_count": len(files),
        "bundle_file_count": included,
        "skipped_count": len(skipped),
        "paths": sections,
        "files": files,
        "skipped": skipped,
    }
    (day_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (day_dir / "README.restore.md").write_text(restore_readme(root.name, root, manifest), encoding="utf-8")
    print(f"mapped {root} ({manifest['mode']}, {len(files)} files, {len(sections)} trees)")
    return {"name": root.name, "file_count": len(files), "skipped_count": len(skipped), "bundle_file_count": included, "paths": sections}


def snapshot_service(
    name: str,
    source: Path,
    day_dir: Path,
    config: dict,
) -> dict:
    if not source.is_dir():
        raise SystemExit(f"source is not a directory: {source}")
    service_dir = day_dir / name
    source_out = service_dir / "source"
    source_out.mkdir(parents=True, exist_ok=True)

    max_file = int(config.get("max_file_bytes", 20 * 1024 * 1024))
    max_bundle = int(config.get("max_bundle_file_bytes", 500 * 1024))
    dir_names = exclude_dirs(config)
    globs = exclude_globs(config)

    entries = []
    skipped = []
    for full in walk_source(source, dir_names, globs, skipped):
        rel = safe_relative(full, source).as_posix()
        size = full.stat().st_size
        if size > max_file:
            skipped.append(f"{full} (over {max_file} bytes)")
            continue
        dest = source_out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full, dest)
        digest = sha256_file(dest)
        entries.append({
            "path": rel,
            "size": size,
            "sha256": digest,
            "text": looks_text(dest),
        })

    entries.sort(key=lambda item: item["path"])
    tree = build_tree([item["path"] for item in entries])
    (service_dir / "TREE.txt").write_text(tree, encoding="utf-8")
    included = write_bundle(source_out, entries, service_dir / "BUNDLE.llm.txt", max_bundle)

    manifest = {
        "tool": "rebuild-kit",
        "version": VERSION,
        "service": name,
        "source": str(source.resolve()),
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "file_count": len(entries),
        "bundle_file_count": included,
        "skipped_count": len(skipped),
        "files": entries,
        "skipped": skipped,
    }
    (service_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (service_dir / "README.restore.md").write_text(
        restore_readme(name, source, manifest),
        encoding="utf-8",
    )
    return {
        "name": name,
        "file_count": len(entries),
        "skipped_count": len(skipped),
        "bundle_file_count": included,
    }


def walk_source(root: Path, dir_names: set[str], globs: list[str], skipped: list[str]):
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept = []
        for dirname in list(dirs):
            if dirname in dir_names or is_secret(dirname, globs):
                skipped.append(str((current_path / dirname).resolve()) + "/")
                continue
            kept.append(dirname)
        dirs[:] = kept
        for filename in files:
            full = current_path / filename
            if is_secret(filename, globs):
                skipped.append(str(full.resolve()))
                continue
            try:
                mode = full.lstat().st_mode
            except OSError as exc:
                skipped.append(f"{full} ({exc})")
                continue
            if not stat.S_ISREG(mode):
                skipped.append(str(full.resolve()))
                continue
            yield full


def restore_readme(name: str, source: Path, manifest: dict) -> str:
    return f"""# Restore {name}

This folder is a rebuild snapshot, not a git history.

- `source/` is the restore copy. File names and extensions are unchanged.
- `TREE.txt` is the layout that was captured.
- `MANIFEST.json` has path, size, and sha256 for every copied file.
- `BUNDLE.llm.txt` is a text-only pack for an agent. Do not build from it.

Source at snapshot time: `{source.resolve()}`
Created: {manifest["created"]}
Files: {manifest["file_count"]}
Skipped: {manifest["skipped_count"]}

## Restore

1. Copy `source/` to a working directory.
2. Run `python rebuild_kit.py verify` against this service folder before you trust it.
3. Reinstall dependencies. `node_modules`, `vendor`, `dist`, and build output were excluded.
4. Secrets were excluded. Restore env files and keys from the secret store, not from this kit.
5. Read the service's own README inside `source/` for the build and run commands.

## Verify

```bash
python rebuild_kit.py verify "{name}"
```
"""


def verify_tree(root: Path) -> tuple[int, int]:
    manifests = sorted(root.rglob("MANIFEST.json"))
    if not manifests:
        raise SystemExit(f"no MANIFEST.json under {root}")
    checked = 0
    failed = 0
    for manifest_path in manifests:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        service_dir = manifest_path.parent
        source_dir = service_dir / "source"
        for entry in data.get("files", []):
            checked += 1
            target = source_dir / entry["path"]
            if not target.is_file():
                failed += 1
                eprint(f"MISSING {target}")
                continue
            digest = sha256_file(target)
            if digest != entry["sha256"]:
                failed += 1
                eprint(f"MISMATCH {target}")
        print(f"verified {data.get('service', service_dir.name)}: {len(data.get('files', []))} files")
    return checked, failed


def copy_day(day_dir: Path, dest_root: Path) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / day_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(day_dir, dest)
    return dest


def prune(root: Path, keep: int) -> list[str]:
    if keep < 1 or not root.is_dir():
        return []
    days = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            dt.datetime.strptime(child.name, DAY_FMT)
        except ValueError:
            continue
        days.append(child)
    days.sort(key=lambda path: path.name)
    removed = []
    for old in days[:-keep]:
        shutil.rmtree(old)
        removed.append(old.name)
    return removed


def mounted_usb_drives() -> list[dict]:
    """Removable or USB mounts the operator can select."""
    import subprocess

    try:
        raw = subprocess.check_output(
            ["lsblk", "-J", "-o", "NAME,TRAN,RM,MOUNTPOINT,LABEL,SIZE,FSTYPE"],
            text=True,
        )
        payload = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        payload = {"blockdevices": []}

    found = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            mount = node.get("mountpoint")
            removable = str(node.get("rm")) == "1" or node.get("tran") == "usb"
            if mount and removable:
                found.append({
                    "mount": mount,
                    "label": node.get("label") or "",
                    "size": node.get("size") or "",
                    "name": node.get("name") or "",
                    "role": read_role(Path(mount)),
                })
            walk(node.get("children") or [])

    walk(payload.get("blockdevices") or [])
    return found


def read_role(mount: Path) -> str:
    marker = mount / ROLE_FILE
    if not marker.is_file():
        return ""
    return marker.read_text(encoding="utf-8").strip().upper()


def write_role(mount: Path, role: str) -> None:
    (mount / ROLE_FILE).write_text(role.upper() + "\n", encoding="utf-8")


def require_mounted_usb(path: Path, label: str) -> Path:
    mount = path.expanduser()
    if not mount.exists():
        raise SystemExit(f"{label} is not plugged in: {mount}")
    mount = mount.resolve()
    if not mount.is_dir():
        raise SystemExit(f"{label} is not a directory: {mount}")
    if not mount.is_mount():
        raise SystemExit(f"{label} is not a mounted drive: {mount}")
    drives = mounted_usb_drives()
    mounts = {item["mount"] for item in drives}
    if drives and str(mount) not in mounts and mount.as_posix() not in mounts:
        raise SystemExit(f"{label} is mounted, but it is not a USB drive: {mount}")
    probe = mount / ".rebuild-kit-write-test"
    try:
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise SystemExit(f"{label} is not writable: {mount} ({exc})") from exc
    return mount


def select_mounts(count: int, purpose: str) -> list[Path]:
    drives = mounted_usb_drives()
    if len(drives) < count:
        listed = ", ".join(item["mount"] for item in drives) or "none"
        raise SystemExit(
            f"{purpose} needs {count} USB drive(s) plugged in. Mounted now: {listed}"
        )
    print(f"Select {count} USB drive(s) for {purpose}:")
    for index, item in enumerate(drives, start=1):
        role = f" role={item['role']}" if item["role"] else ""
        label = f" label={item['label']}" if item["label"] else ""
        print(f"  {index}. {item['mount']}{label} {item['size']}{role}")
    if not sys.stdin.isatty():
        raise SystemExit("no TTY to select a USB. Pass the mount path(s) explicitly.")
    picks = []
    while len(picks) < count:
        raw = input(f"drive {len(picks) + 1} of {count}: ").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= len(drives):
            print("pick a number from the list")
            continue
        chosen = Path(drives[int(raw) - 1]["mount"])
        if chosen in picks:
            print("pick a different drive")
            continue
        picks.append(chosen)
    return picks


def distinct_devices(paths: list[Path]) -> None:
    devices = [path.stat().st_dev for path in paths]
    if len(set(devices)) != len(paths):
        raise SystemExit("the selected paths are the same drive: " + ", ".join(map(str, paths)))


def latest_day(root: Path) -> Path:
    days = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            dt.datetime.strptime(child.name, DAY_FMT)
        except ValueError:
            continue
        days.append(child)
    if not days:
        raise SystemExit(f"no dated snapshots in {root}")
    return sorted(days, key=lambda path: path.name)[-1]


def services_from(args, config: dict) -> list[tuple[str, Path]]:
    if args.src:
        name = args.name or Path(args.src).resolve().name
        return [(name, Path(args.src).expanduser().resolve())]
    raw = config.get("services") or []
    if not raw:
        raise SystemExit("pass --src or a config with services")
    services = []
    for item in raw:
        services.append((item["name"], Path(item["path"]).expanduser().resolve()))
    return services


def cmd_snapshot(args, config: dict) -> int:
    out = Path(args.out).expanduser().resolve()
    if args.usb:
        usb = require_mounted_usb(Path(args.usb), "dock USB")
    else:
        usb = require_mounted_usb(select_mounts(1, "the daily dock copy")[0], "dock USB")
    if usb.stat().st_dev == out.stat().st_dev if out.exists() else False:
        raise SystemExit("dock USB must not be the same drive as --out")
    day = args.date or dt.date.today().isoformat()
    day_dir = out / day
    day_dir.mkdir(parents=True, exist_ok=True)
    selected = []
    if args.src:
        selected = [{"name": args.name or Path(args.src).name, "kind": "root", "path": args.src}]
    else:
        selected = config.get("services") or []
    print(f"snapshot {len(selected)} path(s)")
    summaries = [snapshot_workspace(selected, day_dir, config)]

    stamp = {
        "tool": "rebuild-kit",
        "version": VERSION,
        "day": day,
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "services": summaries,
    }
    (day_dir / "SNAPSHOT.json").write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    checked, failed = verify_tree(day_dir)
    if failed:
        eprint(f"local snapshot failed verification: {failed} problem(s)")
        return 1
    print(f"local snapshot ok: {day_dir} ({checked} files)")

    keep = int(config.get("keep_days", args.keep))
    removed = prune(out, keep)
    if removed:
        print("pruned local: " + ", ".join(removed))

    copied = copy_day(day_dir, usb)
    usb_checked, usb_failed = verify_tree(copied)
    marker = copied / "COPIED.txt"
    marker.write_text(
        f"copied {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"from {day_dir}\nfiles {usb_checked}\nfailed {usb_failed}\n",
        encoding="utf-8",
    )
    if usb_failed:
        eprint(f"USB copy failed verification: {copied}")
        return 1
    if not read_role(usb):
        write_role(usb, "A")
    print(f"usb snapshot ok: {copied} role={read_role(usb) or 'A'}")
    prune(usb, keep)
    return 0


def cmd_friday(args, config: dict) -> int:
    out = Path(args.out).expanduser().resolve()
    if args.usb_a or args.usb_b:
        if not (args.usb_a and args.usb_b):
            raise SystemExit("Friday needs both --usb-a and --usb-b, or neither so you can select them")
        usb_a = require_mounted_usb(Path(args.usb_a), "USB A")
        usb_b = require_mounted_usb(Path(args.usb_b), "USB B")
    else:
        selected = select_mounts(2, "Friday: dock stick and second stick")
        usb_a = require_mounted_usb(selected[0], "USB A")
        usb_b = require_mounted_usb(selected[1], "USB B")
    distinct_devices([usb_a, usb_b])
    role_a = read_role(usb_a)
    role_b = read_role(usb_b)
    if role_a and role_b and role_a == role_b:
        raise SystemExit(f"both sticks are labeled {role_a}; Friday needs USB A and USB B")
    day_dir = latest_day(out)
    checked, failed = verify_tree(day_dir)
    if failed:
        eprint("refusing Friday copy; local snapshot failed verification")
        return 1
    copied_a = copy_day(day_dir, usb_a)
    copied_b = copy_day(day_dir, usb_b)
    failed_a = verify_tree(copied_a)[1]
    failed_b = verify_tree(copied_b)[1]
    if failed_a or failed_b:
        eprint("Friday copy failed verification")
        return 1
    write_role(usb_a, "A")
    write_role(usb_b, "B")
    note = (
        f"friday copy {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"from {day_dir}\nfiles {checked}\n"
    )
    (copied_a / "FRIDAY.txt").write_text(note + "role A\n", encoding="utf-8")
    (copied_b / "FRIDAY.txt").write_text(note + "role B\n", encoding="utf-8")
    print(f"friday sticks ok: A={usb_a} B={usb_b}")
    keep_fridays = int(config.get("keep_fridays", 2))
    prune(usb_b, keep_fridays)
    return 0


def cmd_verify(args, config: dict) -> int:
    del config
    checked, failed = verify_tree(Path(args.path).expanduser().resolve())
    print(f"checked {checked}, failed {failed}")
    return 1 if failed else 0


def cmd_drives(args, config: dict) -> int:
    del args, config
    drives = mounted_usb_drives()
    if not drives:
        eprint("no USB drives mounted")
        return 1
    for item in drives:
        role = item["role"] or "-"
        print(f"{item['mount']}\t{item['label'] or '-'}\t{item['size']}\trole={role}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rebuild-kit", description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON config of services and excludes")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="local snapshot plus a required mounted dock USB")
    snap.add_argument("--src", help="single service directory")
    snap.add_argument("--name", help="service name when using --src")
    snap.add_argument("--out", required=True, help="local backup root")
    snap.add_argument("--usb", help="dock USB mount. Omit to select from plugged-in drives")
    snap.add_argument("--date", help="override date folder, YYYY-MM-DD")
    snap.add_argument("--keep", type=int, default=7, help="dated folders to keep")
    snap.set_defaults(func=cmd_snapshot)

    friday = sub.add_parser("friday", help="require both sticks mounted, then copy the latest day")
    friday.add_argument("--out", required=True, help="local backup root")
    friday.add_argument("--usb-a", help="dock USB mount")
    friday.add_argument("--usb-b", help="Friday USB mount")
    friday.set_defaults(func=cmd_friday)

    drives = sub.add_parser("drives", help="list mounted USB drives")
    drives.set_defaults(func=cmd_drives)

    verify = sub.add_parser("verify", help="rehash a day folder or a service folder")
    verify.add_argument("path")
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
