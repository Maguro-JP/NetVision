from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import backups as backup_routes
from app.api.routes import (
    blueprint,
    calc,
    capabilities,
    changes,
    config_plans,
    configcheck,
    configrecipes,
    configs,
    conventions,
    dashboard,
    devices,
    documents,
    exports,
    exposures,
    glossary,
    imports,
    insights,
    jobs,
    maintenance,
    mib,
    pipeline,
    reference,
    remediation,
    replacement,
    scenarios,
    sites,
    syslog,
    topology,
)
from app.api.routes import schema as schema_routes
from app.api.routes import settings as settings_routes
from app.config import settings
from app.services import audit  # noqa: F401  変更履歴のフック登録


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.bootstrap import ensure_ready

    ensure_ready()
    from app.services import exposure_schedule

    exposure_schedule.start()
    from app.services import backup

    backup.start()
    yield
    exposure_schedule.stop()
    backup.stop()


app = FastAPI(
    title="NetVision API",
    version="0.1.0",
    description="ネットワーク機器のインベントリ・保守期限・ライフサイクル管理",
    lifespan=lifespan,
)

# どこからでも呼べる設定（"*"）のまま資格情報を許すと、他所のページから
# 利用者の権限で API を叩けてしまう。認証を入れたときに効いてくるので、
# いま塞いでおく。宛先を並べてある場合だけ資格情報を許す
_any_origin = "*" in settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not _any_origin,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = "/api/v1"
app.include_router(devices.router, prefix=api)
app.include_router(backup_routes.router, prefix=api)
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
app.include_router(calc.router, prefix=api)
app.include_router(mib.router, prefix=api)
app.include_router(pipeline.router, prefix=api)
app.include_router(schema_routes.router, prefix=api)
app.include_router(settings_routes.router, prefix=api)
app.include_router(jobs.router, prefix=api)
app.include_router(blueprint.router, prefix=api)
app.include_router(capabilities.router, prefix=api)
app.include_router(glossary.router, prefix=api)
app.include_router(conventions.router, prefix=api)
app.include_router(configcheck.router, prefix=api)
app.include_router(configrecipes.router, prefix=api)
app.include_router(syslog.router, prefix=api)

_SCHEMA_OPEN = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/meta",
    "/api/v1/schema",
    "/api/v1/schema/apply",
    "/api/v1/schema/confirm",
)


@app.middleware("http")
async def block_until_schema_is_current(request: Request, call_next):  # noqa: ANN001
    """表に差分があるあいだは、専用ページ以外の API を止める。

    古い形のまま一部の画面だけ動くと、壊れたように見えて原因が分からない。
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in _SCHEMA_OPEN or path.startswith("/api/v1/schema"):
        return await call_next(request)

    from app.services.schema_gate import SCHEMA_BLOCK_HINT, needs_apply

    if needs_apply():
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "表の形を更新する必要があります。"
                    "専用のページで控えを取ってから更新してください。"
                ),
                "hint": SCHEMA_BLOCK_HINT,
            },
        )
    return await call_next(request)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/meta", tags=["meta"])
def meta() -> dict[str, object]:
    """画面側が動作モードを知るための情報。

    版とスキーマの世代も返す。版を上げたあとに「本当に入れ替わったか」を
    確かめる先が無いと、上げ忘れや入れ替え漏れに気づけない。閉域では
    画面を開いて確かめられることが大事なので、API から見えるようにしておく。
    """
    from app.services import llm
    from app.services.schema_gate import schema_status

    status = schema_status()
    return {
        "demo_mode": settings.demo_mode,
        # モデルを置いてあるか。画面はこれを見て「AI で下書きする」を出す
        "llm": llm.status(),
        "warning_days": settings.warning_days,
        "critical_days": settings.critical_days,
        "version": app.version,
        "schema_revision": status["current_revision"],
        "schema_phase": status["phase"],
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
