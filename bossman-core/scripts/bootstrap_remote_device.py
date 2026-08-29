#!/usr/bin/env python3
"""Local-only bootstrap for the first Stage 12 phone.

Run from `bossman-core/` on the BOSSMAN host. It talks directly to the existing
Postgres store and prints the raw device token exactly once. Nothing is sent to
any external service.
"""
from __future__ import annotations

import argparse
import asyncio

from bossman import db
from bossman.remote_client.service import DeviceService
from bossman.remote_client.store import DDL, PostgresDeviceStore


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="owner-iphone")
    ap.add_argument("--scopes", default="chat,events,approve,admin")
    args = ap.parse_args()
    scopes = {x.strip() for x in args.scopes.split(",") if x.strip()}
    allowed = {"chat", "events", "approve", "admin"}
    if not scopes or not scopes <= allowed:
        raise SystemExit(f"invalid scopes; allowed={sorted(allowed)}")
    async with (await db.pool()).acquire() as conn:
        await conn.execute(DDL)
    svc = DeviceService(PostgresDeviceStore())
    device_id, raw = await svc.enroll(args.name, scopes)
    print(f"device_id={device_id}")
    print(f"device_token={raw}")
    print("STORE THIS TOKEN NOW. It cannot be retrieved again.")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
