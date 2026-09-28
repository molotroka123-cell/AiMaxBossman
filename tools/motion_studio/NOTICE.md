# Third-party material in Motion Studio

| Component | Where | License | Attribution / notes |
|---|---|---|---|
| lottie-web 5.13.0 (Airbnb) | `vendor/lottie-web/lottie_canvas.min.js` | MIT (`vendor/lottie-web/LICENSE.md`) | npm tarball integrity `sha512-+gfBXl6sxXMPe8tKQm7qzLnUy5DUPJPKIyRHwtpCpyUEYjHYRJC/5gjUvdkuO2c3JllrPtHXH5UJJK8LRYl5yQ==` verified 2026-09-28 |
| Noto Emoji Animation (Google), 100 files | fetched on demand, pinned in `lottie/catalog.json` | CC BY 4.0 | "Animated emoji: Noto Emoji Animation by Google, CC BY 4.0". `make_video.py` writes this into the MP4 comment when a video uses them; keep it in any published description |
| Onest, IBM Plex Mono fonts | fetched by `tools/intro_video/render.py` | SIL OFL 1.1 | not stored in Git |
| kokoro-onnx (runtime), Kokoro-82M (voices) | installed by the user | MIT, Apache-2.0 | local TTS; voice lines are synthesized, not recorded |

The score (`score.py`) is synthesized from code and uses no samples.
