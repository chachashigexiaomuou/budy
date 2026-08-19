# Permission API Spec

## POST /api/permission
Request: {"permission_id": string, "action": "once"|"always"|"reject"}
Response: {"status": "ok"}

Behavior:
- Calls POST OpenCode /session/{id}/permissions/{permId}
- Body: {"response": action}

## SSE extension (in /api/chat/stream)
When OpenCode emits permission.asked event:
- Emit to client: {"type": "permission", "id": "per_xxx", "tool": "bash", "patterns": ["echo hello"]}

## Frontend
When "permission" event received:
- Display card with tool name + patterns
- Show [Allow] and [Deny] buttons
- On click: POST /api/permission with permission_id and action
