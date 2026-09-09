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
        # File Intelligence reads the pinned upstream identity of the external
        # AI File Sorter sidecar from integration.json. The checkout resolves it
        # relative to the repository; an installed wheel has no repository, and
        # a soft "manifest not found" silently reported an EMPTY pin — the
        # installed product dropped a declared identity the source build has.
        # The manifest travels with the code, like the UI does.
        integration = Path(__file__).parent.parent / "integrations" / "ai-file-sorter"
        packaged = Path(self.build_lib) / "bcc" / "_integrations" / "ai-file-sorter"
        packaged.mkdir(parents=True, exist_ok=True)
        for name in ("integration.json", "NOTICE.md"):
            source_file = integration / name
            if not source_file.is_file():
                raise RuntimeError(
                    f"AI File Sorter integration {name} is missing: a wheel whose "
                    "File Intelligence cannot name its pinned integration is not usable")
            shutil.copyfile(source_file, packaged / name)


setup(cmdclass={"build_py": BuildWithUI})
