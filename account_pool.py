import os
import json
import time
import uuid
import threading
from pathlib import Path
from datetime import datetime

ACCOUNTS_PATH = Path(os.environ.get("ACCOUNTS_JSON_PATH") or (Path(__file__).resolve().parent / "accounts.json"))
_lock = threading.Lock()
_cache = {"mtime": 0.0, "data": None}

def _read_accounts_raw() -> list[dict]:
    if not ACCOUNTS_PATH.exists():
        return []
    try:
        content = ACCOUNTS_PATH.read_text(encoding="utf-8")
        if not content.strip():
            return []
        obj = json.loads(content)
        return obj.get("accounts", []) if isinstance(obj, dict) else []
    except Exception as e:
        print(f"[chatgpt-pool] Erro ao ler accounts.json diretamente: {e}")
        return []

def load_accounts() -> list[dict]:
    with _lock:
        mtime = ACCOUNTS_PATH.stat().st_mtime if ACCOUNTS_PATH.exists() else 0.0
        if _cache["data"] is None or mtime != _cache["mtime"]:
            _cache["data"] = _read_accounts_raw()
            _cache["mtime"] = mtime
        return [dict(a) for a in _cache["data"]]  # Defensively copy items

def save_accounts(accounts: list[dict]) -> None:
    with _lock:
        payload = {"version": 1, "accounts": accounts}
        tmp = ACCOUNTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(ACCOUNTS_PATH))
        _cache["data"] = [dict(a) for a in accounts]
        _cache["mtime"] = ACCOUNTS_PATH.stat().st_mtime

def select_account(exclude_ids: set[str] | None = None) -> dict | None:
    exclude_ids = exclude_ids or set()
    now = int(time.time())
    accounts = load_accounts()
    
    candidates = [
        a for a in accounts
        if not a.get("disabled") 
        and a.get("cooldown_until", 0) <= now 
        and a["id"] not in exclude_ids
    ]
    if not candidates:
        return None
        
    # Critério de seleção: Menos usada recentemente (LRU) -> Menos requisições no geral (sucessos + erros)
    candidates.sort(key=lambda a: (
        a.get("last_used_at") or "", 
        a.get("success_count", 0) + a.get("error_count", 0)
    ))
    return candidates[0]

def _mutate_account(account_id: str, mutator) -> bool:
    with _lock:
        accounts = _read_accounts_raw()
        found = False
        for a in accounts:
            if a["id"] == account_id:
                mutator(a)
                found = True
                break
        if not found:
            return False
            
        payload = {"version": 1, "accounts": accounts}
        tmp = ACCOUNTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(ACCOUNTS_PATH))
        _cache["data"] = accounts
        _cache["mtime"] = ACCOUNTS_PATH.stat().st_mtime
        return True

def mark_success(account_id: str) -> None:
    def m(a):
        a["success_count"] = a.get("success_count", 0) + 1
        a["consecutive_failures"] = 0
        a["last_used_at"] = datetime.now().astimezone().isoformat()
    _mutate_account(account_id, m)

def mark_failure(account_id: str, status_code: int) -> None:
    def m(a):
        a["error_count"] = a.get("error_count", 0) + 1
        a["consecutive_failures"] = a.get("consecutive_failures", 0) + 1
        a["last_used_at"] = datetime.now().astimezone().isoformat()
        if status_code in (403, 429):
            a["cooldown_until"] = int(time.time()) + 15 * 60
        if a.get("consecutive_failures", 0) >= 3:
            a["disabled"] = True
            a["disabled_reason"] = f"{a['consecutive_failures']}+ falhas consecutivas (último erro: HTTP {status_code})"
    _mutate_account(account_id, m)

def disable_account(account_id: str, reason: str = "Manually disabled") -> bool:
    def m(a):
        a["disabled"] = True
        a["disabled_reason"] = reason
    return _mutate_account(account_id, m)

def enable_account(account_id: str) -> bool:
    def m(a):
        a["disabled"] = False
        a["disabled_reason"] = None
        a["consecutive_failures"] = 0
        a["cooldown_until"] = 0
    return _mutate_account(account_id, m)

def remove_account(account_id: str) -> bool:
    with _lock:
        accounts = _read_accounts_raw()
        original_len = len(accounts)
        accounts = [a for a in accounts if a["id"] != account_id]
        if len(accounts) == original_len:
            return False
            
        payload = {"version": 1, "accounts": accounts}
        tmp = ACCOUNTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(ACCOUNTS_PATH))
        _cache["data"] = accounts
        _cache["mtime"] = ACCOUNTS_PATH.stat().st_mtime
        return True
