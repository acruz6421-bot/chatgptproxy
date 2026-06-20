"""
chatgptproxy - servidor OpenAI-compatible para o ChatGPT Web (chatgpt.com).
End-points: /health, /v1/models, /v1/chat/completions (streaming e nao-streaming).
Suporte a rotação de contas e injeção de tool-calling por prompt com depuração.
"""
import os
import json
import time
import uuid
import re
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from dotenv import load_dotenv

from chatgpt_client import ChatGPTClient, ChatGPTAuthFailure
from tool_calling import StreamingToolParser, build_tools_instructions, serialize_tool_calls_for_history
import account_pool as ap

load_dotenv()

app = FastAPI(title="chatgptproxy")
API_KEY = os.getenv("API_KEY", "chatgpt-local-dev").strip()

SUPPORTED_MODELS = [
    {"id": "gpt-5-3", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-5", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-2", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-1", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-mini", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-3-mini", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-5-4-t-mini", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "research", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "auto", "object": "model", "created": 1745000000, "owned_by": "openai"},
    {"id": "gpt-4o-mini", "object": "model", "created": 1715000000, "owned_by": "openai"},
    {"id": "gpt-4o", "object": "model", "created": 1715000000, "owned_by": "openai"},
    {"id": "gpt-4-turbo", "object": "model", "created": 1715000000, "owned_by": "openai"},
]


def log_debug_info(payload: dict, prompt: str, content: str, tool_calls=None):
    try:
        log_file = Path(__file__).resolve().parent / "debug_completions.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"TIMESTAMP: {datetime.now().isoformat()}\n")
            f.write(f"PAYLOAD: {json.dumps(payload, ensure_ascii=False)}\n\n")
            f.write(f"PROMPT SENT TO UPSTREAM:\n{prompt}\n\n")
            f.write(f"RESPONSE TEXT: {content}\n")
            f.write(f"RESPONSE TOOLS: {json.dumps(tool_calls, ensure_ascii=False)}\n")
            f.write(f"{'='*80}\n")
    except Exception as e:
        print("[chatgpt] Erro ao gravar debug_completions.log:", e)


def _check_auth(request: Request):
    if not API_KEY:
        return
    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def clean_and_stringify_content(content) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    parts.append("[Image]")
            else:
                parts.append(str(part))
        text = "\n".join(parts)
    else:
        text = str(content) if content is not None else ""

    if "Workspace root folder: /" in text and "Workspace root folder: /workspace" not in text:
        print(f"[chatgpt] REWRITE: removing misleading 'Workspace root folder: /' line")
        text = re.sub(r"\s*Workspace root folder: /\s*\n?", "\n", text)
        print(f"[chatgpt] REWRITE: done")

    # Clean PowerShell CLIXML headers and progress objects
    if "#< CLIXML" in text or "<Objs" in text:
        print(f"[chatgpt] REWRITE: cleaning CLIXML/progress tags from tool output")
        text = text.replace("#< CLIXML", "")
        text = re.sub(r"<Objs\b.*?</Objs>", "", text, flags=re.DOTALL)
        print(f"[chatgpt] REWRITE: done")

    return text


