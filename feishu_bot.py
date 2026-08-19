import asyncio
import json
import logging
import threading
from typing import Callable, Coroutine, Any, Optional

logger = logging.getLogger(__name__)

HELP_TEXT = """\
🤖 OpenCode Bot 使用说明

直接发消息 → 默认 build agent 回复

切换 Agent：
  /agent assistant 你的问题
  /agent build 你的问题

权限审批：
  工具调用时会收到审批卡片，点击 ✅Allow 或 ❌Deny

其他命令：
  /help  显示此帮助
  /agent 列出可用 agent"""


def _build_permission_card(perm_id: str, tool: str, patterns: list) -> dict:
    """Build a Feishu interactive card for permission approval."""
    pattern_text = "、".join(patterns) if patterns else "(无)"
    return {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**工具调用审批**\n工具：`{tool}`\n操作：`{pattern_text}`",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ Allow"},
                        "type": "primary",
                        "value": {"perm_id": perm_id, "action": "once"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "❌ Deny"},
                        "type": "danger",
                        "value": {"perm_id": perm_id, "action": "reject"},
                    },
                ],
            },
        ],
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": "⚠️ 需要授权"},
        },
    }


class FeishuBot:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: Callable[[str, str, str, Optional[str]], Coroutine[Any, Any, None]],
        on_permission: Callable[[str, str], Coroutine[Any, Any, None]],
        list_agents: Callable[[], Coroutine[Any, Any, list]],
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._on_message = on_message
        self._on_permission = on_permission
        self._list_agents = list_agents
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    # ── public ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._main_loop = loop
        t = threading.Thread(target=self._run_in_thread, daemon=True)
        t.start()
        logger.info("FeishuBot WebSocket thread started")

    # ── internal ─────────────────────────────────────────────────────────────

    def _dispatch(self, coro) -> None:
        if self._main_loop:
            asyncio.run_coroutine_threadsafe(coro, self._main_loop)

    def _run_in_thread(self) -> None:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
            CallBackToast,
        )

        def _on_message(event: P2ImMessageReceiveV1) -> None:
            try:
                data = event.event
                if data is None:
                    return
                msg = data.message
                if msg is None or msg.message_type != "text":
                    return
                chat_id = msg.chat_id
                sender = data.sender
                user_id = (
                    sender.sender_id.user_id
                    if sender and sender.sender_id
                    else ""
                )
                content = json.loads(msg.content or "{}")
                text = content.get("text", "").strip()
                if not text or not chat_id:
                    return

                # /help
                if text.lower() == "/help":
                    self._dispatch(self._send_text(chat_id, HELP_TEXT))
                    return

                # /agent (list)
                if text.lower() == "/agent":
                    self._dispatch(self._send_agent_list(chat_id))
                    return

                # /agent <name> <message>
                if text.lower().startswith("/agent "):
                    parts = text[7:].split(" ", 1)
                    agent_name = parts[0].strip()
                    msg_text = parts[1].strip() if len(parts) > 1 else ""
                    if not msg_text:
                        self._dispatch(
                            self._send_text(chat_id, f"用法：/agent {agent_name} 你的问题")
                        )
                        return
                    self._dispatch(self._on_message(user_id, chat_id, msg_text, agent_name))
                    return

                self._dispatch(self._on_message(user_id, chat_id, text, None))
            except Exception:
                logger.exception("FeishuBot._on_message error")

        def _on_card_action(event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            resp = P2CardActionTriggerResponse()
            try:
                data = event.event
                if data is None:
                    return resp
                value = data.action.value if data.action else {}
                perm_id = value.get("perm_id", "")
                action = value.get("action", "reject")
                if perm_id:
                    self._dispatch(self._on_permission(perm_id, action))
                toast = CallBackToast()
                toast.type = "success"
                toast.content = "✅ 已允许" if action != "reject" else "❌ 已拒绝"
                resp.toast = toast
            except Exception:
                logger.exception("FeishuBot._on_card_action error")
            return resp

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_message)
            .register_p2_card_action_trigger(_on_card_action)
            .build()
        )

        while True:
            try:
                client = lark.ws.Client(
                    self._app_id,
                    self._app_secret,
                    log_level=lark.LogLevel.INFO,
                    event_handler=handler,
                )
                client.start()
            except Exception:
                logger.exception("FeishuBot WebSocket disconnected, reconnecting in 5s...")
            import time
            time.sleep(5)

    async def _send_text(self, chat_id: str, text: str) -> None:
        from app import _feishu_send
        await _feishu_send(self._app_id, self._app_secret, chat_id, text)

    async def _send_agent_list(self, chat_id: str) -> None:
        agents = await self._list_agents()
        if not agents:
            await self._send_text(chat_id, "暂无自定义 Agent")
            return
        lines = ["可用 Agent："]
        for a in agents:
            lines.append(f"• {a['name']} — {a.get('description', '')}")
        lines.append("\n用法：/agent <名称> 你的问题")
        await self._send_text(chat_id, "\n".join(lines))
