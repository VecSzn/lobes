"""lobes api: the OpenAI-style http server. Chat completions live in api.py, the Responses API in responses.py."""
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import api, responses


def make_app(cfg, default_profile=None):
    async def models(request):
        ids = list(api.MODELS) + [f"lobes/{p}" for p in cfg["profiles"]]
        return JSONResponse({"object": "list", "data": [{"id": i, "object": "model", "owned_by": "lobes"} for i in ids]})

    return Starlette(routes=[Route("/v1/chat/completions", api.make_route(cfg, default_profile), methods=["POST"]),
                             Route("/v1/responses", responses.make_route(cfg, default_profile), methods=["POST"]),
                             Route("/v1/models", models)])


def serve(cfg, host, port, profile=None):
    uvicorn.run(make_app(cfg, profile), host=host, port=port, log_level="warning")