def format_conversation(messages: list, tools=None, tool_choice=None) -> str:
    """Consolida as mensagens do historico em um único prompt formatado para o ChatGPT Web."""
    formatted = []
    
    # Injeta instruções de tool calling se ferramentas estiverem disponiveis
    tools_block = build_tools_instructions(tools, tool_choice)
    
    # Agrupa system prompts
    system_prompts = []
    for m in messages:
        if m.get("role") == "system":
            val = clean_and_stringify_content(m.get("content"))
            if val:
                system_prompts.append(val)
                
    if tools_block:
        system_prompts.insert(0, tools_block)
        
    if system_prompts:
        formatted.append("# INSTRUÇÕES DE SISTEMA")
        formatted.append("\n\n".join(system_prompts))
        formatted.append("---")
        
    # Adiciona o historico da conversa
    formatted.append("# HISTÓRICO DE CONVERSA")
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
            
        content = clean_and_stringify_content(m.get("content"))
        # Caso o assistente tenha gerado tool calls no turno anterior, anexa-as ao prompt
        if role == "assistant":
            if isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                content = (content or "") + serialize_tool_calls_for_history(m["tool_calls"])
                
        if role == "user":
            formatted.append(f"User: {content}")
        elif role == "assistant":
            formatted.append(f"Assistant: {content}")
        elif role in ("tool", "function"):
            nm = m.get("name") or m.get("tool_call_id") or "tool"
            formatted.append(f"Tool (Resultado da ferramenta {nm}): {content}")
            
    # Injeta Nudge anti-memoria e anti-python para forcar o uso das ferramentas customizadas
    if tools and messages:
        last_role = messages[-1].get("role")
        if last_role in ("user", "tool", "function"):
            formatted.append(
                "\n[CRITICAL SYSTEM INSTRUCTION: You MUST use one of the custom tools listed under '# TOOLS AVAILABLE' (such as 'glob', 'read', 'grep', or 'bash') to investigate or act in the workspace.\n"
                "DO NOT use your built-in/native Python tool, Advanced Data Analysis, or Code Interpreter. They run in a remote sandbox on your servers and have NO access to the user's workspace. Your previous memories or internal listings about the filesystem (e.g. Linux directories like '/etc', '/var', '/usr', '/openai', '/caas_toolbox') are STALE and INCORRECT. The current workspace is different. "
                "You MUST call the custom tools immediately using the exact `<tool_call>` tag format. Choose a tool (like 'glob' or 'bash') and execute it now.]"
            )
            
    return "\n\n".join(formatted)


@app.get("/health")
def health():
    try:
        accounts = ap.load_accounts()
    except Exception:
        accounts = []
    now = int(time.time())
    total = len(accounts)
    disabled = sum(1 for a in accounts if a.get("disabled"))
    cooldown = sum(1 for a in accounts if not a.get("disabled") and a.get("cooldown_until", 0) > now)
    available = sum(1 for a in accounts if not a.get("disabled") and a.get("cooldown_until", 0) <= now)

    token_set = bool(os.getenv("CHATGPT_ACCESS_TOKEN"))
    cookie_set = bool(os.getenv("CHATGPT_COOKIE"))
    
    return {
        "status": "ok", 
        "token_set": token_set, 
        "cookie_set": cookie_set,
        "pool": {
            "total": total,
            "available": available,
            "cooldown": cooldown,
            "disabled": disabled
        }
    }


@app.get("/v1/models")
def models():
    return {"object": "list", "data": SUPPORTED_MODELS}


@app.get("/admin/accounts")
async def list_accounts(request: Request):
    _check_auth(request)
    accounts = ap.load_accounts()
    now = int(time.time())
    return {
        "total": len(accounts),
        "accounts": [
            {
                "id": a["id"],
                "name": a["name"],
                "status": (
                    "disabled" if a.get("disabled")
                    else "cooldown" if a.get("cooldown_until", 0) > now
                    else "available"
                ),
                "cooldown_remaining_s": max(0, a.get("cooldown_until", 0) - now),
                "success_count": a.get("success_count", 0),
                "error_count": a.get("error_count", 0),
                "consecutive_failures": a.get("consecutive_failures", 0),
                "last_used_at": a.get("last_used_at"),
                "added_at": a.get("added_at"),
                "disabled_reason": a.get("disabled_reason"),
            }
            for a in accounts
        ]
    }


@app.post("/admin/accounts/{id}/disable")
async def disable_account(id: str, request: Request):
    _check_auth(request)
    try:
        payload = await request.json()
        reason = payload.get("reason", "Manually disabled")
    except Exception:
        reason = "Manually disabled"
    success = ap.disable_account(id, reason)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "message": f"Account {id} disabled"}


