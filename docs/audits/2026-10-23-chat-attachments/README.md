# Chat attachments reach the model — live proof (owner 23.10, class 1)

Tool: `tools/prove_chat_attachments.py --agent 41` (Gemma 4 uncensored), synthetic files only, own thread
«PROOF chat attachments». Each file carries a random code `K` + 6 chars; PASS = the code is in the answer.

| receipt | verdict | note |
|---|---|---|
| `receipt-20261023-0924.json` | FAIL (measurement bug) | the tool read `result` from `/api/tasks/<id>`, which has no such field. The real answer (thread turn of task 193) listed all 7 codes exactly; the tool was fixed to read the thread turn, the same view the pult renders. |
| `receipt-20261023-0930.json` | **PASS 7/7** | task 194, 86.5 s: png (vision), scanned pdf (vision), text pdf, docx, xlsx, pptx, txt. |

Photo → refined prompt → local uncensored Qwen edit (Direct Gen): `tools/prove_chat_photo_edit.py`, receipts
`photo-edit-*.json` in this folder.

| receipt | verdict | note |
|---|---|---|
| `photo-edit-20261023-1022.json` | FAIL (found a real Direct Gen bug) | hand-off 5/5 PASS: Gemma read the code off the picture, wrote its own English edit prompt, queued `qwen-image-2.1-uc-edit` with 1 reference. The edit itself failed `empty_output`: sd-cli writes `image.png.part.png` (it appends `.png` to a non-png `-o`), the runner looked for `image.png.part`. Same cause as an owner job failing earlier on 23.10. Fixed in `bcc/direct_gen/sdcli.py` (temp name `image.part.png`) + regression test with a negative control. |
| `photo-edit-20261023-1049.json` | **PASS 7/7** | on `8736fd51` (sd-cli fix + aspect ratio): task 196, turn 200 s; Gemma read «KPAJYBJ» off the picture, wrote the edit prompt itself; Direct Gen job `1ca96fd9` `qwen-image-2.1-uc-edit`, 1 reference, 704x384 (reference aspect kept), 1171 s, verified PNG; circle RGB 220,20,20 → 0,51,211. Sheet: `photo-edit-before-after.png`. |
