# Web Chat 需求

## REQ-1: 发送消息
When the user submits text via the chat input,
the system shall send it to OpenCode and display the reply,
so that the user can converse with the agent.

## REQ-2: 启动初始化
When the server starts,
the system shall create an OpenCode session,
so that subsequent messages have a conversation context.

## REQ-3: 页面访问
When the user visits the root URL,
the system shall serve a chat HTML page,
so that the user can interact without installing anything.

## REQ-4: 错误处理
When the OpenCode server returns an error,
the system shall return {"error": "message"} to the frontend,
so that the user sees a meaningful failure state.
