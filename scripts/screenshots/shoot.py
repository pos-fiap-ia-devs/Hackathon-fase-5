"""Refaz as imagens de `docs/imagens/` com o mesmo enquadramento.

    python -m scripts.screenshots.shoot                 todas
    python -m scripts.screenshots.shoot dashboard.png   só as nomeadas
    python -m scripts.screenshots.shoot --refazer       apaga e recria as conversas antes

Ferramenta de desenvolvimento, só no host. Usa o Google Chrome já
instalado (`CHROME_PATH` pra apontar outro) via `playwright`, que fica no
extra `shots` e fora das dependências da aplicação -- nada disso entra na
imagem Docker.

Precisa da aplicação no ar (`./run.sh` ou `uvicorn app.web.api:app`) e do
Postgres. O estado que as capturas mostram (usuário do painel, conversas de
exemplo) é semeado pelo próprio script, em `conversas.py`.
"""

import argparse
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent.parent
SAIDA = RAIZ / "docs" / "imagens"
CONFIG = Path(__file__).parent / "shots.json"

APP = os.environ.get("APP_URL", "http://localhost:8000")
CHROME = os.environ.get(
    "CHROME_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)

# Credenciais só de desenvolvimento, criadas por este script no banco local.
EMAIL_DEMO = "corretor@demo.local"
SENHA_DEMO = "captura-de-tela-2026"


def _app_no_ar() -> bool:
    try:
        return httpx.get(f"{APP}/health", timeout=3).status_code == 200
    except Exception:
        return False


def _contexto(browser, *, largura: int, altura: int, escala: float):
    return browser.new_context(
        viewport={"width": largura, "height": altura},
        device_scale_factor=escala,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
    )


def _logar(pagina) -> None:
    pagina.goto(f"{APP}/login")
    pagina.fill('input[name="email"]', EMAIL_DEMO)
    pagina.fill('input[name="senha"]', SENHA_DEMO)
    pagina.click('button[type="submit"]')
    pagina.wait_for_url("**/dashboard", timeout=15_000)


def _esperar_htmx(pagina, seletor: str | None) -> None:
    """O painel carrega as três seções por HTMX depois do load. Sem esperar
    pelo conteúdo, a captura sai com os blocos vazios."""
    if seletor:
        pagina.wait_for_selector(seletor, timeout=20_000)
    pagina.wait_for_timeout(600)


def _tirar(pagina, shot: dict) -> Path:
    destino = SAIDA / shot["file"]
    if alvo := shot.get("rolar_ate"):
        pagina.locator(alvo).first.scroll_into_view_if_needed()
        pagina.wait_for_timeout(400)
    pagina.screenshot(path=destino)
    return destino


def _painel(browser, shot: dict, *, pagina_cache: dict) -> Path:
    altura = shot.get("altura", 900)
    # Uma aba por altura: relogar a cada captura custaria ~1s de argon2 à toa.
    if altura not in pagina_cache:
        ctx = _contexto(browser, largura=shot.get("largura", 1440), altura=altura, escala=1.5)
        pagina = ctx.new_page()
        _logar(pagina)
        pagina_cache[altura] = pagina
    pagina = pagina_cache[altura]

    pagina.goto(f"{APP}/dashboard")
    # A tabela sempre primeiro: é ela que o HTMX carrega no load, e é dela
    # que sai o clique. O `espera` da captura vem DEPOIS da interação --
    # `#lead-detail h3` só existe uma vez que o lead foi aberto.
    pagina.wait_for_selector("#leads-table tbody tr", timeout=20_000)

    if shot.get("abrir_primeiro_lead"):
        pagina.locator("#leads-table tbody tr").first.click()

    _esperar_htmx(pagina, shot.get("espera"))
    return _tirar(pagina, shot)


def _chat(browser, shot: dict, sids: dict) -> Path:
    ctx = _contexto(browser, largura=shot.get("largura", 430), altura=shot.get("altura", 1300), escala=2)
    # O chat web liga o navegador ao lead pelo cookie `web_session`.
    ctx.add_cookies([{
        "name": "web_session", "value": sids[shot["conversa"]],
        "url": APP,
    }])
    pagina = ctx.new_page()
    pagina.goto(f"{APP}/chat")
    pagina.wait_for_timeout(1500)
    # A galeria carrega depois do scroll do próprio chat: rola de novo.
    pagina.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    pagina.wait_for_timeout(1200)
    return _tirar(pagina, shot)


# Página mínima de uma "imobiliária" de terceiro, só pra mostrar o widget
# no contexto em que ele vive. Não é entregável do projeto -- é cenário.
_SITE_FICTICIO = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>Imobiliária Exemplo</title><style>
body{{font-family:system-ui;margin:0;color:#1e293b}}
header{{background:#0f172a;color:#fff;padding:14px 40px;font-weight:600}}
main{{max-width:760px;margin:48px auto;padding:0 24px}}
h1{{font-size:30px;margin:0 0 8px}} p{{color:#475569;line-height:1.6}}
.card{{border:1px solid #e2e8f0;border-radius:10px;padding:18px;margin-top:22px}}
</style></head><body>
<header>Imobiliária Exemplo</header>
<main><h1>Apartamento na Zona Sul</h1>
<p>2 quartos · 45m² · Campo Belo</p>
<div class="card"><strong>R$ 311.000</strong><p>Ficou interessado? Fale com a gente pelo chat.</p></div>
</main>
<script src="{app}/widget.js" data-brand="Imobiliária Exemplo" defer></script>
</body></html>"""


@contextmanager
def _site_de_terceiro():
    """Serve a página fictícia por HTTP, numa porta própria.

    `page.set_content()` não serve: deixa a página em `about:blank`, e o
    navegador recusa carregar um iframe de outra origem a partir daí -- a
    captura saía com o ícone de documento quebrado no lugar do chat. Servir
    de verdade também torna a cena honesta: é mesmo outra origem, que é o
    cenário em que o widget vive."""
    html = _SITE_FICTICIO.format(app=APP).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_):  # silencia o log por requisição
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}/"
    finally:
        servidor.shutdown()


def _widget(browser, shot: dict, sids: dict) -> Path:
    ctx = _contexto(browser, largura=shot.get("largura", 1100), altura=shot.get("altura", 760), escala=1.5)
    pagina = ctx.new_page()
    # O widget guarda a sessão no localStorage do site que hospeda (não em
    # cookie: dentro do iframe seria third-party). Ver app/web/static/widget.js.
    pagina.add_init_script(
        f"localStorage.setItem('bia_sid_{APP}', {json.dumps(sids['widget'])})"
    )
    with _site_de_terceiro() as url:
        pagina.goto(url)
        pagina.wait_for_timeout(1200)
        pagina.evaluate("window.Bia && window.Bia.abrir()")
        # O iframe é outra origem e carrega a conversa inteira: espera o
        # conteúdo, não só o elemento.
        pagina.wait_for_timeout(3500)
        return _tirar(pagina, shot)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arquivos", nargs="*", help="só estas imagens (padrão: todas)")
    parser.add_argument("--refazer", action="store_true",
                        help="apaga as conversas de demonstração e as recria do zero")
    parser.add_argument("--demo", action="store_true",
                        help="semeia com o provedor de regras (sem GPU, determinístico). "
                             "O painel de observabilidade sai sem números — veja conversas.py")
    args = parser.parse_args()

    if args.demo:
        os.environ["CAPTURA_DEMO"] = "1"

    if not _app_no_ar():
        print(f"A aplicação não respondeu em {APP}/health.", file=sys.stderr)
        print("Suba com ./run.sh (ou uvicorn app.web.api:app) e rode de novo.", file=sys.stderr)
        return 1

    from scripts.screenshots import conversas

    if args.refazer:
        print(f"conversas apagadas: {conversas.apagar_todas()}")
    conversas.garantir_admin(EMAIL_DEMO, SENHA_DEMO)
    print("provedor:", "regras (--demo)" if conversas.USANDO_DEMO else "Ollama real")
    criados = conversas.garantir_tudo()
    sids = {nome: conversas.sid_de(chat_id) for nome, chat_id in criados.items()}
    print(f"estado semeado: {', '.join(criados)}")

    shots = json.loads(CONFIG.read_text())
    if args.arquivos:
        shots = [s for s in shots if s["file"] in args.arquivos]
        if not shots:
            print(f"nenhuma captura chamada {args.arquivos} em shots.json", file=sys.stderr)
            return 1

    SAIDA.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        cache: dict = {}
        for shot in shots:
            tipo = shot["tipo"]
            if tipo == "painel":
                destino = _painel(browser, shot, pagina_cache=cache)
            elif tipo == "chat":
                destino = _chat(browser, shot, sids)
            elif tipo == "widget":
                destino = _widget(browser, shot, sids)
            else:
                print(f"tipo desconhecido em {shot['file']}: {tipo}", file=sys.stderr)
                continue
            kb = destino.stat().st_size // 1024
            print(f"  {destino.relative_to(RAIZ)}  ({kb} KB)")
        browser.close()

    print("\nOlhe as imagens antes do commit: conversa que mudou de tamanho pode")
    print("cortar a parte que interessa (ajuste `altura` ou `rolar_ate` em shots.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
