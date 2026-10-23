"""`photo.edit` / `photo.status` — the chat agent edits the owner's reference photo through Direct Gen.

Owner 23.10 (class 1): «Gemma должен забирать фото референс и редактировать промт, отправлять uncensored qwen
edit через чат пульт, а он пишет что не понимает фотки». Thin adapter, no new pipeline:
  * input = chat attachment ids (bcc.features.chat_threads), the picture bytes are read from the local
    attachment folder and go only into the local Direct Gen queue (sd-server on this PC) — never off the PC;
  * the job is the owner's own Direct Gen job (participant "owner"): the same queue, STOP, FaceLock, history,
    full-screen viewer and «send to pult» the owner already uses; nothing here bypasses them;
  * permission `media.generate` (granted per agent → AUTO, otherwise ASK);
  * the pult's fixed rules (gen_refine.safety_violation, photo=True) hold for agents too: no sexual/nude change
    of the person on a real photo, nothing involving minors. Refused before anything is queued.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

EDIT_TOOL = "photo.edit"
STATUS_TOOL = "photo.status"
DEFAULT_MODEL = "qwen-image-2.1-uc-edit"
EDIT_MODELS = ("qwen-image-2.1-uc-edit", "qwen-image-edit-2509")
MAX_REFS = 3
MAX_PROMPT_CHARS = 4000
PARTICIPANT = "owner"


def _reference_bytes(svc, attachment_id: str) -> bytes:
    """The attachment's picture as PNG/JPEG bytes (Direct Gen takes only those; webp/gif/bmp are re-encoded)."""
    from fastapi import HTTPException

    from . import chat_threads as ct
    try:
        meta = ct._read_attachment_meta(svc, attachment_id)
    except HTTPException as exc:
        raise ValueError(f"вложение {attachment_id}: {exc.detail}") from None
    if meta.get("kind") != "image":
        raise ValueError(f"вложение «{meta.get('name')}» — не картинка ({meta.get('kind')})")
    folder = ct._attachment_folder(svc, attachment_id)
    path = folder / Path(str(meta.get("name") or "")).name
    if path.resolve().parent != folder.resolve() or not path.is_file():
        raise ValueError(f"файл вложения {attachment_id} не найден")
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        return data
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            out = io.BytesIO()
            img.convert("RGB").save(out, "PNG")
    except OSError as exc:
        raise ValueError(f"«{meta.get('name')}» не открывается как картинка ({exc})") from None
    return out.getvalue()


def _aspect_resolution(model: str, data: bytes) -> str | None:
    """WxH with the reference's aspect ratio at the model's default pixel budget (an edit must not come back square)."""
    from PIL import Image

    from ..direct_gen import sdcli
    spec = sdcli.BY_ID.get(model)
    if spec is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
    except OSError:
        return None
    dw, dh = (int(x) for x in spec.default_resolution.split("x"))
    k = ((dw * dh) / (w * h)) ** 0.5
    m = spec.side_multiple

    def side(v: float) -> int:
        return int(min(spec.max_side, max(spec.min_side, round(v * k / m) * m)))
    return f"{side(w)}x{side(h)}"


def _brief(job: dict) -> str:
    return (f"job {job.get('job_id')} · {job.get('model')} · статус {job.get('status')}"
            + (f" · этап {job.get('stage')}" if job.get("stage") and job.get("stage") != job.get("status") else ""))


