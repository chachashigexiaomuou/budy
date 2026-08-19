# Streaming API Spec

## POST /api/chat/stream
Request: {"text": string}
Response: text/event-stream

Events emitted to client:
- {"type": "text", "content": "<full text so far>"}
- {"type": "done"}

Internal behavior:
1. POST to OpenCode /session/{id}/prompt_async (returns 204)
2. Subscribe to GET OpenCode /global/event (SSE)
3. OpenCode SSE format: "data: {json}\n\n"
   - JSON: {"payload": {"type": "event_type", "properties": {...}}}
4. When payload.type == "message.part.updated":
   - Check properties.part.type == "text"
   - Emit {"type": "text", "content": properties.part.text}
5. When payload.type == "session.status" and properties.status.type == "idle":
   - Emit {"type": "done"}
   - Close stream

## Frontend changes
- Replace fetch+json with fetch+ReadableStream
- Create assistant message bubble, update textContent on each "text" event
- Stop on "done" event
