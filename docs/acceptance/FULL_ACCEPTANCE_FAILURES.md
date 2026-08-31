# FULL ACCEPTANCE FAILURES — every non-PASS explained

Format per failure: PHASE / TEST / ERROR / ROOT_CAUSE / SEVERITY / STATUS / NEXT.

(none yet)

---

## LIVE-ОПЕРАЦИЯ SEEDANCE (2026-08-31,详见 docs/acceptance/SEEDANCE_LIVE_LOG.md)

FAIL V06-provider-live / submit-seedance / HTTP 400 InputImageSensitiveContentDetected.PrivacyInformation
ROOT_CAUSE: провайдер отклоняет референсы-фотографии реальных людей (анти-дипфейк).
SEVERITY: P2 (внешнее ограничение провайдера)
STATUS: FIXED — стилизация референсов image-моделью до полного отсутствия фотореализма (2 итерации: painterly недостаточно, comic-poster прошёл).

FAIL V06-tooling / state-json / json.decoder.JSONDecodeError: Unexpected UTF-8 BOM
ROOT_CAUSE: PowerShell Out-File -Encoding utf8 пишет BOM.
SEVERITY: P2 (окружение)
STATUS: FIXED — запись через [IO.File]::WriteAllText (UTF-8 без BOM).

FAIL V18-final-glue / ffmpeg-concat-demuxer / длительность 75.1с вместо 54.5с
ROOT_CAUSE: разные timebase/аудио-параметры клипов; concat-демуксер не нормализует.
SEVERITY: P2 (инструментальная)
STATUS: FIXED — filter_complex concat=n=6:v=1:a=1 с перекодированием.

BUG V18-cost-estimate / seedance-2.0 1080p цена / фактическая $1.5125 за 4с при оценке ~$0.27
ROOT_CAUSE: экстраполяция цены от SKU mini; фактический расход токенов/сек флагмана в ~5.6 раза выше.
SEVERITY: P1 (финансовый перерасход ~$1.17 сверх лимита ключа $3.50; итог ≈$4.71)
STATUS: ACKNOWLEDGED — перерасход зафиксирован честно; правило: живой прайс модели сверяется до сабмита.
