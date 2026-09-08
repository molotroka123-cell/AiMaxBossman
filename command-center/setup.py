"""Include the same reviewed UI in editable-source and wheel installations."""
from pathlib import Path
import shutil
import json
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithUI(build_py):
    def run(self):
        super().run()
        repository = Path(__file__).resolve().parent.parent
        revision = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=15)
        source_sha = revision.stdout.strip() if revision.returncode == 0 else None
        (Path(self.build_lib) / "bcc" / "_build.json").write_text(
            json.dumps({"source_sha": source_sha}) + "\n", encoding="utf-8")
        source = Path(__file__).parent / "ui"
        target = Path(self.build_lib) / "bcc" / "_ui"
        if target.exists():
            shutil.rmtree(target)
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if (not path.is_file() or "tests" in relative.parts
                    or path.suffix not in {".html", ".js", ".css", ".svg", ".png", ".ico", ".json"}):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        apps = Path(__file__).parent.parent / "apps"
        for manifest in apps.glob("*/app.manifest.yaml"):
            destination = Path(self.build_lib) / "bcc" / "_apps" / manifest.parent.name
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest, destination / manifest.name)
            metadata = manifest.parent / "pyproject.toml"
            if metadata.is_file():
                shutil.copyfile(metadata, destination / metadata.name)
            if manifest.parent.name == "file-commander-mini":
                app_ui = manifest.parent / "src" / "file_commander_mini" / "ui.html"
                if not app_ui.is_file():
                    raise RuntimeError("File Commander UI is missing from the release")
                shutil.copyfile(app_ui, destination / "ui.html")
        if not (target / "index.html").is_file():
            raise RuntimeError("Command Center UI is missing: cannot build a usable wheel")


setup(cmdclass={"build_py": BuildWithUI})
