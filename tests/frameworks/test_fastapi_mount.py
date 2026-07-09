import asyncio

import httpx
import pytest
from fastapi import FastAPI

from agentkit.frameworks.serving.fastapi_mount import mount_legacy_fastapi_app


def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_mount_legacy_fastapi_app_under_prefix():
    app = FastAPI()
    legacy = FastAPI()

    @legacy.post("/chat")
    async def chat(payload: dict):
        return {"answer": payload["question"]}

    mount_legacy_fastapi_app(app, legacy, prefix="/legacy")

    response = _request(app, "POST", "/legacy/chat", json={"question": "hi"})

    assert response.status_code == 200
    assert response.json() == {"answer": "hi"}


def test_mount_is_promoted_ahead_of_root_mount():
    app = FastAPI()
    root = FastAPI()
    legacy = FastAPI()
    app.mount("/", root)

    @legacy.get("/health")
    async def health():
        return {"legacy": True}

    mount_legacy_fastapi_app(app, legacy, prefix="/legacy")

    response = _request(app, "GET", "/legacy/health")

    assert response.status_code == 200
    assert response.json() == {"legacy": True}


def test_mount_rejects_root_prefix_by_default():
    app = FastAPI()
    legacy = FastAPI()

    with pytest.raises(ValueError, match="shadow AgentKit routes"):
        mount_legacy_fastapi_app(app, legacy, prefix="/")


def test_mount_allows_root_prefix_when_explicit():
    app = FastAPI()
    legacy = FastAPI()

    @legacy.get("/legacy-health")
    async def health():
        return {"ok": True}

    mount_legacy_fastapi_app(app, legacy, prefix="/", allow_root=True)

    response = _request(app, "GET", "/legacy-health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_mount_rejects_non_fastapi_app():
    with pytest.raises(TypeError, match="FastAPI"):
        mount_legacy_fastapi_app(FastAPI(), object(), prefix="/legacy")
