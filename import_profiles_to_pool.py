"""
Importa sessões já logadas dos perfis 'chatgpt_profiles/chatgpt_profile_<id>'
para o pool (accounts.json), sem refazer login interativo.

Para cada perfil que tiver uma sessão válida, abre o browser (headful, pra furar
o Cloudflare), acessa /api/auth/session, captura o accessToken + cookies e faz
upsert da conta no pool com id 'profile-<id>'.

Uso:
    python import_profiles_to_pool.py            # varre todos os perfis encontrados
    python import_profiles_to_pool.py 1 2 3      # importa só os perfis indicados
"""
import os
import re
import sys
import json
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

import account_pool as pool

HERE = Path(__file__).resolve().parent
PROFILES_DIR = HERE / "chatgpt_profiles"
SESSION_URL = "https://chatgpt.com/api/auth/session"


def find_chrome_executable() -> str | None:
    if os.environ.get("CHROME_PATH") and os.path.exists(os.environ["CHROME_PATH"]):
        return os.environ["CHROME_PATH"]
    home = Path.home()
    for d in (home / "AppData/Local/ms-playwright", home / ".cache/ms-playwright",
              home / "Library/Caches/ms-playwright"):
        if d.exists():
            for path in d.glob("**/chrome*"):
                if path.is_file() and path.name in ("chrome.exe", "chrome", "chromium"):
                    return str(path)
    candidates = []
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    elif sys.platform.startswith("linux"):
        candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


CHROME = find_chrome_executable()


def build_cookie_string(cookies) -> str:
    return "; ".join(f'{c["name"]}={c["value"]}' for c in cookies if "chatgpt.com" in c.get("domain", ""))


def discover_profile_ids() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    ids = []
    for p in PROFILES_DIR.iterdir():
        m = re.match(r"chatgpt_profile_(.+)$", p.name)
        if p.is_dir() and m:
            ids.append(m.group(1))
    return sorted(ids, key=lambda s: (len(s), s))


def extract_session(profile_id: str, executable: str | None):
    """Retorna (token, cookie_str, ua) ou None se a sessão for inválida/expirada."""
    profile_path = str(PROFILES_DIR / f"chatgpt_profile_{profile_id}")
    if not Path(profile_path).exists():
        print(f"[!] perfil {profile_id}: pasta inexistente ({profile_path})")
        return None

    with sync_playwright() as p:
        launch_args = {
            "user_data_dir": profile_path,
            "headless": False,  # headful pra passar pelo Cloudflare
            "args": ["--disable-blink-features=AutomationControlled", "--no-first-run"],
            "viewport": {"width": 800, "height": 600},
        }
        if executable:
            launch_args["executable_path"] = executable

        ctx = p.chromium.launch_persistent_context(**launch_args)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(SESSION_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            text = page.locator("body").inner_text().strip()
            try:
                data = json.loads(text)
            except Exception:
                print(f"[!] perfil {profile_id}: resposta não-JSON (Cloudflare?). Trecho: {text[:120]}")
                return None
            token = data.get("accessToken")
            if not token:
                print(f"[!] perfil {profile_id}: sessão sem accessToken (expirada/deslogada).")
                return None
            cookie_str = build_cookie_string(ctx.cookies())
            ua = page.evaluate("navigator.userAgent")
            return token, cookie_str, ua
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def upsert(accounts: list[dict], profile_id: str, token: str, cookie_str: str, ua: str):
    account_id = f"profile-{profile_id}"
    existing = next((a for a in accounts if a.get("id") == account_id), None)
    if existing:
        existing.update({
            "access_token": token, "cookie": cookie_str, "user_agent": ua,
            "disabled": False, "disabled_reason": None,
            "consecutive_failures": 0, "cooldown_until": 0,
        })
        print(f"[+] conta '{existing.get('name')}' (id={account_id}) atualizada.")
    else:
        accounts.append({
            "id": account_id, "name": f"chatgpt-account-{profile_id}",
            "access_token": token, "cookie": cookie_str, "user_agent": ua,
            "added_at": datetime.now().astimezone().isoformat(), "last_used_at": None,
            "success_count": 0, "error_count": 0, "consecutive_failures": 0,
            "cooldown_until": 0, "disabled": False, "disabled_reason": None, "notes": "",
        })
        print(f"[+] conta 'chatgpt-account-{profile_id}' (id={account_id}) adicionada ao pool.")


def main():
    ids = sys.argv[1:] or discover_profile_ids()
    if not ids:
        print(f"[!] nenhum perfil encontrado em {PROFILES_DIR}")
        sys.exit(1)

    print(f"[*] perfis a importar: {', '.join(ids)}")
    print(f"[*] browser: {CHROME or 'padrão do Playwright'}")

    accounts = pool.load_accounts()
    imported = 0
    for pid in ids:
        print("-" * 56)
        print(f"[*] abrindo perfil {pid}...")
        res = extract_session(pid, CHROME if CHROME and os.path.exists(CHROME) else None)
        if res:
            upsert(accounts, pid, *res)
            imported += 1

    if imported:
        pool.save_accounts(accounts)
    print("=" * 56)
    print(f"[*] importadas {imported}/{len(ids)} contas. Total no pool: {len(accounts)}")
    if imported:
        print("[*] reinicie o servidor (start-chatgptproxy.cmd) e cheque http://localhost:3535/health")


if __name__ == "__main__":
    main()
