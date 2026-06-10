"""Local dev runner with an in-memory Mongo (mongomock-motor) and seeded data.

For development without MongoDB installed: python run_dev.py
Production uses real Atlas via MONGO_URL; this file is dev-only.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient

import db

db._client = AsyncMongoMockClient()

import seed
import server  # noqa: E402


@server.app.on_event("startup")
async def dev_seed():
    await seed.seed()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=8000)
