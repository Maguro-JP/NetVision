from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    changes,
    config_plans,
    configs,
    dashboard,
    devices,
    documents,
    exports,
    exposures,
    imports,
    insights,
    maintenance,
    reference,
    remediation,
    replacement,
    scenarios,
    sites,
    topology,
)
from app.config import settings
from app.services import audit  # noqa: F401  変更履歴のフック登録

app = FastAPI(
    title="NetVision API",
    version="0.1.0",
    description="ネットワーク機器のインベントリ・保守期限・ライフサイクル管理",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = "/api/v1"
app.include_router(devices.router, prefix=api)
app.include_router(maintenance.router, prefix=api)
app.include_router(sites.router, prefix=api)
app.include_router(dashboard.router, prefix=api)
app.include_router(imports.router, prefix=api)
app.include_router(exports.router, prefix=api)
app.include_router(changes.router, prefix=api)
app.include_router(insights.router, prefix=api)
app.include_router(scenarios.router, prefix=api)
app.include_router(configs.router, prefix=api)
app.include_router(topology.router, prefix=api)
app.include_router(reference.router, prefix=api)
app.include_router(documents.router, prefix=api)
app.include_router(config_plans.router, prefix=api)
app.include_router(replacement.router, prefix=api)
app.include_router(remediation.router, prefix=api)
app.include_router(exposures.router, prefix=api)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/meta", tags=["meta"])
def meta() -> dict[str, object]:
    """画面側が動作モードを知るための情報。"""
    return {
        "demo_mode": settings.demo_mode,
        "warning_days": settings.warning_days,
        "critical_days": settings.critical_days,
    }


def _mount_frontend(directory: str) -> None:
    """ビルド済みの画面を同じプロセスから配信する（1台完結モード）。

    nginx を別に立てずに済むので、1人で試すときの手数が減る。
    SPA なので、API 以外の未知のパスは index.html に返す。
    """
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(directory)
    index = root / "index.html"
    if not index.exists():
        return

    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = root / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


if settings.serve_frontend:
    _mount_frontend(settings.serve_frontend)
