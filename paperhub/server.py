"""Read-only FastAPI dashboard service. Run with python -m paperhub.server."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Response

from .config import Settings
from .history import History
from .runtime import Runtime


def create_app(settings=None, *, transport=None, clock=None, renderer=None, polling=True):
    @asynccontextmanager
    async def lifespan(app):
        configured = settings if settings is not None else Settings.load()
        store = History(Path(configured.data_dir) / "history.db")
        tasks = []
        try:
            async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
                options = {k: v for k, v in {"clock": clock, "renderer": renderer}.items() if v is not None}
                runtime = Runtime(configured, store, client, **options)
                app.state.runtime = runtime
                if polling:
                    tasks = [asyncio.create_task(runtime.poll(name)) for name in ("strata", "xangi")]
                    tasks.append(asyncio.create_task(runtime.cleanup()))
                try:
                    yield
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/paper.png")
    async def png(bat: int | None = Query(None, ge=0, le=100),
                  rssi: int | None = Query(None, ge=-200, le=0)):
        runtime = app.state.runtime
        runtime.save_telemetry(bat=bat, rssi=rssi)
        return Response(runtime.png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/paper.json")
    async def display():
        return app.state.runtime.view

    @app.get("/healthz")
    async def health():
        return app.state.runtime.health()

    return app


app = create_app()


def main():
    import uvicorn
    try:
        settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(f"設定エラー: {exc}") from None
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
