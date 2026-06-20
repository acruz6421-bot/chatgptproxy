"""
Cliente ChatGPT Web (chatgpt.com) via curl_cffi (impersonate Chrome).
Realiza chamadas autenticadas usando o Access Token e cookies capturados do navegador.
Gera o token de Proof of Work (PoW) do Sentinel localmente para validar as requisições.
"""
import os
import re
import json
import uuid
import time
import random
from base64 import b64encode
from datetime import datetime
from typing import Iterator
from zoneinfo import ZoneInfo
from curl_cffi import requests as creq
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://chatgpt.com"
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"


class ChatGPTAuthFailure(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class Challenges:
    @staticmethod
    def encode(e) -> str:
        e_str = json.dumps(e, separators=(",", ":"))
        return b64encode(e_str.encode("utf-8")).decode()

    @staticmethod
    def generate_token(config: list) -> str:
        t0 = time.time() * 1000
        try:
            config_copy = list(config)
            config_copy[3] = 1
            config_copy[9] = round(time.time() * 1000 - t0)
            return "gAAAAAC" + Challenges.encode(config_copy)
        except Exception as e:
            err_b64 = Challenges.encode(str(e))
            return "error_" + err_b64

    @staticmethod
    def mod(e: str) -> str:
        t = 2166136261
        for ch in e:
            t ^= ord(ch)
            t = (t * 16777619) & 0xFFFFFFFF

        t ^= (t >> 16)
        t = (t * 2246822507) & 0xFFFFFFFF
        t ^= (t >> 13)
        t = (t * 3266489909) & 0xFFFFFFFF
        t ^= (t >> 16)

        return f"{t:08x}"

    @staticmethod
    def _runCheck(t0: int, seed: str, difficulty: str, nonce: int, config: list) -> str:
        config_copy = list(config)
        config_copy[3] = nonce
        config_copy[9] = round(time.time() * 1000 - t0)

        i = Challenges.encode(config_copy)
        # FNV-1a hash
        hashed = Challenges.mod(seed + i)
        if hashed[:len(difficulty)] <= difficulty:
            return f"{i}~S"
        return None

    @staticmethod
    def solve_pow(seed: str, difficulty: str, config: list) -> str:
        t0 = int(time.time() * 1000)
        # Iterar ate 500k nonces
        for i in range(500000):
            res = Challenges._runCheck(t0, seed, difficulty, i, config)
            if res:
                return "gAAAAAB" + res
        # Fallback de seguranca caso nao ache (improvavel para dificuldades baixas)
        return ""


class ChatGPTClient:
    def __init__(self, custom_access_token: str = None, custom_cookie: str = None, custom_ua: str = None):
        self.access_token = (custom_access_token or os.getenv("CHATGPT_ACCESS_TOKEN", "")).strip()
        self.cookie_str = (custom_cookie or os.getenv("CHATGPT_COOKIE", "")).strip()
        self.user_agent = (custom_ua or os.getenv("CHATGPT_UA", DEFAULT_UA)).strip()

        if not self.access_token:
            raise RuntimeError("CHATGPT_ACCESS_TOKEN nao configurada. Execute o login primeiro.")

        self.session = creq.Session(impersonate="chrome")
        
        # Carregar cookies na sessao do curl_cffi
        if self.cookie_str:
            self._load_cookies(self.cookie_str)

        # Extrair device-id a partir do cookie 'oai-did'
        self.device_id = self._extract_device_id()
        self.build_hash = None
        self.timezone = "America/Sao_Paulo"
        self.start_time = int(time.time() * 1000) - random.randint(2000, 5000) # Em milissegundos
        self.sid = str(uuid.uuid4())
        
        # Gerar react val como o do navegador
        suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=11))
        self.react_val = f"_reactListening{suffix}"
        self.window_val = "showDirectoryPicker"

    def _load_cookies(self, cookie_str: str):
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip(), domain=".chatgpt.com")

    def _extract_device_id(self) -> str:
        # Tenta pegar 'oai-did' dos cookies
        did = self.session.cookies.get("oai-did")
        if not did:
            # Fallback gera um UUID persistente por execucao
            did = str(uuid.uuid4())
        return did

    def fetch_build_hash(self):
        """Acessa a pagina principal para extrair o build hash atual do ChatGPT."""
        if self.build_hash:
            return self.build_hash
        
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
        try:
            r = self.session.get(BASE_URL, headers=headers, timeout=15)
            match = re.search(r'data-build="([^"]+)"', r.text)
            if match:
                self.build_hash = match.group(1)
                print(f"[chatgpt] Build hash detectado: {self.build_hash}")
            else:
                # Fallback detectado no log .mitm
                self.build_hash = "prod-497f333866796e100096ad083b51ca949d22e751"
                print(f"[chatgpt] Build hash nao encontrado. Usando fallback: {self.build_hash}")
        except Exception as e:
            print(f"[chatgpt] Erro ao extrair build hash: {e}")
            self.build_hash = "prod-497f333866796e100096ad083b51ca949d22e751"
        
        return self.build_hash

    def _get_config_array(self) -> list:
        # Monta a data no formato do browser
        tz_name = "Hora padrão de Brasília" # Pode ser estatico ou dinâmico
        now = datetime.now(ZoneInfo(self.timezone))
        gmt_str = now.strftime(f"%a %b %d %Y %H:%M:%S GMT%z ({tz_name})")
        
        # Emula exatamente a lista de 25 chaves/caracteristicas do navegador
        # Utiliza o caractere Unicode \u2212 para o menos em hardwareConcurrency-12
        return [
            4000, # Canvas / screen descriptor
            gmt_str,
            4294967296, # Screen fingerprinting
            random.random(), # Nonce
            self.user_agent,
            None,
            self.build_hash,
            "pt-BR",
            "pt-BR,pt,en-US,en",
            random.random(), # Time delta
            "hardwareConcurrency\u221212", # hardwareConcurrency com o sinal de menos correto
            self.react_val,
            self.window_val,
            1500 + random.random(), # Performance time check
            self.sid,
            "utm_source,utm_medium,utm_campaign,c_id,c_agid,c_crid,c_kwid,c_ims,c_pms,c_nw,c_dvc,gad_source,gad_campaignid,gbraid,gclid",
            12, # Navigator key count
            self.start_time, # Start time em milissegundos
            0,
            0,
            0,
            0,
            0,
            0,
            0
        ]

    def get_requirements(self) -> tuple:
        """Executa a nova sequência de 5 passos do Sentinel/Conversation prepare e finalize."""
        self.fetch_build_hash()
        
        # Cabeçalhos base de autenticação e dispositivo
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": self.user_agent,
            "oai-client-version": self.build_hash,
            "oai-device-id": self.device_id,
            "oai-language": "pt-BR",
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com"
        }
        
        config = self._get_config_array()
        p_value = Challenges.generate_token(config)
        
        # 1. POST /sentinel/chat-requirements/prepare
        prepare_url = f"{BASE_URL}/backend-api/sentinel/chat-requirements/prepare"
        prepare_payload = {"p": p_value}
        
        print("[chatgpt] Executando chat-requirements/prepare...")
        r_prep = self.session.post(prepare_url, json=prepare_payload, headers=headers, timeout=15)
        if r_prep.status_code != 200:
            snippet = r_prep.text[:300]
            if r_prep.status_code in (401, 403, 429) or "unusual_activity" in snippet:
                raise ChatGPTAuthFailure(r_prep.status_code, f"Bloqueio/Expirado no Sentinel Prepare (HTTP {r_prep.status_code}): {snippet}")
            raise RuntimeError(f"Falha no Sentinel Prepare (HTTP {r_prep.status_code}): {snippet}")
            
        prep_data = r_prep.json()
        prepare_token = prep_data.get("prepare_token")
        pow_challenge = prep_data.get("proofofwork")
        
        if not pow_challenge or not prepare_token:
            raise RuntimeError("Prepare response nao retornou dados de PoW ou prepare_token.")
            
        # 2. POST /f/conversation/prepare para obter conduit_token
        conduit_token = ""
        try:
            print("[chatgpt] Executando f/conversation/prepare...")
            conduit_url = f"{BASE_URL}/backend-api/f/conversation/prepare"
            dummy_parent_id = str(uuid.uuid4())
            conduit_payload = {
                "action": "next",
                "conversation_id": None,
                "parent_message_id": dummy_parent_id,
                "model": "auto",
                "client_prepare_state": "none",
                "timezone_offset_min": 180,
                "timezone": self.timezone,
                "conversation_mode": {"kind": "primary_assistant"},
                "system_hints": [],
                "supports_buffering": True,
                "supported_encodings": ["v1"],
                "client_contextual_info": {"app_name": "chatgpt.com"}
            }
            r_cond = self.session.post(conduit_url, json=conduit_payload, headers=headers, timeout=15)
            if r_cond.status_code == 200:
                conduit_token = r_cond.json().get("conduit_token", "")
                print(f"[chatgpt] conduit_token obtido com sucesso.")
        except Exception as e:
            print(f"[chatgpt] Alerta: Falha ao obter conduit_token: {e}")

        # 3. Resolver a PoW
        seed = pow_challenge.get("seed")
        difficulty = pow_challenge.get("difficulty")
        
        print(f"[pow] Resolvendo PoW com semente: {seed[:15]}... e dificuldade: {difficulty}")
        proof_token = Challenges.solve_pow(seed, difficulty, config)
        print(f"[pow] PoW resolvido: {proof_token[:25]}...")
        
        # 4. POST /sentinel/chat-requirements/finalize
        finalize_url = f"{BASE_URL}/backend-api/sentinel/chat-requirements/finalize"
        finalize_payload = {
            "prepare_token": prepare_token,
            "proofofwork": proof_token,
            "turnstile": ""
        }
        
        print("[chatgpt] Executando chat-requirements/finalize...")
        r_fin = self.session.post(finalize_url, json=finalize_payload, headers=headers, timeout=15)
        if r_fin.status_code != 200:
            snippet = r_fin.text[:300]
            if r_fin.status_code in (401, 403, 429) or "unusual_activity" in snippet:
                raise ChatGPTAuthFailure(r_fin.status_code, f"Bloqueio/Expirado no Sentinel Finalize (HTTP {r_fin.status_code}): {snippet}")
            raise RuntimeError(f"Falha no Sentinel Finalize (HTTP {r_fin.status_code}): {snippet}")
            
        fin_data = r_fin.json()
        req_token = fin_data.get("token")
        
        if not req_token:
            raise RuntimeError("Finalize response nao retornou o chat requirements token.")
            
        return req_token, proof_token, conduit_token

    def stream_completion(self, prompt: str) -> Iterator[dict]:
        """Inicia uma conversa e yielda chunks de texto do ChatGPT."""
        req_token, proof_token, conduit_token = self.get_requirements()
        
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": self.user_agent,
            "oai-client-version": self.build_hash,
            "oai-device-id": self.device_id,
            "oai-language": "pt-BR",
            "openai-sentinel-chat-requirements-token": req_token,
            "openai-sentinel-proof-token": proof_token,
            "openai-sentinel-turnstile-token": "", 
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com",
            "Accept-Encoding": "gzip, deflate, br"
        }
        if conduit_token:
            headers["x-conduit-token"] = conduit_token

        # Payload para a conversa na rota /f/conversation
        payload = {
            "action": "next",
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                    "metadata": {}
                }
            ],
            "parent_message_id": "client-created-root", # Sempre inicia uma nova conversa
            "model": "auto", 
            "timezone_offset_min": 180,
            "timezone": self.timezone,
            "client_prepare_state": "success",
            "enable_message_followups": True,
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "client_contextual_info": {
                "is_dark_mode": True,
                "time_since_loaded": random.randint(15, 30),
                "page_height": 1080,
                "page_width": 1920,
                "pixel_ratio": 1,
                "screen_height": 1080,
                "screen_width": 1920,
                "app_name": "chatgpt.com"
            }
        }

        url = f"{BASE_URL}/backend-api/f/conversation"
        print("[chatgpt] Enviando conversa para f/conversation...")
        r = self.session.post(url, json=payload, headers=headers, stream=True, timeout=120)
        
        if r.status_code != 200:
            snippet = r.text[:300]
            if r.status_code in (401, 403, 429) or "unusual_activity" in snippet:
                raise ChatGPTAuthFailure(r.status_code, f"Bloqueio/Expirado no ChatGPT (HTTP {r.status_code}): {snippet}")
            raise RuntimeError(f"Erro ao criar conversa (HTTP {r.status_code}): {snippet}")

        last_text = ""
        for raw_line in r.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", "replace").strip()
            
            if line.startswith("event:"):
                continue
                
            if not line.startswith("data:"):
                continue
            
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            
            try:
                obj = json.loads(data_str)
                
                # Tratar o novo formato de delta patch (operacao: append ou patch com sub-ops)
                if isinstance(obj, dict):
                    # Caso 1: Patch batch contendo lista de operacoes
                    if obj.get("o") == "patch" and isinstance(obj.get("v"), list):
                        for op in obj.get("v"):
                            if isinstance(op, dict) and op.get("o") == "append" and op.get("p", "").startswith("/message/content/parts/"):
                                val = op.get("v")
                                if isinstance(val, str) and val:
                                    yield {"type": "content", "text": val}
                        continue
                        
                    # Caso 2: Operacao direta de append
                    if obj.get("o") == "append" and obj.get("p", "").startswith("/message/content/parts/"):
                        val = obj.get("v")
                        if isinstance(val, str) and val:
                            yield {"type": "content", "text": val}
                            continue

                    # Caso 3: Shorthand/Implicit append (apenas {"v": "texto"} sem "o" ou "p")
                    if "v" in obj and isinstance(obj["v"], str) and "o" not in obj and "p" not in obj:
                        yield {"type": "content", "text": obj["v"]}
                        continue
                    
                    # Suporte ao formato legado (retrocompatibilidade)
                    msg = obj.get("message")
                    if msg and msg.get("author", {}).get("role") == "assistant":
                        parts = msg.get("content", {}).get("parts", [])
                        if parts and isinstance(parts[0], str):
                            current_text = parts[0]
                            if current_text.startswith(last_text):
                                delta = current_text[len(last_text):]
                                last_text = current_text
                                if delta:
                                    yield {"type": "content", "text": delta}
            except Exception:
                continue
        
        yield {"type": "done", "text": ""}
