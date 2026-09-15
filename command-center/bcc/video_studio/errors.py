"""Safe, stable owner diagnostics. Raw subprocess output and paths stay private."""
from __future__ import annotations
import errno
import uuid


def render_failure(error: Exception, stage: str) -> dict:
    from .export_receipt import RenderReceiptInvalid
    from .model import Conflict
    message = str(error).lower()
    if isinstance(error, RenderReceiptInvalid) or "independent verification" in message:
        code, text = "VERIFY_FAILED", "The output did not pass independent media verification."
    elif isinstance(error, TimeoutError):
        code, text = "TIMEOUT", "Media processing timed out; inspect the job before retrying."
    elif isinstance(error, FileNotFoundError) or ("unavailable" in message and ("ffmpeg" in message or "ffprobe" in message)):
        code, text = "FFMPEG_MISSING", "The required local media executable or input is unavailable."
    elif isinstance(error, FileExistsError):
        # Занятое имя артефакта — это конкурирующий экспорт, а не сбой хоста:
        # INTERNAL_ERROR отправлял владельца искать поломку там, где её нет.
        code, text = "OUTPUT_EXISTS", "The export artifact path is already taken; queue a new export."
    elif isinstance(error, PermissionError):
        code, text = "POLICY_BLOCKED", "The media operation is not permitted in this environment."
    elif isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.ENOMEM, errno.EMFILE):
        code, text = "RESOURCE_LIMIT", "Insufficient disk, memory or file resources for media processing."
    elif "media process failed" in message:
        code = "DECODE_FAILED" if stage == "verifying" else "ENCODE_FAILED"
        text = "The local media process failed; the job has not produced a verified result."
    elif isinstance(error, Conflict):
        code, text = "REVISION_CONFLICT", "The project changed after this export was queued; queue a new export."
    elif isinstance(error, (ValueError, KeyError, StopIteration)):
        code, text = "INVALID_PROJECT", "The project or export options are invalid or unsupported."
    else:
        code, text = "INTERNAL_ERROR", "The media job failed; review its correlated diagnostic record."
    return {"code": code, "message": text, "stage": stage, "retryable": False,
            "context_id": uuid.uuid4().hex}
