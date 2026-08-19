import asyncio
import json
import logging
import os
import subprocess
import sys
import httpx
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

OPENCODE_BASE = "http://127.0.0.1:4096"
_opencode_proc: Optional[subprocess.Popen] = None


def _cleanup_opencode():
    proc = globals().get("_opencode_proc")
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


import atexit
atexit.register(_cleanup_opencode)
DIRECTORY = os.environ.get("DIRECTORY", os.getcwd())
FEISHU_API = "https://open.feishu.cn/open-apis"

state: dict = {}


async def _feishu_token(app_id: str, app_secret: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        r.raise_for_status()
        return r.json()["tenant_access_token"]


async def _feishu_send(app_id: str, app_secret: str, chat_id: str, text: str) -> None:
    token = await _feishu_token(app_id, app_secret)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{FEISHU_API}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
        )
        if r.status_code not in (200, 201):
            logger.warning("Feishu send failed: %s %s", r.status_code, r.text)


async def _feishu_send_card(app_id: str, app_secret: str, chat_id: str, card: dict) -> None:
    token = await _feishu_token(app_id, app_secret)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{FEISHU_API}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
        )
        if r.status_code not in (200, 201):
            logger.warning("Feishu send card failed: %s %s", r.status_code, r.text)


async def _opencode_ask(text: str, agent: Optional[str] = None, chat_id: Optional[str] = None, timeout: float = 120.0) -> str:
    """Send text to OpenCode, collect reply. If chat_id given, send permission cards to Feishu."""
    client: httpx.AsyncClient = state["client"]
    session_id: str = state["session_id"]
    logger.info("OpenCode ask: session=%s agent=%s chat_id=%s text=%.80s", session_id, agent, chat_id, text)

    prompt_body: dict = {"parts": [{"type": "text", "text": text}]}
    if agent:
        prompt_body["agent"] = agent

    r = await client.post(
        f"/session/{session_id}/prompt_async",
        params={"directory": DIRECTORY},
        json=prompt_body,
    )
    if r.status_code != 204:
        logger.warning("prompt_async returned %s: %s", r.status_code, r.text)

    reply_parts: list = []
    part_types: dict = {}
    event_count = 0

    async with client.stream("GET", "/global/event", timeout=timeout) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            payload = event.get("payload", {})
            ptype = payload.get("type", "")
            props = payload.get("properties", {})

            if ptype == "message.part.updated":
                part = props.get("part", {})
                if "id" in part and "type" in part:
                    part_types[part["id"]] = part["type"]
                if event_count == 0:
                    logger.debug("First SSE event: type=%s part_type=%s", ptype, part.get("type"))

            elif ptype == "message.part.delta" and props.get("field") == "text":
                part_id = props.get("partID", "")
                if part_types.get(part_id) != "reasoning":
                    reply_parts.append(props["delta"])

            elif ptype == "permission.asked" and chat_id:
                from feishu_bot import _build_permission_card
                app_id = os.environ.get("FEISHU_APP_ID", "")
                app_secret = os.environ.get("FEISHU_APP_SECRET", "")
                card = _build_permission_card(
                    props.get("id", ""),
                    props.get("permission", ""),
                    props.get("patterns", []),
                )
                asyncio.create_task(
                    _feishu_send_card(app_id, app_secret, chat_id, card)
                )

            elif ptype == "session.status" and props.get("status", {}).get("type") == "idle":
                logger.debug("OpenCode idle after %s events, reply=%s chars", event_count, len(reply_parts))
                break

            event_count += 1

    return "".join(reply_parts)


async def on_permission_response(perm_id: str, action: str) -> None:
    client: httpx.AsyncClient = state["client"]
    session_id: str = state["session_id"]
    try:
        r = await client.post(
            f"/session/{session_id}/permissions/{perm_id}",
            json={"response": action},
        )
        logger.info("Permission %s -> %s (status=%s)", perm_id, action, r.status_code)
    except Exception:
        logger.exception("on_permission_response error for perm_id=%s", perm_id)


async def _list_agents() -> list:
    client: httpx.AsyncClient = state["client"]
    r = await client.get("/agent")
    r.raise_for_status()
    all_agents = r.json()
    logger.debug("Available agents: %s", [a["name"] for a in all_agents if a.get("mode") == "primary"])
    return [
        {"name": a["name"], "description": a.get("description", "")}
        for a in all_agents
        if a.get("mode") == "primary" and not a.get("native", True)
    ]


