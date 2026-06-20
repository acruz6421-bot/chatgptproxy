"""
Camada de tool-calling estilo kimiproxy pra proxies de chat web (sem function
calling nativo). Injeta as ferramentas no prompt no formato <tool_call>{...}</tool_call>
e parseia a saida de volta pro formato tool_calls do OpenAI.
"""
import json
import re
import uuid


def compact_tools_prompt(tools) -> str:
    def serialize_object_schema(properties, required_list, prefix=""):
        lines = []
        if not properties or not isinstance(properties, dict):
            return lines
        for key, p in properties.items():
            is_req = "required" if key in required_list else "optional"
            desc = p.get("description", "")
            if p.get("enum"):
                desc += f" Options: [{', '.join(str(x) for x in p['enum'])}]"
            type_str = p.get("type", "string")
            label = f"{prefix}.{key}" if prefix else key
            
            if p.get("type") == "object" and p.get("properties"):
                lines.append(f"    * {label} (object, {is_req}){': ' + desc if desc else ''}")
                sub_lines = serialize_object_schema(p["properties"], p.get("required", []), label)
                lines.extend(sub_lines)
            else:
                lines.append(f"    * {label} ({type_str}, {is_req}){': ' + desc if desc else ''}")
        return lines

    results = []
    for t in tools or []:
        if not isinstance(t, dict):
            results.append(str(t))
            continue
        if t.get("type") != "function":
            results.append(json.dumps(t, ensure_ascii=False))
            continue
        
        fn = t.get("function", {})
        res = f"- {fn.get('name')}: {fn.get('description', '')}"
        params = fn.get("parameters")
        if isinstance(params, dict) and params.get("type") == "object" and params.get("properties"):
            param_lines = serialize_object_schema(params["properties"], params.get("required", []))
            if param_lines:
                res += f"\n  Params:\n" + "\n".join(param_lines)
        results.append(res)
    return "\n".join(results)


def build_tools_instructions(tools, tool_choice=None) -> str:
    """Bloco de system-prompt que ensina o formato <tool_call>. '' se nao houver tools."""
    if not tools or not isinstance(tools, list):
        return ""
    
    tools_info = compact_tools_prompt(tools)
    block = (
        "# TOOLS AVAILABLE\nYou have access to the following tools:\n" + tools_info + "\n\n"
        "# TOOL CALLING FORMAT (MANDATORY)\nTo use a tool, you MUST output a JSON object "
        "wrapped EXACTLY in these tags:\n"
        '<tool_call>\n{"name": "tool_name", "arguments": {"param_name": "value"}}\n</tool_call>\n\n'
        "EXAMPLE OF MULTIPLE TOOL CALLS (replace <tool_name> with a REAL name from the list above):\n"
        '<tool_call>\n{"name": "<tool_name>", "arguments": {"arg1": "value1"}}\n</tool_call>\n'
        '<tool_call>\n{"name": "<tool_name>", "arguments": {"arg1": "value2"}}\n</tool_call>\n\n'
        "CRITICAL RULES:\n"
        "0. Use ONLY the EXACT tool names listed under TOOLS AVAILABLE. Never rename, abbreviate "
        "or invent names (e.g. if the available tool is \"read\", do NOT call \"read_file\"; if it "
        "is \"edit\", do NOT call \"edit_file\"). Copy the name character-for-character.\n"
        "1. ONLY use the tags above for tool calling. NEVER output raw JSON without tags.\n"
        "2. You can call multiple tools by outputting multiple <tool_call> blocks consecutively.\n"
        "3. Do NOT output any other text after your <tool_call> blocks. Wait for the tool response.\n"
        "4. The JSON inside the tags MUST be valid and include the 'arguments' field.\n"
        "5. If you need to use a tool, do it IMMEDIATELY without preamble.\n"
        "6. DO NOT use your internal/native Python tool, Advanced Data Analysis, or Code Interpreter. They run in a remote sandbox on your servers and have NO access to the user's workspace. You MUST use ONLY the custom tools listed under TOOLS AVAILABLE (like 'glob', 'read', 'grep', or 'bash').\n\n"
    )
    if isinstance(tool_choice, dict) and tool_choice.get("function"):
        block += f"CRITICAL: You MUST call the tool \"{tool_choice['function'].get('name')}\" in this response.\n\n"
    return block


def serialize_tool_calls_for_history(tool_calls) -> str:
    """Re-serializa tool_calls antigos do historico como tags <tool_call>."""
    out = ""
    for tc in tool_calls or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out += f'\n<tool_call>{{"name": "{fn.get("name")}", "arguments": {args}}}</tool_call>'
    return out


