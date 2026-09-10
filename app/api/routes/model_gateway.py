from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ModelChannelBatchUpdate,
    ModelChannelCreate,
    ModelChannelProfile,
    ModelChannelUpdate,
    ModelGatewaySummary,
    ModelInvocation,
    ModelProviderPreset,
    ModelRouteCreate,
    ModelRouteProfile,
    ModelRouteUpdate,
    PlaygroundChatRequest,
)
from app.modules.ai.service import (
    create_model_channel,
    create_model_route,
    delete_model_channel,
    delete_model_route,
    get_model_channel,
    get_model_route,
    list_model_channels,
    list_model_invocations,
    list_model_routes,
    model_gateway_summary,
    model_provider_presets,
    stream_playground_chat,
    sync_model_channel_models,
    test_model_channel,
    update_model_channel,
    update_model_route,
)
from app.modules.platform.service import add_admin_audit, current_admin_user


router = APIRouter(tags=["Model gateway"])


@router.get("/api/model-provider-presets", response_model=list[ModelProviderPreset])
def provider_presets(request: Request) -> list[ModelProviderPreset]:
    current_admin_user(request)
    return model_provider_presets()


@router.get("/api/model-gateway/summary", response_model=ModelGatewaySummary)
def gateway_summary(request: Request) -> ModelGatewaySummary:
    current_admin_user(request)
    return model_gateway_summary()


@router.get("/api/model-channels", response_model=list[ModelChannelProfile])
def model_channels(request: Request) -> list[ModelChannelProfile]:
    current_admin_user(request)
    return list_model_channels()


@router.post("/api/model-channels", response_model=ModelChannelProfile)
def add_model_channel(payload: ModelChannelCreate, request: Request) -> ModelChannelProfile:
    actor = current_admin_user(request)
    channel = create_model_channel(payload)
    add_admin_audit(actor, "create", "model-channels", channel.id, "Model channel created",
                    after_state=channel.model_dump(mode="json"))
    return channel


@router.patch("/api/model-channels/{channel_id}", response_model=ModelChannelProfile)
def patch_model_channel(channel_id: str, payload: ModelChannelUpdate, request: Request) -> ModelChannelProfile:
    actor = current_admin_user(request)
    before = get_model_channel(channel_id).model_dump(mode="json")
    channel = update_model_channel(channel_id, payload)
    add_admin_audit(actor, "edit", "model-channels", channel_id, "Model channel updated",
                    before_state=before, after_state=channel.model_dump(mode="json"))
    return channel


@router.delete("/api/model-channels/{channel_id}")
def remove_model_channel(channel_id: str, request: Request) -> dict[str, bool]:
    actor = current_admin_user(request)
    before = get_model_channel(channel_id).model_dump(mode="json")
    delete_model_channel(channel_id)
    add_admin_audit(actor, "delete", "model-channels", channel_id, "Model channel deleted", before_state=before)
    return {"deleted": True}


@router.post("/api/model-channels/{channel_id}/test")
def test_channel(channel_id: str, request: Request, model: str | None = Query(default=None)) -> dict[str, object]:
    actor = current_admin_user(request)
    result = test_model_channel(channel_id, model)
    add_admin_audit(actor, "test", "model-channels", channel_id, f"Model channel tested in {result['latency_ms']}ms")
    return result


@router.post("/api/model-channels/{channel_id}/sync-models", response_model=ModelChannelProfile)
def sync_channel_models(channel_id: str, request: Request) -> ModelChannelProfile:
    actor = current_admin_user(request)
    channel = sync_model_channel_models(channel_id)
    add_admin_audit(actor, "sync", "model-channels", channel_id, f"Synchronized {len(channel.models)} models")
    return channel


@router.post("/api/model-channels/batch-status")
def batch_channel_status(payload: ModelChannelBatchUpdate, request: Request) -> dict[str, int | bool]:
    actor = current_admin_user(request)
    updated = 0
    for channel_id in dict.fromkeys(payload.ids):
        update_model_channel(channel_id, ModelChannelUpdate(enabled=payload.enabled))
        updated += 1
    add_admin_audit(actor, "batch_enable" if payload.enabled else "batch_disable", "model-channels", None,
                    f"Updated {updated} model channels")
    return {"updated": updated, "enabled": payload.enabled}


@router.get("/api/model-routes", response_model=list[ModelRouteProfile])
def model_routes(request: Request) -> list[ModelRouteProfile]:
    current_admin_user(request)
    return list_model_routes()


@router.post("/api/model-routes", response_model=ModelRouteProfile)
def add_model_route(payload: ModelRouteCreate, request: Request) -> ModelRouteProfile:
    actor = current_admin_user(request)
    route = create_model_route(payload)
    add_admin_audit(actor, "create", "model-routes", route.id, "Model route created",
                    after_state=route.model_dump(mode="json"))
    return route


@router.patch("/api/model-routes/{route_id}", response_model=ModelRouteProfile)
def patch_model_route(route_id: str, payload: ModelRouteUpdate, request: Request) -> ModelRouteProfile:
    actor = current_admin_user(request)
    before = get_model_route(route_id).model_dump(mode="json")
    route = update_model_route(route_id, payload)
    add_admin_audit(actor, "edit", "model-routes", route_id, "Model route updated",
                    before_state=before, after_state=route.model_dump(mode="json"))
    return route


@router.delete("/api/model-routes/{route_id}")
def remove_model_route(route_id: str, request: Request) -> dict[str, bool]:
    actor = current_admin_user(request)
    before = get_model_route(route_id).model_dump(mode="json")
    delete_model_route(route_id)
    add_admin_audit(actor, "delete", "model-routes", route_id, "Model route deleted", before_state=before)
    return {"deleted": True}


@router.get("/api/model-invocations", response_model=list[ModelInvocation])
def model_invocations(request: Request, limit: int = Query(default=100, ge=1, le=500),
                      status: str | None = Query(default=None)) -> list[ModelInvocation]:
    current_admin_user(request)
    return list_model_invocations(limit, status)


@router.post("/api/model-playground/chat")
def model_playground_chat(payload: PlaygroundChatRequest, request: Request) -> StreamingResponse:
    current_admin_user(request)

    def events():
        for event in stream_playground_chat(payload):
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
