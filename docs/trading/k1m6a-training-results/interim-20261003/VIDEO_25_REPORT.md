# Video #25 — July 30th! We Almost Done! Bitcoin Levels To Watch!

- Video ID: `8wItpyVUi2k`; duración técnica: 1,836.754 s (30:36.754).
- Alcance: análisis local de audio, subtítulos y fotogramas; no se descargaron pesos ni se usó servicio de pago.
- Fuente de audio/ASR: `faster-whisper-small`, CPU int8, 4 hilos; 404 segmentos, 90.2 s de runtime ASR.
- Extracción de candidatos de aprendizaje: `bossman-community-qwen-uncensored:latest`; 15 ventanas de 120 s, 26 candidatos, 19 descartados, 238.27 s de latencia acumulada, media 15.885 s/ventana. Todos `UNVERIFIED_NOT_PROMOTED`.
- Selección semántica: 29 fotogramas de 326 cambios de escena; búsqueda guiada por subtítulos, ASR y cambios cercanos, no muestreo uniforme.
- Revisión local de visión: `bossman-fast-qwen36-vision:latest`; 27 respuestas de 29, 2 timeouts (`smart_003_00164.jpg`, `smart_023_01382.jpg`); latencia acumulada de respuestas correctas 784.14 s. Contact sheet inspeccionada: predominan capturas repetidas de interfaz/gráfico y overlays; valores visuales precisan verificación en píxeles antes de usarse.
- Intento inicial de importación automática se atascó en solicitud de imagen al endpoint compatible con OpenAI y se canceló el cliente local; recuperación y procesamiento local continuaron sin modificar el servidor Ollama ni el servicio Bossman.
- No se validaron resultados de mercado, reproducción cerrada ni backtest; no se entrenó/fine-tuneó ningún modelo y no se promovió ninguna regla.

## Incidencia y evidencia

- El JSON de visión conserva resultados y dos timeout. El análisis del contact sheet halló abundantes vistas repetidas del stream; las predicciones numéricas no se aceptan como datos de mercado verificados.
- Vídeo WebM original, MP4 remux, subtítulos originales, transcripción ASR, notas completas y fotogramas se conservan únicamente en el archivo local; este informe no duplica citas extensas ni medios.
- SHA-256 íntegros, tamaños, imágenes verificadas y versiones quedan en `audit_manifest.json`.