# --- Tolerância ao "container.exec" do ChatGPT --------------------------------
# O gpt-5 às vezes ignora o protocolo <tool_call> e emite, como texto, o formato
# do sandbox nativo dele: {"cmd": ["powershell","-Command","..."]} (sem 'name').
# Em vez de brigar com o modelo no prompt, mapeamos esse shape para a ferramenta
# de shell disponível no cliente (ex.: 'bash') para o loop não travar.

_SHELL_TOOL_NAMES = ("bash", "shell", "run_command", "execute_command", "terminal", "powershell", "run", "command")


def resolve_shell_tool(tools):
    """Retorna (nome_da_ferramenta, nome_do_param_de_comando) ou (None, None)."""
    for t in tools or []:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function", {}) or {}
        name = (fn.get("name") or "").lower()
        if name in _SHELL_TOOL_NAMES:
            props = ((fn.get("parameters") or {}).get("properties") or {})
            param = "command"
            for cand in ("command", "cmd", "script", "input"):
                if cand in props:
                    param = cand
                    break
            return fn.get("name"), param
    return None, None


def _cmd_to_string(cmd) -> str:
    """Normaliza o valor de 'cmd' (lista estilo exec ou string) em uma linha de comando."""
    if isinstance(cmd, str):
        return cmd.strip()
    if isinstance(cmd, list):
        parts = [str(x) for x in cmd]
        # Desembrulha invólucros tipo ["powershell","-Command", <real>...] / ["bash","-c", <real>]
        low = [p.lower() for p in parts]
        for i, p in enumerate(low):
            if p in ("-command", "-c") and i + 1 < len(parts):
                rest = parts[i + 1:]
                return rest[0] if len(rest) == 1 else " ".join(rest)
        # Remove o executável de shell inicial, se houver
        if low and low[0] in ("powershell", "pwsh", "cmd", "bash", "sh"):
            parts = parts[1:]
        return " ".join(parts).strip()
    return ""


def _iter_json_objects(text: str):
    """Itera objetos JSON balanceados encontrados em texto livre."""
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, instr, esc, j = 0, False, False, i
            while j < n:
                ch = text[j]
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = not instr
                elif not instr:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            blob = text[i:j + 1]
                            obj = _robust_json(blob)
                            if isinstance(obj, dict):
                                yield obj
                            i = j
                            break
                j += 1
        i += 1


