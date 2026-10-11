/*
 * Widget da Bia -- uma linha no site de qualquer imobiliária:
 *
 *   <script src="https://SEU-APP/widget.js" data-brand="Imobiliária X" defer></script>
 *
 * Decisões que importam:
 *
 * - SHADOW DOM. O CSS do site do cliente não vaza pra dentro do widget e o
 *   nosso não vaza pra fora. Sem isso, um `button { }` genérico no tema do
 *   WordPress deformaria a bolha.
 *
 * - IFRAME CARREGADO SÓ NO PRIMEIRO CLIQUE. O script sozinho é alguns KB;
 *   quem nunca abre o chat não paga o custo da página inteira.
 *
 * - SESSÃO NO localStorage DO SITE, NÃO EM COOKIE. O chat roda num iframe
 *   de outro domínio, então o cookie dele seria third-party: Safari e
 *   Firefox bloqueiam por padrão, e o Chrome exige SameSite=None+Secure
 *   (que não funciona em http://localhost). O id da conversa vive no
 *   localStorage do site que hospeda e viaja na URL do iframe -- o chat
 *   continua de onde parou mesmo o visitante mudando de página, sem
 *   depender de cookie de terceiro.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  // De onde o script veio é de onde o chat vem -- não dá pra assumir mesma
  // origem do site que hospeda.
  var origem = new URL(script.src, location.href).origin;
  var marca = script.getAttribute("data-brand") || "";
  var lado = script.getAttribute("data-position") === "left" ? "left" : "right";

  // Chave por origem: dois widgets de apps diferentes no mesmo site não
  // podem compartilhar a mesma conversa.
  var CHAVE = "bia_sid_" + origem;

  function sessionId() {
    try {
      var existente = localStorage.getItem(CHAVE);
      if (existente) return existente;
      var novo =
        crypto.randomUUID
          ? crypto.randomUUID().replace(/-/g, "")
          : String(Date.now()) + Math.random().toString(16).slice(2);
      localStorage.setItem(CHAVE, novo);
      return novo;
    } catch (e) {
      // Navegação privada / storage bloqueado: a conversa ainda funciona,
      // só não sobrevive a um reload. Melhor que não abrir o chat.
      return String(Date.now()) + Math.random().toString(16).slice(2);
    }
  }

  var host = document.createElement("div");
  host.setAttribute("data-bia-widget", "");
  var raiz = host.attachShadow({ mode: "open" });

  raiz.innerHTML = [
    "<style>",
    "  :host { all: initial; }",
    "  .bolha {",
    "    position: fixed; bottom: 20px; " + lado + ": 20px; z-index: 2147483000;",
    "    width: 56px; height: 56px; border-radius: 50%; border: 0; cursor: pointer;",
    "    background: #1e293b; color: #fff; font-size: 24px; line-height: 56px;",
    "    box-shadow: 0 4px 16px rgba(0,0,0,.28);",
    "    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    "  }",
    "  .bolha:hover { background: #334155; }",
    "  .painel {",
    "    position: fixed; bottom: 88px; " + lado + ": 20px; z-index: 2147483000;",
    "    width: 380px; height: 560px; max-height: calc(100vh - 110px);",
    "    background: #fff; border-radius: 12px; overflow: hidden; display: none;",
    "    box-shadow: 0 8px 40px rgba(0,0,0,.24);",
    "  }",
    "  .painel.aberto { display: block; }",
    "  iframe { width: 100%; height: 100%; border: 0; display: block; }",
    // Celular: ocupa a tela toda. Painel de 380px num aparelho de 360px
    // sairia da viewport.
    "  @media (max-width: 480px) {",
    "    .painel { bottom: 0; left: 0; right: 0; top: 0;",
    "              width: 100%; height: 100%; max-height: 100%; border-radius: 0; }",
    "  }",
    "</style>",
    '<button class="bolha" part="bolha" aria-label="Abrir chat" title="Fale com a gente">💬</button>',
    '<div class="painel" role="dialog" aria-label="Chat"></div>',
  ].join("\n");

  var bolha = raiz.querySelector(".bolha");
  var painel = raiz.querySelector(".painel");
  var iframe = null;

  function garantirIframe() {
    if (iframe) return;
    iframe = document.createElement("iframe");
    var url = new URL(origem + "/embed");
    url.searchParams.set("sid", sessionId());
    if (marca) url.searchParams.set("brand", marca);
    iframe.src = url.toString();
    iframe.setAttribute("title", "Chat");
    painel.appendChild(iframe);
  }

  function abrir() {
    garantirIframe();
    painel.classList.add("aberto");
    bolha.textContent = "✕";
    bolha.setAttribute("aria-label", "Fechar chat");
  }

  function fechar() {
    painel.classList.remove("aberto");
    bolha.textContent = "💬";
    bolha.setAttribute("aria-label", "Abrir chat");
  }

  bolha.addEventListener("click", function () {
    painel.classList.contains("aberto") ? fechar() : abrir();
  });

  // O botão de fechar mora DENTRO do iframe (outra origem), então ele avisa
  // por postMessage. Checar a origem é obrigatório: sem isso, qualquer
  // página conseguiria mandar mensagem pro widget.
  window.addEventListener("message", function (e) {
    if (e.origin !== origem) return;
    if (e.data === "bia:fechar") fechar();
  });

  document.body.appendChild(host);

  // API mínima pro site: botão "Fale com a imobiliária" abre o chat.
  window.Bia = { abrir: abrir, fechar: fechar };
})();
