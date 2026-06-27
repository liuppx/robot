from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi import Query

from hub.adapters import TraderAdapter
from hub.config import Settings
from hub.models import (
    AuthChallengeRequest,
    AuthChallengeView,
    BotInstanceActionResponse,
    AuthSessionView,
    AuthSessionVerifyRequest,
    BotInstanceCreateRequest,
    BotInstanceDiagnoseResponse,
    BotInstanceListResponse,
    BotInstanceLogsResponse,
    BotInstancePairResponse,
    BotInstanceUpdateModelRequest,
    BotInstanceView,
    HealthResponse,
    RobotListResponse,
    RobotTypesResponse,
    RouterModelsResponse,
    TraderActionResponse,
    TraderConfigUpdateRequest,
    TraderConfigUpdateResponse,
    TraderSummaryResponse,
    VersionResponse,
)
from hub.services import (
    MessengerStateService,
    RobotRegistry,
    SESSION_COOKIE_NAME,
    auth_service,
)

router = APIRouter(prefix="/api/v1/public", tags=["public"])


def require_session(request: Request) -> AuthSessionView:
    settings = Settings()
    session = auth_service(settings).current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return session


def messenger_service(settings: Settings) -> MessengerStateService:
    from hub.services.messenger import MessengerRuntimeConfig

    return MessengerStateService(
        MessengerRuntimeConfig(
            state_file=settings.resolved_runtime_dir / "state.json",
            instances_root=settings.resolved_instances_root,
            runtime_dir=settings.resolved_runtime_dir,
            repo_root=settings.repo_root,
            default_model=settings.default_model,
            model_allowlist=settings.parsed_model_allowlist,
            router_base_url=settings.router_base_url,
            router_api_key=settings.router_api_key,
            port_range_start=settings.instance_port_start,
            port_range_end=settings.instance_port_end,
            openclaw_prefix=settings.openclaw_prefix,
        )
    )


@router.get("/health", response_model=HealthResponse)
async def public_health() -> HealthResponse:
    return HealthResponse()


@router.get("/version", response_model=VersionResponse)
async def public_version() -> VersionResponse:
    return VersionResponse()


@router.get("/auth/me", response_model=AuthSessionView)
async def public_auth_me(request: Request) -> AuthSessionView:
    settings = Settings()
    session = auth_service(settings).current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return session


