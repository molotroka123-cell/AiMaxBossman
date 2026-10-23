"""Live proof on the owner's PC: the chat model really SEES pictures and READS documents (owner 23.10, class 1).

    python tools/prove_chat_attachments.py [--agent 41] [--out docs/audits/2026-10-23-chat-attachments]

Synthetic files only, each carrying its own random code: PNG (code drawn as text), scanned PDF (image only, no
text layer), text PDF, DOCX, XLSX, PPTX, TXT. Each is uploaded through the live API into a fresh test thread,
then ONE message asks the agent to list the codes it can see. PASS for a file = its code appears in the answer.
The thread is the tool's own (title «PROOF chat attachments»); no owner chat or file is read. Receipt JSON with
counts, codes, task id, model, seconds. Exit 0 only when every file passes.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import string
import time
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = "http://127.0.0.1:8801"


def _token() -> str:
    d = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    return (d / "token").read_text(encoding="utf-8").strip()


def api(method: str, path: str, body=None, raw: bytes | None = None, ctype: str = "application/json", timeout=300):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-BCC-Token": _token(), "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def code() -> str:
    return "K" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def _font(size: int):
    for f in ("arial.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def picture(c: str) -> bytes:
    img = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((40, 60, 340, 360), fill="red")
    d.text((400, 170), c, fill="black", font=_font(72))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def scanned_pdf(c: str) -> bytes:
    img = Image.open(io.BytesIO(picture(c)))
    out = io.BytesIO()
    img.save(out, "PDF")
    return out.getvalue()


def text_pdf(c: str) -> bytes:
    """Minimal one-page PDF with a real text layer."""
    content = f"BT /F1 24 Tf 72 720 Td (Document code {c}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def docx(c: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Код документа {c}</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def xlsx(c: str) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["поле", "значение"])
    wb.active.append(["код таблицы", c])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def pptx(c: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("ppt/slides/slide1.xml", '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/'
                   'presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                   f'<a:t>Код слайда {c}</a:t></p:sld>')
    return out.getvalue()


MAKERS = {"picture.png": picture, "scan.pdf": scanned_pdf, "text.pdf": text_pdf, "doc.docx": docx,
          "table.xlsx": xlsx, "slides.pptx": pptx, "note.txt": lambda c: f"Код заметки {c}".encode()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--out", default="docs/audits/2026-10-23-chat-attachments")
    ap.add_argument("--wait", type=int, default=900)
    a = ap.parse_args(argv)
    codes = {name: code() for name in MAKERS}
    thread = api("POST", "/api/chat/threads", {"title": "PROOF chat attachments"})
    tid = thread.get("id") or (thread.get("thread") or {}).get("id")
    ids, kinds = [], {}
    for name, make in MAKERS.items():
        up = api("POST", f"/api/chat/attachments?filename={name}&thread_id={tid}", raw=make(codes[name]),
                 ctype="application/octet-stream")
        ids.append(up["id"])
        kinds[name] = up.get("kind")
    t0 = time.time()
    sent = api("POST", f"/api/chat/threads/{tid}/send", {"text": "Перечисли все коды вида K + 6 символов, которые ты видишь "
               "во вложениях, по одному на строку с именем файла.", "agent_id": a.agent, "attachments": ids}, timeout=900)
    task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get("task_id")
    answer, status = "", "?"
    while time.time() - t0 < a.wait:
        # The answer lives in the thread turn (task-run view), exactly what the pult renders; the bare
        # /api/tasks/<id> record has no result field.
        th = api("GET", f"/api/chat/threads/{tid}")
        turn = next((x for x in th.get("turns") or [] if int(x.get("task_id") or 0) == int(task_id or 0)), {})
        status = turn.get("status")
        if status in ("completed", "failed", "stopped", "cancelled"):
            answer = str(turn.get("result") or turn.get("error") or "")
            break
        time.sleep(5)
    rows = {name: {"kind": kinds[name], "code": codes[name], "seen": codes[name] in answer} for name in MAKERS}
    ok = all(r["seen"] for r in rows.values())
    receipt = {"verdict": "PASS" if ok else "FAIL", "agent_id": a.agent, "thread_id": tid, "task_id": task_id,
               "task_status": status, "answer_chars": len(answer), "seconds": round(time.time() - t0, 1), "files": rows,
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"receipt-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1),
                                                                    encoding="utf-8", newline="\n")
    for name, r in rows.items():
        print(f"{'PASS' if r['seen'] else 'FAIL'} {name:12s} kind={r['kind']}")
    print("VERDICT", receipt["verdict"], "| task", task_id, status, "|", receipt["seconds"], "s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
