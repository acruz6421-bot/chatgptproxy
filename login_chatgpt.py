"""
Helper de login do chatgptproxy (suporta pool multi-conta com perfis persistentes).
Abre o chatgpt.com num browser real usando o perfil selecionado.
"""
import os
import sys
import time
import json
import uuid
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

# Import pool manager
import account_pool as pool

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
def find_chrome_executable() -> str | None:
    if os.environ.get("CHROME_PATH"):
        p = os.environ.get("CHROME_PATH")
        if os.path.exists(p):
            return p
    home = Path.home()
    possible_dirs = [
        home / "AppData/Local/ms-playwright",
        home / ".cache/ms-playwright",
        home / "Library/Caches/ms-playwright"
    ]
    for d in possible_dirs:
        if d.exists():
            for path in d.glob("**/chrome*"):
                if path.is_file() and path.name in ("chrome.exe", "chrome", "chromium"):
                    return str(path)
    system_paths = []
    if sys.platform == "win32":
        system_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        system_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    elif sys.platform.startswith("linux"):
        system_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    for p in system_paths:
        if os.path.exists(p):
            return p
    return None

CHROME = find_chrome_executable()
URL = "https://chatgpt.com"


def update_env(token: str, cookie_str: str, ua: str):
    env_vars = {}
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, v = ln.split("=", 1)
                env_vars[k.strip()] = v.strip()

    env_vars["CHATGPT_ACCESS_TOKEN"] = token
    env_vars["CHATGPT_COOKIE"] = cookie_str
    env_vars["CHATGPT_UA"] = ua
    if "API_KEY" not in env_vars:
        env_vars["API_KEY"] = "chatgpt-local-dev"

    lines = []
    for k, v in env_vars.items():
        lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_cookie_string(cookies) -> str:
    wanted = []
    for c in cookies:
        dom = c.get("domain", "")
        if "chatgpt.com" in dom:
            wanted.append(f'{c["name"]}={c["value"]}')
    return "; ".join(wanted)


def main():
    profile_id = "1"
    if len(sys.argv) > 1:
        profile_id = sys.argv[1].strip()

    print("=" * 64)
    print(f" LOGIN CHATGPT (CONTA #{profile_id}) - abrindo o browser...")
    print(" Faca login na sua conta do ChatGPT na janela que abriu.")
    print(" Assim que o painel carregar, o token e cookies serao capturados.")
    print("=" * 64)

    executable = CHROME if os.path.exists(CHROME) else None
    if executable:
        print(f"[*] Usando browser customizado: {CHROME}")
    else:
        print("[*] Usando browser padrao do Playwright")

    profiles_dir = HERE / "chatgpt_profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile_path = str(profiles_dir / f"chatgpt_profile_{profile_id}")
    print(f"[*] Pasta de perfil persistente: {profile_path}")

    access_token = None

    def handle_response(response):
        nonlocal access_token
        if "/api/auth/session" in response.url:
            try:
                data = response.json()
                tok = data.get("accessToken")
                if tok:
                    access_token = tok
                    print("[+] Access Token interceptado com sucesso!")
            except Exception:
                pass

    ctx = None
    try:
        with sync_playwright() as p:
            launch_args = {
                "user_data_dir": profile_path,
                "headless": False,
                "args": ["--disable-blink-features=AutomationControlled", "--no-first-run"],
                "viewport": {"width": 1280, "height": 800},
            }
            if executable:
                launch_args["executable_path"] = executable

            ctx = p.chromium.launch_persistent_context(**launch_args)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", handle_response)
            page.goto(URL, wait_until="domcontentloaded")

            # Obter o User-Agent do proprio navegador
            ua = page.evaluate("navigator.userAgent")

            print("\n[*] Aguardando login (procurando token de sessao)...")
            print("    (limite de 5 minutos)")
            deadline = time.time() + 300
            success = False
            cookie_str = None

            while time.time() < deadline:
                cookies = ctx.cookies()
                has_session = any("session" in c["name"] or "auth" in c["name"] for c in cookies)
                if access_token and has_session:
                    cookie_str = build_cookie_string(cookies)
                    success = True
                    break
                time.sleep(2)

            if not success or not cookie_str or not access_token:
                print("[!] Timeout: login nao detectado ou token ausente.")
                ctx.close()
                sys.exit(1)

            print("\n[+] Cookies + Access Token capturados com sucesso!")
            
            # Atualiza o arquivo .env como fallback
            update_env(access_token, cookie_str, ua)
            print("[+] Credenciais default salvas no .env (fallback).")

            # Definir o ID e nome padrao a partir do ID do perfil
            account_id = f"profile-{profile_id}"
            default_name = f"chatgpt-account-{profile_id}"

            # Solicitar nome para identificar a conta no pool
            print("-" * 64)
            account_name = input(f"Digite um e-mail ou apelido para a conta {profile_id} (Pressione ENTER para '{default_name}'): ").strip()
            if not account_name:
                account_name = default_name
            print("-" * 64)

            # Carregar e atualizar/anexar no accounts.json
            accounts = pool.load_accounts()

            # Verificar se a conta ja existe pelo ID de perfil
            existing = [a for a in accounts if a.get("id") == account_id]
            if existing:
                print(f"[+] Atualizando credenciais da conta '{existing[0]['name']}' (ID: {account_id})...")
                existing[0]["name"] = account_name
                existing[0]["access_token"] = access_token
                existing[0]["cookie"] = cookie_str
                existing[0]["user_agent"] = ua
                existing[0]["disabled"] = False
                existing[0]["disabled_reason"] = None
                existing[0]["consecutive_failures"] = 0
                existing[0]["cooldown_until"] = 0
                pool.save_accounts(accounts)
                print(f"[+] Conta '{existing[0]['name']}' atualizada com sucesso!")
            else:
                new_account = {
                    "id": account_id,
                    "name": account_name,
                    "access_token": access_token,
                    "cookie": cookie_str,
                    "user_agent": ua,
                    "added_at": datetime.now().astimezone().isoformat(),
                    "last_used_at": None,
                    "success_count": 0,
                    "error_count": 0,
                    "consecutive_failures": 0,
                    "cooldown_until": 0,
                    "disabled": False,
                    "disabled_reason": None,
                    "notes": ""
                }
                accounts.append(new_account)
                pool.save_accounts(accounts)
                print(f"[+] Conta '{account_name}' adicionada com sucesso ao pool!")
                print(f"    Total de contas no pool: {len(accounts)}")

            print("[*] Fechando o browser...")
            time.sleep(2)
            ctx.close()
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
