from social_farm.generation.content_buffer import (
    BufferHealth,
    BufferedAsset,
    evaluate_buffer,
    recommended_generation_slots,
)


def test_buffer_excludes_blocked_and_expired_assets() -> None:
    snapshot = evaluate_buffer(
        [
            BufferedAsset("a", 3600),
            BufferedAsset("b", 3600, blocked=True),
            BufferedAsset("c", 3600, expires_at_epoch_s=10),
        ],
        now_epoch_s=20,
        target_seconds=7200,
    )
    assert snapshot.playable_seconds == 3600
    assert snapshot.health is BufferHealth.READY
    assert snapshot.asset_count == 1


def test_buffer_health_thresholds() -> None:
    empty = evaluate_buffer([], now_epoch_s=0, target_seconds=1000)
    critical = evaluate_buffer([BufferedAsset("a", 100)], now_epoch_s=0, target_seconds=1000)
    low = evaluate_buffer([BufferedAsset("a", 300)], now_epoch_s=0, target_seconds=1000)
    ready = evaluate_buffer([BufferedAsset("a", 600)], now_epoch_s=0, target_seconds=1000)

    assert empty.health is BufferHealth.EMPTY
    assert critical.health is BufferHealth.CRITICAL
    assert low.health is BufferHealth.LOW
    assert ready.health is BufferHealth.READY


def test_recommended_slots_is_bounded() -> None:
    snapshot = evaluate_buffer([], now_epoch_s=0, target_seconds=3600)
    assert recommended_generation_slots(snapshot, average_asset_seconds=60, max_batch=12) == 12

    full = evaluate_buffer([BufferedAsset("a", 3600)], now_epoch_s=0, target_seconds=3600)
    assert recommended_generation_slots(full, average_asset_seconds=60) == 0
