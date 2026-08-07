from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    changes,
    configs,
    dashboard,
    devices,
    exports,
    imports,
    insights,
    maintenance,
    reference,
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


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
