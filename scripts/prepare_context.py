"""Prepare an allowlisted, checksummed indexer image context without starting it."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT.name.removesuffix("-umbrel")
PINS_REVISION = "3e014f4ff156a35eea4b8613e37116fd124aa719"
QORD_REVISION = "a4febe49f10eb6ba10ded6ab42098a5bec7be914"
IMPORT = re.compile(r'''(?:from\s*|import\s*\()\s*["']([^"']+)["']''')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Only regular source files are allowed")
    before = digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o644)
    if digest(source) != before or digest(target) != before:
        raise ValueError("Source changed while preparing context")


def closure(source, entries):
    todo, seen = list(entries), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        text = (source / rel).read_text()
        for name in IMPORT.findall(text):
            if not name.startswith("."):
                continue
            target = ((source / rel).parent / name).resolve()
            if not target.is_relative_to(source.resolve()) or not target.is_file():
                raise ValueError("Unresolved source import")
            todo.append(str(target.relative_to(source)))
    return seen


def prepare(source, output):
    source, output = source.absolute(), output.absolute()
    if not source.is_dir() or source.is_symlink() or output.exists() or output.is_symlink():
        raise ValueError("Expected an existing source and a new context destination")
    if PROTOCOL not in {"leap", "pins", "qord"}:
        raise ValueError("Unsupported independent app repository")
    if PROTOCOL in {"pins", "qord"}:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if head != {"pins": PINS_REVISION, "qord": QORD_REVISION}[PROTOCOL]:
            raise ValueError("Protocol source differs from the reviewed upstream revision")
    runtime, fixtures = set(), set()
    if PROTOCOL == "leap":
        if (source / "pebbles").exists():
            raise ValueError("Public LEAP context must exclude the experimental implementation")
        for package in ("quantums", "indexer"):
            runtime.update(str(p.relative_to(source)) for p in (source / package).glob("*.py"))
        if any("pebbletest" in rel or "portable_ops" in rel or "native_pebble" in rel or "profile_ops" in rel for rel in runtime):
            raise ValueError("Experimental modules are not public release inputs")
        runtime.add("LICENSE")
        for folder in ("tests", "scripts", "examples", "batch-examples", "gallery-examples", "Guides/Quantums"):
            fixtures.update(str(p.relative_to(source)) for p in (source / folder).rglob("*")
                            if p.is_file() and "__pycache__" not in p.parts
                            and p.suffix in {".py", ".json", ".yaml", ".yml", ".txt"})
    elif PROTOCOL == "pins":
        # These reviewed lazy imports are named through variables upstream.
        runtime = closure(source, ["scripts/indexer-run.ts", "packages/protocol/profile.mjs",
                                   "packages/protocol/reserve.mjs", "packages/collection/canonical.mjs"])
        fixtures = closure(source, [str(p.relative_to(source)) for p in (source / "packages/indexer").glob("*.test.ts")]) - runtime
        runtime.update({"LICENSE", "NOTICE"})
    else:
        names = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).split(b"\0")
        runtime = {x.decode() for x in names if x
                   and x.decode().split("/")[0] not in {".github", ".agents"}
                   and Path(x.decode()).name not in {"AGENTS.md", ".DS_Store"}}
    output.mkdir(parents=True, mode=0o700)
    for name in ("Dockerfile", "requirements.lock", "LICENSE", "NOTICE"):
        if (ROOT / name).is_file():
            copy(ROOT / name, output / name)
    if PROTOCOL == "qord":
        copy(ROOT / "scripts/collect_notices.py", output / "build/collect_notices.py")
    for p in (ROOT / "app").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            copy(p, output / "app" / p.relative_to(ROOT / "app"))
    original, flattened = {}, {}
    for folder, files in (("protocol", runtime), ("fixtures", fixtures)):
        for rel in sorted(files):
            target = output / folder / rel
            input_path = source / rel
            if input_path.is_symlink() and PROTOCOL == "qord" and rel in {
                    "docs/theme/favicon.png", "docs/theme/favicon.svg"}:
                resolved = input_path.resolve()
                expected = source / "static" / Path(rel).name
                if resolved != expected.resolve() or not resolved.is_relative_to(source.resolve()):
                    raise ValueError("Unexpected upstream asset link target")
                flattened[rel] = str(expected.relative_to(source))
                input_path = resolved
            copy(input_path, target)
            original[folder + "/" + rel] = digest(target)
    if PROTOCOL == "pins":
        original_lock = json.loads((source / "package-lock.json").read_text())
        todo, required = ["@qbit-oss/protocol"], {}
        while todo:
            name = todo.pop()
            if name in required:
                continue
            entry = original_lock["packages"]["node_modules/" + name]
            if not entry.get("integrity") or not str(entry.get("resolved", "")).startswith("https://registry.npmjs.org/"):
                raise ValueError("Unpinned production npm dependency")
            required[name] = entry
            todo.extend(entry.get("dependencies", {}))
        package = {"name": "pins-indexer-runtime", "version": "1.0.0", "private": True,
                   "type": "module", "license": "Apache-2.0",
                   "dependencies": {"@qbit-oss/protocol": required["@qbit-oss/protocol"]["version"]}}
        lock = {"name": package["name"], "version": package["version"], "lockfileVersion": 3,
                "requires": True, "packages": {"": {k: package[k] for k in ("name", "version", "license", "dependencies")},
                **{"node_modules/" + name: entry for name, entry in required.items()}}}
        for name, value in (("package.json", package), ("package-lock.json", lock)):
            (output / "protocol" / name).write_text(json.dumps(value, indent=2) + "\n")
    report = {"format": "standalone-indexer-context-v1", "protocol": PROTOCOL,
              "upstreamRevision": {"pins": PINS_REVISION, "qord": QORD_REVISION}.get(PROTOCOL),
              "sourceIdentity": "Curated public Quantum/Gallery source" if PROTOCOL == "leap" else "Reviewed upstream plus recorded read-side corrections",
              "originalSourceHashes": original,
              "flattenedUpstreamAssetLinks": flattened,
              "runtimeFiles": {p.relative_to(output).as_posix(): digest(p)
                               for p in sorted(output.rglob("*")) if p.is_file()},
              "noOperationalDataIncluded": True, "nativeRuntimeAccepted": False}
    (output / "source-manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def verify(output):
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Expected a regular context directory")
    if (output / "source-manifest.json").is_symlink():
        raise ValueError("Linked manifest refused")
    report = json.loads((output / "source-manifest.json").read_text())
    actual = {}
    for path in output.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Linked or special context entry refused")
        if path.is_file() and path != output / "source-manifest.json":
            actual[path.relative_to(output).as_posix()] = digest(path)
    if (report.get("format") != "standalone-indexer-context-v1" or report.get("protocol") != PROTOCOL
            or actual != report.get("runtimeFiles")):
        raise ValueError("Context differs from the prepared source manifest")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--protocol-source", type=Path)
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.output) if args.verify else prepare(args.protocol_source, args.output)
    print(json.dumps({"protocol": PROTOCOL, "contextFiles": len(result["runtimeFiles"]), "serviceStarted": False}))


if __name__ == "__main__":
    main()
