# IMPROVEMENTS FOR APPROVAL

1. **Semantic sorter**

Лёгкая локальная классификация файлов по проектам/темам, без тяжёлого LLM на каждом файле.

Decision: `APPROVE / REJECT / LATER`

2. **Safe rollback snapshots**

Перед массовым rename/move сохранять manifest операции и уметь полностью откатить её.

Decision: `APPROVE / REJECT / LATER`

3. **Smart duplicate sets**

Хеши + near-duplicate detection для фото/документов, но удаление только после preview/approval.

Decision: `APPROVE / REJECT / LATER`

4. **Project bundle creator**

Одна команда собирает связанные файлы проекта в portable bundle с индексом.

Decision: `APPROVE / REJECT / LATER`

5. **Watch-folder mode**

Опционально следить за Downloads/Inbox и только предлагать действия, не применять без правил.

Decision: `APPROVE / REJECT / LATER`
