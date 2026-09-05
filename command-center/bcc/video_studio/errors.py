"""Owner-safe, structured local media failures; raw tool output stays private."""
import uuid

MESSAGES = {
    "FFMPEG_MISSING": "Install FFmpeg and ffprobe, then retry the media job.",
    "DECODE_FAILED": "The source media could not be decoded. Replace or relink the source.",
    "ENCODE_FAILED": "FFmpeg could not encode the export. Check the selected codec and available disk space.",
    "VERIFY_FAILED": "The export failed independent media verification. Retry or inspect the source and export settings.",
    "TIMEOUT": "The media operation exceeded its time limit. Retry with a smaller export range.",
    "RESOURCE_LIMIT": "Local memory, disk space, or process output limits prevented this media operation.",
    "POLICY_BLOCKED": "The media operation was blocked by the host storage or execution policy.",
    "APPROVAL_REQUIRED": "Owner approval is required before the media operation can continue.",
    "INVALID_PROJECT": "The project or media parameters are invalid. Review the timeline and export settings.",
    "OUTPUT_MISSING": "The media output is missing. Retry the export or restore the owned artifact.",
    "INTERNAL_ERROR": "The local media operation failed. Retry and use the context ID when investigating.",
}


class MediaFailure(ValueError):
    def __init__(self, category, stage="render"):
        self.details = _details(category, stage)
        super().__init__(self.details["safe_message"])


def _details(category, stage):
    return {"code": category, "category": category, "safe_message": MESSAGES[category],
            "stage": stage, "retryable": category in {"ENCODE_FAILED", "VERIFY_FAILED", "TIMEOUT", "RESOURCE_LIMIT", "OUTPUT_MISSING", "INTERNAL_ERROR"},
            "owner_action_required": category in {"FFMPEG_MISSING", "DECODE_FAILED", "POLICY_BLOCKED", "APPROVAL_REQUIRED", "INVALID_PROJECT"},
            "technical_context_id": uuid.uuid4().hex}


def failure_details(exc, stage="render"):
    if isinstance(exc, MediaFailure):
        return exc.details
    text = str(exc).lower()
    if isinstance(exc, TimeoutError): category = "TIMEOUT"
    elif isinstance(exc, PermissionError): category = "POLICY_BLOCKED"
    elif isinstance(exc, FileNotFoundError): category = "OUTPUT_MISSING"
    elif any(x in text for x in ("disk space", "capture limit", "memory", "no space")): category = "RESOURCE_LIMIT"
    elif "verification" in text or "hash mismatch" in text: category = "VERIFY_FAILED"
    elif "ffmpeg" in text and "unavailable" in text: category = "FFMPEG_MISSING"
    elif isinstance(exc, (ValueError, KeyError, TypeError)): category = "INVALID_PROJECT"
    else: category = "INTERNAL_ERROR"
    return _details(category, stage)
