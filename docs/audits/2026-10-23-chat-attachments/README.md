# Chat attachments reach the model — live proof (owner 23.10, class 1)

Tool: `tools/prove_chat_attachments.py --agent 41` (Gemma 4 uncensored), synthetic files only, own thread
«PROOF chat attachments». Each file carries a random code `K` + 6 chars; PASS = the code is in the answer.

| receipt | verdict | note |
|---|---|---|
| `receipt-20261023-0924.json` | FAIL (measurement bug) | the tool read `result` from `/api/tasks/<id>`, which has no such field. The real answer (thread turn of task 193) listed all 7 codes exactly; the tool was fixed to read the thread turn, the same view the pult renders. |
| `receipt-20261023-0930.json` | **PASS 7/7** | task 194, 86.5 s: png (vision), scanned pdf (vision), text pdf, docx, xlsx, pptx, txt. |

Photo → refined prompt → local uncensored Qwen edit (Direct Gen): `tools/prove_chat_photo_edit.py`, receipts
`photo-edit-*.json` in this folder.