@router.post("/auth/wallet/challenge", response_model=AuthChallengeView)
async def public_auth_wallet_challenge(
    payload: AuthChallengeRequest,
    request: Request,
) -> AuthChallengeView:
    settings = Settings()
    service = auth_service(settings)
    try:
        return service.create_challenge(request, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/wallet/verify", response_model=AuthSessionView)
async def public_auth_wallet_verify(
    payload: AuthSessionVerifyRequest,
    response: Response,
) -> AuthSessionView:
    settings = Settings()
    service = auth_service(settings)
    try:
        session = service.verify_wallet_session(payload)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=service.encode_session(session),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return session


@router.post("/auth/logout")
async def public_auth_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/robots", response_model=RobotListResponse)
async def public_robots(request: Request) -> RobotListResponse:
    require_session(request)
    settings = Settings()
    return RobotRegistry(settings.repo_root).list_items()


@router.get("/robot/types", response_model=RobotTypesResponse)
async def public_robot_types(request: Request) -> RobotTypesResponse:
    require_session(request)
    return RobotTypesResponse(
        botTypes=[
            {"id": "whatsapp", "name": "WhatsApp eCommerce", "requires": ["manual_pairing"]},
            {"id": "dingtalk", "name": "DingTalk", "requires": ["client_id", "client_secret"]},
        ]
    )


@router.get("/router/models", response_model=RouterModelsResponse)
async def public_router_models(request: Request) -> RouterModelsResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    return service.router_models()


@router.get("/robot/instances", response_model=BotInstanceListResponse)
async def public_bot_instances(request: Request) -> BotInstanceListResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    return service.list_instances()


@router.post("/robot/instances", response_model=BotInstanceView)
async def public_create_bot_instance(payload: BotInstanceCreateRequest, request: Request) -> BotInstanceView:
    settings = Settings()
    service = messenger_service(settings)
    session = require_session(request)
    try:
        return service.create_instance(payload, owner_wallet=session.wallet_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/robot/instances/{instance_id}", response_model=BotInstanceView)
async def public_bot_instance(instance_id: str, request: Request) -> BotInstanceView:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    instance = service.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return instance


@router.delete("/robot/instances/{instance_id}")
async def public_delete_bot_instance(instance_id: str, request: Request) -> dict:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.delete_instance(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except RuntimeError as exc:
        message = str(exc)
        status_code = 409 if "stop it before delete" in message else 502
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.patch("/robot/instances/{instance_id}/model", response_model=BotInstanceView)
async def public_patch_bot_instance_model(
    instance_id: str,
    payload: BotInstanceUpdateModelRequest,
    request: Request,
) -> BotInstanceView:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.update_instance_model(instance_id, payload.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/robot/instances/{instance_id}/logs", response_model=BotInstanceLogsResponse)
async def public_bot_instance_logs(
    instance_id: str,
    request: Request,
    lines: int = 120,
) -> BotInstanceLogsResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    logs = service.get_instance_logs(instance_id, lines=lines)
    if logs is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return logs


@router.get("/robot/instances/{instance_id}/diagnose", response_model=BotInstanceDiagnoseResponse)
async def public_bot_instance_diagnose(
    instance_id: str,
    request: Request,
    auto_recover: bool = Query(default=False),
) -> BotInstanceDiagnoseResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.diagnose_instance(instance_id, auto_recover=auto_recover)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/robot/instances/{instance_id}/start", response_model=BotInstanceActionResponse)
async def public_start_bot_instance(instance_id: str, request: Request) -> BotInstanceActionResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.start_instance(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/robot/instances/{instance_id}/stop", response_model=BotInstanceActionResponse)
async def public_stop_bot_instance(instance_id: str, request: Request) -> BotInstanceActionResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.stop_instance(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/robot/instances/{instance_id}/pair-whatsapp", response_model=BotInstancePairResponse)
async def public_pair_whatsapp(instance_id: str, request: Request) -> BotInstancePairResponse:
    require_session(request)
    settings = Settings()
    service = messenger_service(settings)
    try:
        return service.pair_whatsapp(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/trader/summary", response_model=TraderSummaryResponse)
async def public_trader_summary(request: Request) -> TraderSummaryResponse:
    require_session(request)
    settings = Settings()
    trader_root = settings.repo_root / "robots" / "custom" / "trader"
    adapter = TraderAdapter(trader_root)
    return adapter.summary()


@router.get("/trader/config", response_model=TraderSummaryResponse)
async def public_trader_config(request: Request) -> TraderSummaryResponse:
    return await public_trader_summary(request)


@router.put("/trader/config", response_model=TraderConfigUpdateResponse)
async def public_trader_config_update(
    payload: TraderConfigUpdateRequest,
    request: Request,
) -> TraderConfigUpdateResponse:
    require_session(request)
    settings = Settings()
    trader_root = settings.repo_root / "robots" / "custom" / "trader"
    adapter = TraderAdapter(trader_root)
    if not adapter.exists():
        raise HTTPException(status_code=404, detail="trader robot not found")
    try:
        return adapter.update_config(payload.broker, payload.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/trader/run-once", response_model=TraderActionResponse)
async def public_trader_run_once(request: Request) -> TraderActionResponse:
    return await _run_trader_action("run_once", request)


@router.post("/trader/start", response_model=TraderActionResponse)
async def public_trader_start(request: Request) -> TraderActionResponse:
    return await _run_trader_action("start", request)


@router.post("/trader/stop", response_model=TraderActionResponse)
async def public_trader_stop(request: Request) -> TraderActionResponse:
    return await _run_trader_action("stop", request)


async def _run_trader_action(action: str, request: Request) -> TraderActionResponse:
    require_session(request)
    settings = Settings()
    trader_root = settings.repo_root / "robots" / "custom" / "trader"
    adapter = TraderAdapter(trader_root)
    if not adapter.exists():
        raise HTTPException(status_code=404, detail="trader robot not found")
    try:
        return adapter.run_action(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
