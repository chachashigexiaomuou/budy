# API Spec

## GET /
Response: HTML chat page

## POST /api/chat
Request: {"text": string}
Response: {"reply": string} | {"error": string}

Behavior:
1. Forward text to OpenCode POST /session/{id}/message
2. Extract text from response parts
3. Return as {"reply": "extracted text"}
