"""One-command rebuild from the pinned Git refs and code-rendered source."""

from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main() -> None:
    subprocess.run([sys.executable, str(HERE / "build_data.py")], check=True)
    subprocess.run([sys.executable, str(HERE / "render.py"), "stills", "--lang", "ru", "--times", "78"], check=True)
    shutil.copyfile(HERE.parent / "still_ru_078.0.png", HERE.parent / "poster_ru.png")
    subprocess.run([sys.executable, str(HERE / "render_all.py")], check=True)


if __name__ == "__main__":
    main()