async def on_feishu_message(user_id: str, chat_id: str, text: str, agent: Optional[str] = None) -> None:
    app_id = os.environ["FEISHU_APP_ID"]
    app_secret = os.environ["FEISHU_APP_SECRET"]
    logger.info("Feishu message from %s in %s (agent=%s): %.80s", user_id, chat_id, agent, text)
    try:
        reply = await _opencode_ask(text, agent=agent, chat_id=chat_id)
        if reply:
            logger.info("Feishu reply to %s (%s chars)", chat_id, len(reply))
            await _feishu_send(app_id, app_secret, chat_id, reply)
        else:
            logger.warning("Empty reply from OpenCode for chat_id=%s", chat_id)
    except asyncio.TimeoutError:
        logger.error("OpenCode timeout for chat_id=%s text=%.80s", chat_id, text)
        await _feishu_send(app_id, app_secret, chat_id, "请求超时，请稍后重试")
    except Exception:
        logger.exception("on_feishu_message error for chat_id=%s", chat_id)
        await _feishu_send(app_id, app_secret, chat_id, "内部错误，请稍后重试")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _opencode_proc
    # auto-start opencode if not already running
    try:
        async with httpx.AsyncClient(base_url=OPENCODE_BASE, timeout=2.0) as c:
            await c.get("/api/health")
    except Exception:
        logger.info("OpenCode not detected, starting subprocess...")
        opencode_path = os.environ.get("OPENCODE_PATH") or "opencode"
        _opencode_proc = subprocess.Popen(
            f"{opencode_path} serve --port 4096",
            shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        import threading

        def _pipe_logger():
            for line in iter(_opencode_proc.stdout.readline, b""):
                logger.info("[opencode] %s", line.decode("utf-8", errors="replace").rstrip())
        threading.Thread(target=_pipe_logger, daemon=True).start()
        for i in range(30):
            try:
                async with httpx.AsyncClient(base_url=OPENCODE_BASE, timeout=2.0) as c:
                    r = await c.get("/api/health")
                    if r.status_code == 200:
                        logger.info("OpenCode subprocess ready after %ss", i + 1)
                        break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            logger.error("OpenCode subprocess failed to start in 30s")

    async with httpx.AsyncClient(base_url=OPENCODE_BASE, timeout=120.0) as client:
        logger.info("Connecting to OpenCode at %s ...", OPENCODE_BASE)
        try:
            r = await client.get("/api/health")
            logger.info("OpenCode health: %s", r.json())
        except Exception:
            logger.warning("OpenCode health check failed (server may not be ready)")

        r = await client.post("/session", params={"directory": DIRECTORY}, json={})
        r.raise_for_status()
        state["session_id"] = r.json()["id"]
        state["client"] = client
        logger.info("OpenCode session created: %s  directory=%s", state["session_id"], DIRECTORY)

        # start feishu bot if credentials are configured
        app_id = os.environ.get("FEISHU_APP_ID")
        app_secret = os.environ.get("FEISHU_APP_SECRET")
        if app_id and app_secret:
            from feishu_bot import FeishuBot
            bot = FeishuBot(
                app_id, app_secret,
                on_feishu_message,
                on_permission_response,
                _list_agents,
            )
            bot.start(asyncio.get_event_loop())
            logger.info("FeishuBot started (app_id=%s)", app_id)
        else:
            logger.info("FEISHU_APP_ID not set, skipping FeishuBot")

        yield
    state.clear()
    if _opencode_proc:
        logger.info("Stopping OpenCode subprocess...")
        _opencode_proc.terminate()
        try:
            _opencode_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _opencode_proc.kill()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    text: str
    agent: Optional[str] = None


class PermissionRequest(BaseModel):
    permission_id: str
    action: str


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-store"})


@app.get("/api/agents")
async def agents():
    client: httpx.AsyncClient = state["client"]
    r = await client.get("/agent")
    r.raise_for_status()
    all_agents = r.json()
    custom = [
        {"name": a["name"], "description": a.get("description", "")}
        for a in all_agents
        if a.get("mode") == "primary" and not a.get("native", True)
    ]
    return JSONResponse(custom)


@app.post("/api/permission")
async def permission(req: PermissionRequest):
    client: httpx.AsyncClient = state["client"]
    session_id: str = state["session_id"]
    try:
        r = await client.post(
            f"/session/{session_id}/permissions/{req.permission_id}",
            json={"response": req.action},
        )
        if r.status_code in (200, 204):
            return JSONResponse({"status": "ok"})
        return JSONResponse({"error": r.text}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/health")
async def health():
    return JSONResponse({"status": "ok", "session_id": state.get("session_id", "")})


@app.post("/api/chat")
async def chat(req: ChatRequest):
    return await chat_stream(req)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    client: httpx.AsyncClient = state["client"]
    session_id: str = state["session_id"]
    logger.info("Chat stream: session=%s agent=%s text=%.80s", session_id, req.agent, req.text)

    prompt_body: dict = {"parts": [{"type": "text", "text": req.text}]}
    if req.agent:
        prompt_body["agent"] = req.agent

    await client.post(
        f"/session/{session_id}/prompt_async",
        params={"directory": DIRECTORY},
        json=prompt_body,
    )

    async def event_generator():
        part_types: dict = {}
        event_count = 0

        try:
            async with client.stream("GET", "/global/event", timeout=120.0) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    payload = event.get("payload", {})
                    ptype = payload.get("type", "")
                    props = payload.get("properties", {})

                    if ptype == "message.part.updated":
                        part = props.get("part", {})
                        if "id" in part and "type" in part:
                            part_types[part["id"]] = part["type"]
                        if event_count == 0:
                            logger.debug("First SSE event: %s", ptype)

                    if ptype == "message.part.delta":
                        if props.get("field") == "text":
                            part_id = props.get("partID", "")
                            if part_types.get(part_id) == "reasoning":
                                continue
                            yield _sse({"type": "delta", "content": props["delta"]})

                    elif ptype == "permission.asked":
                        logger.debug("Permission asked: %s", props.get("permission"))
                        yield _sse({
                            "type": "permission",
                            "id": props.get("id"),
                            "tool": props.get("permission"),
                            "patterns": props.get("patterns", []),
                        })

                    elif ptype == "session.status":
                        if props.get("status", {}).get("type") == "idle":
                            logger.debug("SSE stream done after %s events", event_count)
                            yield _sse({"type": "done"})
                            return

                    event_count += 1
        except httpx.TimeoutException:
            logger.warning("SSE stream timeout after %s events", event_count)
            yield _sse({"type": "error", "content": "请求超时"})
            yield _sse({"type": "done"})
        except Exception:
            logger.exception("SSE stream error after %s events", event_count)
            yield _sse({"type": "error", "content": "内部错误"})
            yield _sse({"type": "done"})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
