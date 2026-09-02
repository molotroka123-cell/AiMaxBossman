"""Higgsfield-like video generation app simulator (SAFE, offline). Screens are
semantic element trees; the job advances one tick per observation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .sim import Element, World


@dataclass
class Job:
    id: str
    prompt: str
    duration_s: int
    aspect: str
    ticks: int = 0
    status: str = "queued"          # queued -> generating -> ready | error
    artifact_hash: str = ""
    extended: bool = False


@dataclass
class HiggsfieldSim:
    world: World = field(default_factory=lambda: World(app="Higgsfield", title="Higgsfield - Home", url="https://higgsfield.example/home"))
    screen: str = "home"
    mode: str = "Image to Video"
    model: str = "Standard"
    source_uploaded: bool = False
    prompt: str = ""
    duration_s: int = 5
    aspect: str = "16:9"
    credits: int = 100
    generation_count: int = 0
    job: Job | None = None
    downloads: list[dict] = field(default_factory=list)
    error_mode: bool = False
    substitute_download: bool = False
    ui_variant: str = "v1"          # v2 renames controls (selector drift)

    def __post_init__(self) -> None:
        self.render()

    # ------------------------------------------------------------ rendering
    def _names(self) -> dict[str, str]:
        if self.ui_variant == "v2":
            return {"generate": "Create", "prompt": "Describe your video", "upload": "Add media"}
        return {"generate": "Generate", "prompt": "Prompt", "upload": "Upload source"}

    def render(self) -> None:
        w, n = self.world, self._names()
        w.elements = []
        if self.screen == "home":
            w.title, w.url, w.summary = "Higgsfield - Home", "https://higgsfield.example/home", "Higgsfield Home"
            w.elements = [Element("button", "Create video", on_click=lambda _w: self.goto("create")),
                          Element("link", "Library"), Element("text", "Credits", text=f"Credits: {self.credits}")]
        elif self.screen == "create":
            w.title, w.url = "Higgsfield - Create", "https://higgsfield.example/create"
            w.summary = f"Create: mode={self.mode} model={self.model} source={'yes' if self.source_uploaded else 'no'} prompt={'set' if self.prompt else 'empty'}"
            can_generate = bool(self.prompt) and self.source_uploaded and self.credits > 0
            w.elements = [
                Element("combobox", "Mode", text=self.mode, on_click=lambda _w: self._set_mode()),
                Element("combobox", "Model", text=self.model, on_click=lambda _w: self._set_model()),
                Element("button", n["upload"], neighbors=["Sources"], on_click=lambda _w: self._upload()),
                Element("text", "Source file", text="source.png" if self.source_uploaded else "no source"),
                Element("textbox", n["prompt"], text=self.prompt, on_type=lambda _w, t: self._set_prompt(t)),
                Element("combobox", "Duration", text=f"{self.duration_s}s"),
                Element("combobox", "Aspect ratio", text=self.aspect),
                Element("text", "Credits", text=f"Credits: {self.credits}"),
                Element("button", n["generate"], neighbors=["Credits"], enabled=can_generate, on_click=lambda _w: self._generate()),
            ]
        elif self.screen == "job":
            j = self.job
            w.title, w.url = "Higgsfield - Job", f"https://higgsfield.example/jobs/{j.id}"
            w.summary = f"Job {j.id}: Status: {j.status}"
            w.elements = [
                Element("text", "Status", text=f"Status: {j.status}"),
                Element("text", "Duration", text=f"Duration: {j.duration_s}s"),
                Element("text", "Format", text="Format: mp4"),
                Element("button", "Extend", enabled=j.status == "ready", on_click=lambda _w: self._extend()),
                Element("button", "Download", enabled=j.status == "ready", on_click=lambda _w: self._download()),
                Element("link", "Back to create", on_click=lambda _w: self.goto("create")),
            ]
        w.touch()

    def goto(self, screen: str) -> None:
        self.screen = screen; self.render()

    # ------------------------------------------------------------ actions
    def _set_mode(self) -> None:
        self.mode = "Text to Video" if self.mode == "Image to Video" else "Image to Video"; self.render()

    def _set_model(self) -> None:
        self.model = "Cinema" if self.model == "Standard" else "Standard"; self.render()

    def _upload(self) -> None:
        self.source_uploaded = True; self.render()

    def _set_prompt(self, text: str) -> None:
        self.prompt = text; self.render()

    def _generate(self) -> None:
        if not (self.prompt and self.source_uploaded and self.credits > 0):
            raise RuntimeError("Generate is disabled")
        self.generation_count += 1
        self.credits -= 10
        self.job = Job(id=f"job{self.generation_count}", prompt=self.prompt, duration_s=self.duration_s, aspect=self.aspect)
        self.goto("job")

    def _extend(self) -> None:
        if not self.job or self.job.status != "ready":
            raise RuntimeError("Extend unavailable")
        self.job.extended = True; self.job.duration_s += 5; self.credits -= 5
        self.job.artifact_hash = hashlib.sha256(f"{self.job.id}:{self.job.duration_s}".encode()).hexdigest()[:16]
        self.render()

    def _download(self) -> None:
        if not self.job or self.job.status != "ready":
            raise RuntimeError("Download unavailable")
        j = self.job
        if self.substitute_download:
            self.downloads.append({"name": f"{j.id}.webm", "duration_s": 3, "format": "webm", "hash": "deadbeef"})
        else:
            self.downloads.append({"name": f"{j.id}.mp4", "duration_s": j.duration_s, "format": "mp4", "hash": j.artifact_hash})
        self.render()

    # ------------------------------------------------------------ time
    def tick(self) -> None:
        """Called by the observer before each observation: the job advances."""
        j = self.job
        if j is None or self.screen != "job" or j.status in ("ready", "error"):
            return
        j.ticks += 1
        if j.ticks == 1:
            j.status = "generating"
        elif j.ticks >= 2:
            if self.error_mode:
                j.status = "error"
            else:
                j.status = "ready"
                j.artifact_hash = hashlib.sha256(f"{j.id}:{j.duration_s}".encode()).hexdigest()[:16]
        self.render()
