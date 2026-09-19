"""Владельческие сценарии 16–17: студии выдают НАСТОЯЩИЙ артефакт.

«Провайдер ответил» не равно «файл лежит на диске и открывается». Обе цепочки
кончаются измерением самого артефакта, и обе опираются на уже существующий в
ветке медиа-оборот `tools/media_roundtrip.py` — второй такой инструмент здесь
намеренно НЕ заводится.

Медиа-оборот сам несёт отрицательные контроли (страница ошибки с именем .mp4,
нулевой файл, обрезанный экспорт, экспорт без дорожки). Сценарий обязан их
предъявить: без них «артефакт получен» означало бы только «файл создан».
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402


def _section(result: dict, name: str) -> dict:
    """Секции медиа-оборота приходят словарём, ключ — имя студии."""
    sections = result.get("sections") or {}
    return sections.get(name) or {}


def _rejected_controls(result: dict) -> list:
    return [c for c in result.get("negative_controls", []) if c.get("rejected")]


@scenario(id="OS-16", depth=INSTALLED_PRODUCT)
def os16_image_studio_saves_a_real_artifact(ctx) -> None:
    """Студия изображений СОХРАНЯЕТ файл, тип которого измерен по байтам."""
    import media_roundtrip as mr  # noqa: PLC0415

    ctx.reached_installed_product("tools/media_roundtrip.py + bcc.features.images этой ветки")
    result = mr.roundtrip("image")
    ctx.positive("медиа-оборот студии изображений прошёл целиком",
                 result.get("status") == "PASS", f"status={result.get('status')}")
    image = _section(result, "image_studio")
    ctx.positive("в обороте участвовала именно студия изображений продукта",
                 image.get("status") == "PASS"
                 and "ImageStorage" in str(image.get("stored_through", "")),
                 f"сохранено через={image.get('stored_through')}")
    ctx.positive("измеренный по байтам тип совпал с обещанием имени файла",
                 image.get("export", {}).get("measured_format") == "png_pipe"
                 and image.get("export", {}).get("passed") is True,
                 f"формат={image.get('export', {}).get('measured_format')}")
    ctx.positive("ffmpeg и ffprobe действительно присутствовали в прогоне",
                 set(result.get("ffmpeg") or []) >= {"ffmpeg", "ffprobe"},
                 f"бинари={result.get('ffmpeg')}")

    controls = _rejected_controls(result)
    ctx.negative("страница ошибки с именем .mp4 отвергнута по ИЗМЕРЕННОМУ типу",
                 any("scene.mp4" in (c.get("case") or "") and "error page" in (c.get("case") or "")
                     for c in controls),
                 f"отвергнуто контролей={len(controls)}")
    ctx.negative("нулевой файл не считается артефактом",
                 any("zero-byte" in (c.get("case") or "") for c in controls))
    ctx.negative("отрицательные контроли не пустые: проверка что-то отвергает",
                 len(controls) >= 2, f"контролей={len(controls)}")

    # ЧЕСТНЫЙ ИТОГ. Сохранение и измерение типа по байтам доказаны на настоящем
    # PNG, но САМА ГЕНЕРАЦИЯ картинки в продукте — детерминированный мок: живого
    # провайдера изображений на этой ветке нет. Цепочка владельца «выдаёт и
    # СОХРАНЯЕТ настоящий артефакт» закрыта наполовину, и выдавать её за зелёную
    # нельзя.
    gap = image.get("provider_gap") or {}
    ctx.owner_required(
        "сохранение и измерение артефакта доказаны, но генерация не настоящая: "
        f"провайдер={gap.get('provider')}, generation_is_real={gap.get('generation_is_real')}. "
        f"{gap.get('note', '')} Нужен ключ живого провайдера изображений владельца.")


@scenario(id="OS-17", depth=INSTALLED_PRODUCT)
def os17_video_studio_artifact_passes_ffprobe(ctx) -> None:
    """Видеостудия выдаёт артефакт, а ffprobe независимо подтверждает поток."""
    import media_roundtrip as mr  # noqa: PLC0415

    ctx.reached_installed_product("tools/media_roundtrip.py + bcc.video_studio этой ветки")
    result = mr.roundtrip("video")
    ctx.positive("медиа-оборот видеостудии прошёл целиком",
                 result.get("status") == "PASS", f"status={result.get('status')}")
    section = _section(result, "video_studio")
    ctx.positive("в обороте участвовала именно видеостудия продукта",
                 section.get("status") == "PASS"
                 and bool(section.get("rendered_through")),
                 f"рендер через={section.get('rendered_through')}")
    ctx.positive("ffprobe подтвердил настоящий поток в артефакте",
                 set(result.get("ffmpeg") or []) >= {"ffprobe"}
                 and result.get("status") == "PASS",
                 f"бинари={result.get('ffmpeg')}")

    controls = _rejected_controls(result)
    ctx.negative("обрезанный экспорт отвергается полным декодированием",
                 any("truncated" in (c.get("case") or "") for c in controls),
                 f"отвергнуто контролей={len(controls)}")
    ctx.negative("нулевой файл не проходит ту же проверку",
                 any("zero-byte" in (c.get("case") or "") for c in controls))
    ctx.negative("отрицательные контроли не пустые: проверка что-то отвергает",
                 len(controls) >= 2, f"контролей={len(controls)}")
