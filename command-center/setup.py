"""Include the same reviewed UI in editable-source and wheel installations."""
from pathlib import Path
import importlib.util
import shutil
import json

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithUI(build_py):
    def run(self):
        repository = Path(__file__).resolve().parent.parent
        identity = {"source_sha": None, "source_dirty": None}
        helper = repository / "tools" / "build_source_identity.py"
        if helper.is_file():
            spec = importlib.util.spec_from_file_location("build_source_identity", helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            identity = module.source_identity(repository)
        super().run()
        (Path(self.build_lib) / "bcc" / "_build.json").write_text(
            json.dumps(identity) + "\n", encoding="utf-8")
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