@app.post("/admin/accounts/{id}/enable")
async def enable_account(id: str, request: Request):
    _check_auth(request)
    success = ap.enable_account(id)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "message": f"Account {id} enabled"}


@app.delete("/admin/accounts/{id}")
async def delete_account(id: str, request: Request):
    _check_auth(request)
    success = ap.remove_account(id)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "message": f"Account {id} removed"}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# Path para a gravacao do log
from pathlib import Path

@app.post("/v1/chat/completions")
def chat_completions(payload: dict, request: Request):
    _check_auth(request)
    messages = payload.get("messages", [])
    model = payload.get("model", "gpt-5-mini")
    stream = bool(payload.get("stream", False))
    tools = payload.get("tools")
    tool_choice = payload.get("tool_choice")

    # Consolida a conversa e as instruçoes em um único prompt para o ChatGPT Web
    prompt = format_conversation(messages, tools=tools, tool_choice=tool_choice)

    cmpl_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    # Para logs de auditoria de tool calling
    logged_content_parts = []
    logged_tool_calls = []

    if stream:
        tried_ids = set()
        active_acct = None
        generator = None
        first_val = None

        while True:
            acct = ap.select_account(exclude_ids=tried_ids)
            if acct is None:
                break
            try:
                print(f"[chatgpt] req#{cmpl_id} tentando conta={acct['name']} id={acct['id']}")
                client = ChatGPTClient(
                    custom_access_token=acct.get("access_token"),
                    custom_cookie=acct.get("cookie"),
                    custom_ua=acct.get("user_agent")
                )
                g = client.stream_completion(prompt)
                first_val = next(g)
                active_acct = acct
                generator = g
                break
            except ChatGPTAuthFailure as e:
                print(f"[chatgpt] conta={acct['name']} id={acct['id']} falhou com HTTP {e.status_code}")
                ap.mark_failure(acct["id"], e.status_code)
                tried_ids.add(acct["id"])
                continue
            except Exception as e:
                print(f"[chatgpt] erro inesperado na conta={acct['name']}: {e}")
                ap.mark_failure(acct["id"], 500)
                tried_ids.add(acct["id"])
                continue

        # Se pool esgotado/vazio, tenta fallback no .env
        if generator is None:
            print(f"[chatgpt] pool esgotado ou vazio. Tentando fallback no .env...")
            try:
                client = ChatGPTClient()
                g = client.stream_completion(prompt)
                first_val = next(g)
                generator = g
            except Exception as e:
                print(f"[chatgpt] fallback .env falhou: {e}")
                raise HTTPException(status_code=502, detail=f"chatgptproxy fallback error: {e}")

        def gen():
            # Inicia o chunk do Assistant
            yield _sse({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
            parser = StreamingToolParser() if tools else None
            tool_idx = 0

            def c_content(txt):
                logged_content_parts.append(txt)
                return _sse({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                             "choices": [{"index": 0, "delta": {"content": txt}, "finish_reason": None}]})

            def c_toolcall(tc, idx):
                logged_tool_calls.append(tc)
                return _sse({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                             "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}]}, "finish_reason": None}]})

            def process_event(ev):
                nonlocal tool_idx
                outs = []
                if ev["type"] == "content":
                    if parser:
                        text, tcs = parser.feed(ev["text"])
                        if text:
                            outs.append(c_content(text))
                        for tc in tcs:
                            outs.append(c_toolcall(tc, tool_idx))
                            tool_idx += 1
                    else:
                        outs.append(c_content(ev["text"]))
                return outs

            try:
                if first_val and first_val["type"] != "done":
                    for res in process_event(first_val):
                        yield res
                for ev in generator:
                    if ev["type"] == "done":
                        break
                    for res in process_event(ev):
                        yield res
                if parser:
                    text, tcs = parser.flush()
                    if text:
                        yield c_content(text)
                    for tc in tcs:
                        yield c_toolcall(tc, tool_idx)
                        tool_idx += 1

                if active_acct:
                    print(f"[chatgpt] req#{cmpl_id} conta={active_acct['name']} id={active_acct['id']} sucesso")
                    ap.mark_success(active_acct["id"])

            except Exception as e:
                print(f"[chatgpt] Erro no stream: {e}")
                yield c_content(f"\n[chatgptproxy erro: {e}]")

            finish = "tool_calls" if tool_idx > 0 else "stop"
            yield _sse({"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
            yield "data: [DONE]\n\n"

            # Escreve auditoria no log
            log_debug_info(payload, prompt, "".join(logged_content_parts), logged_tool_calls)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # non-stream
    parser = StreamingToolParser() if tools else None
    content_parts, tool_calls_out = [], []

    tried_ids = set()
    active_acct = None
    generator = None
    first_val = None

    while True:
        acct = ap.select_account(exclude_ids=tried_ids)
        if acct is None:
            break
        try:
            print(f"[chatgpt] req#{cmpl_id} tentando conta={acct['name']} id={acct['id']}")
            client = ChatGPTClient(
                custom_access_token=acct.get("access_token"),
                custom_cookie=acct.get("cookie"),
                custom_ua=acct.get("user_agent")
            )
            g = client.stream_completion(prompt)
            first_val = next(g)
            active_acct = acct
            generator = g
            break
        except ChatGPTAuthFailure as e:
            print(f"[chatgpt] conta={acct['name']} id={acct['id']} falhou com HTTP {e.status_code}")
            ap.mark_failure(acct["id"], e.status_code)
            tried_ids.add(acct["id"])
            continue
        except Exception as e:
            print(f"[chatgpt] erro inesperado na conta={acct['name']}: {e}")
            ap.mark_failure(acct["id"], 500)
            tried_ids.add(acct["id"])
            continue

    # Fallback no .env se pool esgotado/vazio
    if generator is None:
        print(f"[chatgpt] pool esgotado ou vazio. Tentando fallback no .env...")
        try:
            client = ChatGPTClient()
            g = client.stream_completion(prompt)
            first_val = next(g)
            generator = g
        except Exception as e:
            print(f"[chatgpt] fallback .env falhou: {e}")
            raise HTTPException(status_code=502, detail=f"chatgptproxy fallback error: {e}")

    def process_non_stream_event(ev):
        if ev["type"] == "content":
            if parser:
                text, tcs = parser.feed(ev["text"])
                if text:
                    content_parts.append(text)
                tool_calls_out.extend(tcs)
            else:
                content_parts.append(ev["text"])

    try:
        if first_val and first_val["type"] != "done":
            process_non_stream_event(first_val)
        for ev in generator:
            if ev["type"] == "done":
                break
            process_non_stream_event(ev)
        if parser:
            text, tcs = parser.flush()
            if text:
                content_parts.append(text)
            tool_calls_out.extend(tcs)

        if active_acct:
            print(f"[chatgpt] req#{cmpl_id} conta={active_acct['name']} id={active_acct['id']} sucesso")
            ap.mark_success(active_acct["id"])

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"chatgptproxy error: {e}")

    text_content = "".join(content_parts)
    msg = {"role": "assistant", "content": (text_content or None) if tool_calls_out else text_content}
    if tool_calls_out:
        msg["tool_calls"] = [{"index": i, "id": tc["id"], "type": "function",
                              "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                             for i, tc in enumerate(tool_calls_out)]

    # Escreve auditoria no log
    log_debug_info(payload, prompt, text_content, tool_calls_out)

    return JSONResponse({
        "id": cmpl_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls_out else "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3500"))
    print(f"\n[chatgptproxy] Iniciando servidor em http://localhost:{port}")
    print(f"[chatgptproxy] API KEY = {API_KEY}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