async def _t_edit(args: dict, ctx: ToolContext) -> ToolResult:
    from ..direct_gen.service import DirectGenError
    from ..telegram_companion.gen_refine import safety_violation

    prompt = str(args.get("prompt") or "").strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        return ToolResult(content=f"{EDIT_TOOL}: prompt пустой или длиннее {MAX_PROMPT_CHARS}", error=True)
    ids = args.get("attachments") or []
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_REFS:
        return ToolResult(content=f"{EDIT_TOOL}: нужно 1..{MAX_REFS} attachment_id фото-референсов", error=True)
    model = str(args.get("model") or DEFAULT_MODEL)
    if model not in EDIT_MODELS:
        return ToolResult(content=f"{EDIT_TOOL}: model должна быть одной из {', '.join(EDIT_MODELS)}", error=True)
    refused = safety_violation(prompt, photo=True)
    if refused:
        return ToolResult(content=refused, error=True)
    svc = ctx.svc
    service = getattr(svc, "direct_gen", None)
    if service is None:
        return ToolResult(content=f"{EDIT_TOOL}: Direct Gen не запущен в этом Bossman", error=True)
    try:
        images = [_reference_bytes(svc, str(i)) for i in ids]
    except (ValueError, OSError) as exc:
        return ToolResult(content=f"{EDIT_TOOL}: {exc}", error=True)
    body = {"model": model, "prompt": prompt, "negative": str(args.get("negative") or ""), "mode": "DIRECT",
            "images_b64": [base64.b64encode(b).decode() for b in images],
            "facelock": bool(args.get("facelock", False))}
    if args.get("steps"):
        try:
            body["steps"] = int(args["steps"])
        except (TypeError, ValueError):
            return ToolResult(content=f"{EDIT_TOOL}: steps должно быть целым числом", error=True)
    resolution = str(args.get("resolution") or "") or _aspect_resolution(model, images[0])
    if resolution:
        body["resolution"] = resolution
    try:
        job = await service.create(PARTICIPANT, body)
    except DirectGenError as exc:
        return ToolResult(content=f"{EDIT_TOOL}: Direct Gen отказал: {exc.detail}", error=True)
    head = f"🎨 Правка фото поставлена в очередь Direct Gen: {_brief(job)}"
    content = (f"{head}\nПромт (как отправлен модели):\n{prompt}\n\nРезультат появится в «Генерациях» пульта; "
               f"проверить — {STATUS_TOOL} job_id={job.get('job_id')}. Очередь — это ещё не готовая картинка.")
    return ToolResult(content=content, one_line=head, external=True,
                      data={"job_id": job.get("job_id"), "status": job.get("status"), "model": model})


async def _t_status(args: dict, ctx: ToolContext) -> ToolResult:
    from ..direct_gen.service import DirectGenError
    service = getattr(ctx.svc, "direct_gen", None)
    if service is None:
        return ToolResult(content=f"{STATUS_TOOL}: Direct Gen не запущен", error=True)
    try:
        job = service.get(PARTICIPANT, str(args.get("job_id") or ""))
    except DirectGenError as exc:
        return ToolResult(content=f"{STATUS_TOOL}: {exc.detail}", error=True)
    err = job.get("error") or {}
    tail = f" · ошибка: {err.get('message') or err}" if err else ""
    return ToolResult(content=_brief(job) + tail, external=True,
                      data={"job_id": job.get("job_id"), "status": job.get("status")})


SPECS = [
    ToolSpec(
        name=EDIT_TOOL,
        description=("Отредактировать фото-референс из чата локальной uncensored-моделью правки Qwen (Direct Gen на "
                     "этом ПК). attachments — attachment_id фото из блока вложений (1..3, порядок = @1 @2 @3); "
                     "prompt — твой доработанный подробный английский промт: что изменить и что сохранить. "
                     "Edit the chat reference photo with the local Qwen image-edit model; returns a queued job."),
        handler=_t_edit,
        input_schema={"attachments": {"type": "array", "items": {"type": "string"},
                                      "description": "attachment_id фото-референсов"},
                      "prompt": {"type": "string", "description": f"промт правки, до {MAX_PROMPT_CHARS} знаков"},
                      "model": {"type": "string", "enum": list(EDIT_MODELS)},
                      "negative": {"type": "string"},
                      "facelock": {"type": "boolean", "description": "вернуть лицо с @1 как на фото"},
                      "steps": {"type": "integer"},
                      "resolution": {"type": "string", "description": "WxH; по умолчанию — пропорции фото @1"}},
        required=["attachments", "prompt"],
        category="write", permission="media.generate", source="builtin", default_effect="ask",
        idempotent=False, external_output=True,
    ),
    ToolSpec(
        name=STATUS_TOOL,
        description="Статус правки фото по job_id из photo.edit. Status of a photo.edit job.",
        handler=_t_status,
        input_schema={"job_id": {"type": "string"}},
        required=["job_id"],
        category="read", source="builtin", default_effect="auto", external_output=True,
    ),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_photo_edit", router=None, setup=_setup)
