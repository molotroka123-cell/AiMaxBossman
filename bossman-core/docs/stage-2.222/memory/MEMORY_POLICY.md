# Durable Memory Policy

## Lifecycle

`CANDIDATE → ACTIVE → STALE/SUPERSEDED` либо `CANDIDATE → DISPUTED`.

Новая запись не должна автоматически уничтожать старую. `supersedes` формирует явную историю решений. `contradicted_by` маркирует конфликт, который нужно разрешить.

## Promotion

Автоматически допустимо создавать только candidate. Promotion в ACTIVE — либо deterministic rule с высокой уверенностью для безопасного технического факта, либо explicit agent/user/audit gate. В первой реализации `MemoryManager.promote()` вызывается интеграционным слоем явно.

## Provenance

Каждая durable memory запись должна иметь `source_refs`. Для источника в индексе это chunk/document IDs; для внешнего plugin — стабильный external ref. Потеря provenance считается quality regression.

## Privacy

Не копировать секреты/API keys в долговременную память. Ingest layer должен получить secret scanner до подключения mail/chats. Sensitive content маркируется и фильтруется по agent permissions.
