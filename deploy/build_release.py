"""Package only production inputs, never local credentials or Git metadata."""
from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from pathlib import Path


def build(output: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "web/dist/index.html").is_file():
        raise SystemExit("Build web/ before packaging a release")
    paths = [root / name for name in (
        "src/legendary_broker", "config/ingestion.json", "deploy",
        "web/dist", "requirements-market.lock",
    )]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in paths:
            for file in sorted(path.rglob("*") if path.is_dir() else [path]):
                if file.is_file() and not any(part in {"__pycache__", ".git"} for part in file.parts):
                    archive.add(file, arcname=file.relative_to(root).as_posix())
        manifest = {
            "backend": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "frontend": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root / "web", text=True).strip(),
        }
        content = json.dumps(manifest, indent=2).encode()
        import io
        info = tarfile.TarInfo("release.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/market-release.tar.gz"))
    build(parser.parse_args().output)
