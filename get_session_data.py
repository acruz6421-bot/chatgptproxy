import os
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
PROFILE = str(HERE / "chatgpt_profile")
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
URL = "https://chatgpt.com/api/auth/session"


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
    print("[*] Iniciando Playwright headful...")
    executable = CHROME if os.path.exists(CHROME) else None
    
    with sync_playwright() as p:
        launch_args = {
            "user_data_dir": PROFILE,
            "headless": False, # Executar headful para furar o Cloudflare
            "args": ["--disable-blink-features=AutomationControlled", "--no-first-run"],
            "viewport": {"width": 800, "height": 600},
        }
        if executable:
            launch_args["executable_path"] = executable

        ctx = p.chromium.launch_persistent_context(**launch_args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        
        print("[*] Acessando a URL de sessao (domcontentloaded)...")
        page.goto(URL, wait_until="domcontentloaded")
        
        print("[*] Aguardando 5 segundos para carregamento da sessao...")
        page.wait_for_timeout(5000)
        
        text = page.locator("body").inner_text().strip()
        
        try:
            data = json.loads(text)
            token = data.get("accessToken")
            if not token:
                print("[!] Erro: Access Token nao encontrado. Sua sessao pode ter expirado ou nao foi salva.")
                print(f"[*] Resposta do servidor: {text[:200]}")
                ctx.close()
                sys.exit(1)
                
            cookies = ctx.cookies()
            cookie_str = build_cookie_string(cookies)
            ua = page.evaluate("navigator.userAgent")
            
            update_env(token, cookie_str, ua)
            print("[+] Sucesso: Sessao e cookies atualizados no .env!")
        except Exception as e:
            print(f"[!] Falha ao analisar o JSON da sessao: {e}")
            print(f"[*] Resposta do servidor: {text[:200]}")
            ctx.close()
            sys.exit(1)
            
        ctx.close()


if __name__ == "__main__":
    main()
