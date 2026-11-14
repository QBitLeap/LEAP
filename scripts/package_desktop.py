"""Package and smoke-test LEAP on the platform that will run it."""

import argparse
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def package(output, assets):
    os_name = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}[platform.system()]
    architecture = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64",
                    "aarch64": "arm64"}[platform.machine().lower()]
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    archive_name = f"leap-{version}-{os_name}-{architecture}"
    icon_assets = assets / "platform-icons/leap"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="leap-desktop-") as directory:
        work = Path(directory)
        options = []
        if os_name == "windows":
            png = (icon_assets / "windows.png").read_bytes()
            icon = work / "leap.ico"
            icon.write_bytes(struct.pack("<HHHBBBBHHII", 0, 1, 1, 0, 0, 0, 0, 1, 32, len(png), 22) + png)
            options = ["--icon", str(icon)]
        run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "leap",
            "--paths", str(ROOT), "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
            "--specpath", str(work), *options, str(ROOT / "scripts/desktop_entry.py"), cwd=ROOT)
        bundle = work / "dist/leap"
        for filename in ("LICENSE", "README.md"):
            shutil.copyfile(ROOT / filename, bundle / filename)
        shutil.copyfile(icon_assets / f"{os_name}.png", bundle / "icon.png")
        shutil.copyfile(icon_assets / f"{os_name}-testnet4.png", bundle / "icon-testnet4.png")
        shutil.copyfile(assets / "icon-overlays/NOTICE.md", bundle / "ICON-NOTICE.md")
        archive = shutil.make_archive(str(output / archive_name), "zip" if os_name == "windows" else "gztar",
                                      root_dir=bundle.parent, base_dir=bundle.name)
        extracted = work / "extracted"
        shutil.unpack_archive(archive, extracted)
        exe = extracted / "leap" / ("leap.exe" if os_name == "windows" else "leap")
        (work / "smoke.txt").write_text("LEAP desktop smoke test\n", encoding="utf-8")
        run(exe, "--help", cwd=work)
        run(exe, "build", "smoke.txt", "--name", "Desktop fixture", "--out", "smoke.quantum", cwd=work)
        run(exe, "inspect", "smoke.quantum", cwd=work)
        run(exe, "batch", "--help", cwd=work)
        # Exercise the packaged binary above the previous envelope ceiling.
        (work / "near-limit.txt").write_bytes(b"x" * 197_000)
        run(exe, "build", "near-limit.txt", "--name", "Envelope boundary",
            "--out", "near-limit.quantum", cwd=work)
        encoded = (work / "near-limit.quantum").read_bytes()
        if not 190_000 < len(encoded) <= 198_000:
            raise RuntimeError("Packaged binary does not implement the 198,000-byte envelope limit")
        run(exe, "inspect", "near-limit.quantum", cwd=work)
        (work / "oversized.txt").write_bytes(b"x" * 198_001)
        refused = subprocess.run([str(exe), "build", "oversized.txt", "--name", "Oversized",
                                  "--out", "oversized.quantum"], cwd=work, capture_output=True)
        if refused.returncode == 0:
            raise RuntimeError("Packaged binary accepted an oversized envelope")
    print(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    args = parser.parse_args()
    package(args.output.resolve(), args.assets.resolve())
