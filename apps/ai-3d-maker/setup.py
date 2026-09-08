"""Ship the printer/material profiles required by the installed CAD pipeline."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildProfiles(build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / "ai_3d_maker" / "_profiles"
        destination.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).parent / "profiles"
        for name in ("elegoo_neptune_3_plus.json", "material_defaults.json"):
            shutil.copyfile(source / name, destination / name)


setup(cmdclass={"build_py": BuildProfiles})