def recover_tool_calls_from_text(text: str, tools):
    """Última linha de defesa: extrai tool calls de texto que o modelo emitiu FORA
    das tags <tool_call> — tanto o JSON cru {"name","arguments"} quanto o
    container.exec {"cmd": ...}. Retorna lista de tool calls (pode ser vazia)."""
    if not text or "{" not in text:
        return []
    shell_name, shell_param = resolve_shell_tool(tools)
    out = []
    seen = set()
    for obj in _iter_json_objects(text):
        name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
        if name:
            tc = StreamingToolParser()._build_tool_call(obj)
        elif "cmd" in obj and shell_name:
            cmd = _cmd_to_string(obj.get("cmd"))
            if not cmd:
                continue
            tc = {"id": "call_" + uuid.uuid4().hex[:24], "name": shell_name, "arguments": {shell_param: cmd}}
        else:
            continue
        key = (tc["name"], json.dumps(tc["arguments"], sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(tc)
    return out


def _fix_lone_backslashes(s: str) -> str:
    """Escapa '\\' que NAO seja escape JSON valido (\\" \\\\ \\/ \\b \\f \\n \\r \\t \\u).
    Conserta paths Windows ("G:\\src\\x.py") e regex ("\\d+") com backslash solitario
    — JSON invalido que quebrava json.loads e fazia a tool call sumir/corromper no
    cliente (OpenCode/Cline). Preserva escapes validos. ADITIVO: JSON ja-valido intacto."""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt and nxt in '"\\/bfnrtu':
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _robust_json(s: str):
    s = s.strip()
    s = re.sub(r"^```json\s*", "", s)
    s = re.sub(r"```$", "", s).strip()
    i = s.find("{")
    if i == -1:
        return None
    # FIX 2026-06-15: escapa backslashes solitarios (paths Windows) antes do loads.
    s = _fix_lone_backslashes(s[i:])
    try:
        return json.loads(s)
    except Exception:
        pass
    # tenta fechar chaves balanceando
    try:
        depth = 0
        end = -1
        instr = False
        esc = False
        for idx, ch in enumerate(s):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                instr = not instr
                continue
            if instr:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end != -1:
            return json.loads(s[: end + 1])
    except Exception:
        pass
    return None


class StreamingToolParser:
    """Extrai blocos <tool_call>{...}</tool_call> de um stream de texto com robustez
    contra drift de formato e tags mal-formadas (plural, espacada, JSON cru, etc.)."""

    START = "<tool_call>"
    END = "</tool_call>"

    def __init__(self):
        self.buffer = ""
        self.inside = False
        self.emitted = 0
        self.emitted_text = False

    def _normalize(self, s: str) -> str:
        s = re.sub(r"<tool_calls>", "<tool_call>", s, flags=re.IGNORECASE)
        s = re.sub(r"</tool_calls>", "</tool_call>", s, flags=re.IGNORECASE)
        s = re.sub(r"<tool[_\s]call>", "<tool_call>", s, flags=re.IGNORECASE)
        s = re.sub(r"</tool[_\s]call>", "</tool_call>", s, flags=re.IGNORECASE)
        return s

    def _looks_like_json_junk(self, s: str) -> bool:
        t = s.strip()
        if not t:
            return False
        return t.startswith("`") or t.startswith("{") or t.startswith("[")

    def _try_raw_json_tool_call(self, raw: str) -> dict | None:
        s = raw.strip()
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```$", "", s).strip()
        idx = s.find("{")
        if idx == -1:
            return None
        s = s[idx:]
        obj = _robust_json(s)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
            has_args = "arguments" in obj or "parameters" in obj or "args" in obj
            if name and (has_args or len(obj) > 1):
                return self._build_tool_call(obj)
        return None

    def _build_tool_call(self, obj: dict) -> dict:
        name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
        if isinstance(name, dict):
            name = name.get("name")
        raw_args = obj.get("arguments")
        if raw_args is None:
            raw_args = obj.get("parameters")
        if raw_args is None:
            raw_args = obj.get("args")
            
        if raw_args is None:
            args = {k: v for k, v in obj.items() if k not in ("name", "tool", "tool_name", "function")}
        elif isinstance(raw_args, str):
            try:
                args = json.loads(_fix_lone_backslashes(raw_args))
            except Exception:
                try:
                    args = _robust_json(raw_args) or {}
                except Exception:
                    args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
            
        return {"id": "call_" + uuid.uuid4().hex[:24], "name": str(name), "arguments": args}

    def feed(self, chunk: str):
        self.buffer = self._normalize(self.buffer + chunk)
        text = ""
        tool_calls = []

        while len(self.buffer) > 0:
            if not self.inside:
                start_idx = self.buffer.find(self.START)
                if start_idx != -1:
                    pre = self.buffer[:start_idx]
                    if pre and self.emitted == 0 and not self._looks_like_json_junk(pre):
                        text += pre
                        if pre.strip():
                            self.emitted_text = True
                    self.inside = True
                    self.buffer = self.buffer[start_idx + len(self.START):]
                else:
                    head = self.buffer.lstrip()
                    if self.emitted == 0 and not self.emitted_text and (head.startswith("{") or head.startswith("`")):
                        break

                    flush_index = len(self.buffer)
                    for i in range(1, len(self.START)):
                        if self.buffer.endswith(self.START[:i]):
                            flush_index = len(self.buffer) - i
                            break
                    pre = self.buffer[:flush_index]
                    if pre and self.emitted == 0:
                        text += pre
                        if pre.strip():
                            self.emitted_text = True
                    self.buffer = self.buffer[flush_index:]
                    break
            else:
                end_idx = self.buffer.find(self.END)
                if end_idx != -1:
                    raw = self.buffer[:end_idx].strip()
                    tc = self._try_raw_json_tool_call(raw)
                    if not tc:
                        obj = _robust_json(raw)
                        if obj:
                            tc = self._build_tool_call(obj)
                    if tc:
                        tool_calls.append(tc)
                        self.emitted += 1
                    self.inside = False
                    self.buffer = self.buffer[end_idx + len(self.END):]
                else:
                    break

        return text, tool_calls

    def flush(self):
        text = ""
        tool_calls = []
        remaining = self.buffer
        self.buffer = ""
        if not remaining:
            return text, tool_calls

        if self.inside:
            tc = self._try_raw_json_tool_call(remaining)
            if not tc:
                obj = _robust_json(remaining)
                if obj:
                    tc = self._build_tool_call(obj)
            if tc:
                tool_calls.append(tc)
                self.emitted += 1
            elif self.emitted == 0:
                text = self.START + remaining
            return text, tool_calls

        if self.emitted == 0:
            tc = self._try_raw_json_tool_call(remaining)
            if tc:
                tool_calls.append(tc)
                self.emitted += 1
            elif not self.emitted_text:
                text = remaining

        return text, tool_calls

    def count(self):
        return self.emitted
