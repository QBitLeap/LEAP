"""Prepare one standalone indexer Umbrel package, without deploying it."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT.name.removesuffix("-umbrel")


def prepare(output, app_id, chain, port, image, core_app_id, local_fixture=False, icon_url=None, package_version=None):
    for value in (app_id, core_app_id):
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", value):
            raise ValueError("Invalid app ID")
    if app_id == core_app_id or chain not in {"main", "testnet4"} or not 1024 <= port <= 65535:
        raise ValueError("Invalid independent app/network/port")
    if package_version is not None and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9][a-z0-9.-]*)?", package_version) is None:
        raise ValueError("Explicit app package version required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image) and not (
            local_fixture and re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
        raise ValueError("Pinned image required")
    icon_url = icon_url or "https://raw.githubusercontent.com/QBitLeap/" + PROTOCOL + "-umbrel/main/icon.svg"
    icon = urlsplit(icon_url)
    if icon.scheme not in {"http", "https"} or not icon.hostname or icon.username or icon.password or icon.fragment:
        raise ValueError("Expected a hosted icon URL without credentials")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output exists; preserve it")
    prefix = "APP_" + app_id.upper().replace("-", "_")
    core_prefix = "APP_" + core_app_id.upper().replace("-", "_")
    common_env = {"INDEX_PROTOCOL": PROTOCOL, "QBIT_CHAIN": chain,
                  "INDEX_LOCAL_TOKEN": "${" + prefix + "_LOCAL_TOKEN}"}
    common = {"image": image, "user": "1000:1000", "init": True, "restart": "on-failure",
              "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
              "pids_limit": 256, "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777"]}
    transport = {**common, "command": ["transport"], "mem_limit": "1024m", "cpus": 2,
                 "environment": {**common_env, "INDEX_CORE_READ_URL": "${" + core_prefix + "_READ_URL}",
                                 "INDEX_CORE_READ_TOKEN": "${" + core_prefix + "_READ_TOKEN}"},
                 "healthcheck": {"test": ["CMD", "python3", "-c",
                  "from urllib.request import urlopen; urlopen('http://127.0.0.1:8081/healthz',timeout=2).close()"],
                  "interval": "10s", "timeout": "4s", "retries": 6}}
    indexer = {**common, "command": ["indexer"], "network_mode": "service:transport",
               "environment": {**common_env, "INDEX_ENABLED": "${INDEX_ENABLED:-0}"},
               "volumes": ["${APP_DATA_DIR}/data/index:/data"], "mem_limit": "2048m", "cpus": 2,
               "depends_on": {"transport": {"condition": "service_healthy", "restart": True}},
               "stop_grace_period": "2m"}
    ui = {**common, "command": ["ui"], "network_mode": "service:transport",
          "environment": {**common_env, "INDEX_API_TOKEN": "${" + prefix + "_API_TOKEN}"},
          "volumes": ["${APP_DATA_DIR}/data/index:/data:ro"], "mem_limit": "256m", "cpus": 1,
          "depends_on": {"transport": {"condition": "service_healthy", "restart": True}}}
    compose = {"services": {"app_proxy": {"environment": {"APP_HOST": app_id + "_transport_1", "APP_PORT": 8080}},
                            "transport": transport, "indexer": indexer, "ui": ui}}
    title = {"leap": "LEAP", "pins": "PINS", "qord": "qord"}[PROTOCOL]
    upstream = {"leap": "https://github.com/QBitLeap/LEAP", "pins": "https://github.com/P2MR-PINS-Protocol/pins",
                "qord": "https://github.com/djh58/qord"}[PROTOCOL]
    version = {"leap": "0.1.0-alpha.0", "pins": "1.0.0", "qord": "0.1.0"}[PROTOCOL]
    repo = "https://github.com/QBitLeap/" + PROTOCOL + "-umbrel"
    manifest = {"manifestVersion": 2.0, "id": app_id, "storage": {"dataRoot": "data"},
                "environment": [{"name": "INDEX_ENABLED", "services": ["indexer"], "default": "0",
                                 "note": "Set 1 to index after checking Core, storage and power readiness."}],
                "category": "finance", "name": title + " Indexer" + (" · Testnet4" if chain == "testnet4" else ""),
                "version": package_version or version, "tagline": "Independent read-only " + title + " indexing",
                "description": "Follow your selected Qbit Core instance and maintain a separate, persistent "
                    + title + " index. Indexing starts paused; enable INDEX_ENABLED in app settings when ready. "
                    "Includes a browser status page and authenticated read API. No wallets, signing or broadcasting. "
                    "This is an amd64 package candidate.",
                "releaseNotes": "", "developer": "QBitLeap", "website": upstream,
                "dependencies": [core_app_id], "repo": repo, "support": repo + "/issues", "port": port,
                "icon": icon_url, "gallery": [], "path": "", "defaultUsername": "", "defaultPassword": "",
                "submitter": "QBitLeap"}
    output.mkdir(parents=True)
    (output / "data/index").mkdir(parents=True)
    (output / "data/index/.gitkeep").write_text("")
    for name, value in (("umbrel-app.yml", manifest), ("docker-compose.yml", compose)):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")
    exports = (f'export {prefix}_API_URL="http://{app_id}_transport_1:8080/v1/"\n'
               f'export {prefix}_API_USER="reader"\n'
               f'export {prefix}_API_TOKEN="$(derive_entropy "${{app_entropy_identifier}}-read-api")"\n'
               f'export {prefix}_LOCAL_TOKEN="$(derive_entropy "${{app_entropy_identifier}}-local-rpc")"\n')
    (output / "exports.sh").write_text(exports)
    shutil.copyfile(ROOT / "icon.svg", output / "icon.svg")
    report = {"format": "standalone-indexer-app-package-v1", "protocol": PROTOCOL, "appId": app_id,
              "coreAppId": core_app_id, "chain": chain, "image": image, "localFixtureImages": local_fixture,
              "fullUmbrelAppAccepted": False, "publicImagePublicationAccepted": False,
              "files": {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(output.rglob("*")) if p.is_file()}}
    (output / "package-receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--chain", choices=("main", "testnet4"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--core-app-id", required=True)
    parser.add_argument("--local-fixture-images", action="store_true")
    parser.add_argument("--icon-url")
    parser.add_argument("--package-version")
    args = parser.parse_args()
    report = prepare(args.output, args.app_id, args.chain, args.port, args.image,
                     args.core_app_id, args.local_fixture_images, args.icon_url, args.package_version)
    print(json.dumps({"protocol": PROTOCOL, "appId": report["appId"], "installed": False}))


if __name__ == "__main__":
    main()
