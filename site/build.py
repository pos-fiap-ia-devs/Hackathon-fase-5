"""Gera o site da Horizonte Imóveis a partir dos imóveis do banco.

Site estático: HTML puro, uma folha de estilo, nenhum build de JS. O único
script da página é o `widget.js` do próprio projeto -- é esse o ponto da
coisa: mostrar a Bia atendendo dentro do site de uma imobiliária, que é
onde ela viveria de verdade.

    python site/build.py                      aponta pro app em localhost:8000
    python site/build.py --app https://...    pra publicar em outro lugar

Sai em `site/dist/`. Pra servir:

    python -m http.server 5500 --directory site/dist

O preço, o endereço, a metragem e o código vêm do Postgres, nunca de
template -- mesmo guardrail do card do agente (secao 5.6): dado de imóvel
não se inventa, nem no chat nem na vitrine.
"""

import argparse
import html
import re
import shutil
from functools import lru_cache
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.agents.router import TIPOS_SEM_QUARTO
from app.db.repo import get_conn  # noqa: E402

RAIZ = Path(__file__).resolve().parent
DIST = RAIZ / "dist"

MARCA = "Horizonte Imóveis"
DESTAQUES = 9

TIPOS = {
    "apartamento": "Apartamento",
    "casa": "Casa",
    "studio": "Studio",
    "cobertura": "Cobertura",
    "sala": "Sala comercial",
    "terreno": "Terreno",
    "galpao": "Galpão",
    "loja": "Loja",
    "predio": "Prédio",
    "chacara": "Chácara",
    "resort": "Casa em resort",
}


# ---------------------------------------------------------------- dados


def carregar() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, codigo, titulo, tipo, finalidade, bairro, zona, cidade,
                   preco, quartos, banheiros, vagas, area, condominio,
                   rentabilidade_estimada, descricao, fotos,
                   iptu, suites, comodidades, campos_estimados, rentabilidade_base
            FROM imoveis
            ORDER BY finalidade, preco
            """
        )
        return cur.fetchall()


# Preenchida em main(): só há página/aba de "Investir" se existir imóvel
# com rentabilidade cadastrada. O catálogo real não publica rentabilidade,
# então o menu linkava pra uma página que nunca era gerada (404).
HA_INVESTIMENTO = False


def link_investir(raiz: str, *, item_de_lista: bool = False) -> str:
    if not HA_INVESTIMENTO:
        return ""
    tag = f'<a href="{raiz}investir.html">Investir</a>'
    return f"<li>{tag}</li>" if item_de_lista else tag


def metodo_rentabilidade(im: dict) -> str:
    """De onde veio o número, em português. O catálogo não publica
    rentabilidade: ela é calculada, e quem lê precisa saber de quê."""
    base = im.get("rentabilidade_base") or ""
    if base.startswith("anunciado"):
        return "calculada com o aluguel e o preço de venda anunciados para este mesmo imóvel"
    if base.startswith("comparaveis"):
        return "aluguel estimado pela mediana de imóveis do mesmo tipo alugados na cidade"
    return "estimativa"


def metros(area: int) -> str:
    """2000 -> "2.000 m²". Terreno do catálogo real chega a 1.024.520 m²,
    e sem separador o número não se lê."""
    return f"{int(area):,} m²".replace(",", ".")


# Singular de cada rótulo da ficha, pro card: "1 suítes" é erro de texto.
_SINGULAR = {"Quartos": "quarto", "Suítes": "suíte", "Banheiros": "banheiro", "Vagas": "vaga"}


def spec_card(rotulo: str, valor: str) -> str:
    """Um item da ficha no formato do card ("<b>1</b> suíte")."""
    if rotulo == "Área":
        numero, _, unidade = valor.partition(" ")
        return f"<span><b>{e(numero)}</b> {e(unidade)}</span>"
    nome = _SINGULAR[rotulo] if valor == "1" else rotulo.lower()
    return f"<span><b>{e(valor)}</b> {e(nome)}</span>"


def titulo_vitrine(im: dict) -> str:
    """Título legível pra vitrine, montado dos campos.

    O `imoveis.titulo` do banco é texto de sistema ("Studio 1 quarto(s) -
    Centro"): o "(s)" serve pro agente não errar plural, mas num site
    parece formulário. Aqui vira "Studio em Centro" / "Apartamento de 2
    quartos em Moema", que é como anúncio de imóvel se escreve."""
    tipo = TIPOS.get(im["tipo"], im["tipo"])
    q = im["quartos"]
    # Terreno, galpão, loja, sala e prédio não têm quarto: "Terreno de 0
    # quartos" é dado errado, não neutro. Pra eles o que identifica é a
    # metragem. Mesma regra do agente (agents/router.py::TIPOS_SEM_QUARTO).
    if im["tipo"] in TIPOS_SEM_QUARTO:
        if im["area"]:
            return f"{tipo} de {metros(im['area'])} em {im['bairro']}"
        return f"{tipo} em {im['bairro']}"
    if im["tipo"] == "studio" or q <= 1:
        return f"{tipo} em {im['bairro']}"
    return f"{tipo} de {q} quartos em {im['bairro']}"


def ficha(im: dict) -> list[tuple[str, str]]:
    """Especificações (rótulo, valor) só do que existe e é confiável.

    Mesmos dois cortes do agente (core/turno.py::_specs_do_imovel): zero de
    quarto/banheiro não aparece, e campo estimado não aparece. A imobiliária
    deixa `banheiros` em branco na maior parte dos anúncios e o importador o
    estima; numa página pública isso seria afirmar um número que ninguém
    cadastrou."""
    estimados = set(im.get("campos_estimados") or [])
    itens = []
    if im["quartos"]:
        itens.append(("Quartos", str(im["quartos"])))
    if im.get("suites"):
        itens.append(("Suítes", str(im["suites"])))
    if im["banheiros"] and "banheiros" not in estimados:
        itens.append(("Banheiros", str(im["banheiros"])))
    if im["vagas"]:
        itens.append(("Vagas", str(im["vagas"])))
    if im["area"]:
        itens.append(("Área", metros(im["area"])))
    return itens


STATIC = RAIZ.parent / "app" / "web"

# Tipos que vão pra vitrine da página inicial. Terreno, galpão e sala são
# metade do catálogo, mas foto de lote vazio não vende a imobiliária -- e
# era o que aparecia primeiro, porque a lista vinha ordenada por preço
# crescente e os mais baratos são terrenos.
TIPOS_VITRINE = ("casa", "apartamento", "cobertura", "studio")


@lru_cache(maxsize=None)
def _medidas(caminho_web: str) -> tuple[int, int, int]:
    """(largura, altura, bytes) da foto. `caminho_web` é /static/..."""
    arquivo = STATIC / caminho_web.lstrip("/")
    try:
        with Image.open(arquivo) as im:
            return im.width, im.height, arquivo.stat().st_size
    except (OSError, ValueError):
        return 0, 0, 0


def _horizontal(caminho_web: str) -> bool:
    largura, altura, _ = _medidas(caminho_web)
    return bool(largura) and largura >= altura


def foto_do_card(im: dict, posicao: int = 0) -> str | None:
    """A capa do imóvel: a PRIMEIRA foto horizontal do anúncio.

    A ordem das fotos é escolha do corretor -- a primeira é a que ele quis
    na frente. Respeita-se isso, só pulando foto vertical, que recortada
    pro card 4:3 perde metade da imagem.

    Tentou-se antes escolher pela "quantidade de detalhe" (tamanho do
    arquivo): olhando o resultado, a regra premiava folhagem e piso de
    lajota -- uma vitrine com estacionamento vazio e rua deserta na frente
    de salas mobiliadas. Foto bonita não se mede em bytes.

    `posicao` fica na assinatura pra não quebrar quem chama."""
    fotos = im["fotos"] or []
    if not fotos:
        return None
    return next((f for f in fotos if _horizontal(f)), fotos[0])


# Na vitrine, quantos apartamentos no máximo entram na frente das casas.
# O catálogo real é quase todo casa; sem cota, a home virava nove casas.
APARTAMENTOS_NA_VITRINE = 2


def _base(codigo: str) -> str:
    """`CA1512-HORZ-L` e `CA1512-HORZ` são o mesmo imóvel (aluguel e venda)."""
    return codigo.removesuffix("-L")


def vitrine(imoveis: list[dict], quantos: int = 0, *, evitar: set[str] = frozenset()) -> list[dict]:
    """Os imóveis que abrem a página inicial.

    Só residencial, com a melhor capa possível, até
    `APARTAMENTOS_NA_VITRINE` apartamentos na frente, e no máximo um por
    bairro na primeira passada -- senão a vitrine vira seis casas do mesmo
    condomínio. `evitar` recebe os códigos já mostrados em outra seção: o
    imóvel anunciado pra venda e aluguel não aparece duas vezes na home."""
    quantos = quantos or DESTAQUES
    candidatos = [
        im for im in imoveis
        if im["tipo"] in TIPOS_VITRINE and im["fotos"] and _base(im["codigo"]) not in evitar
    ]
    # Preço mais alto primeiro: imóvel de padrão mais alto costuma ter foto
    # profissional, e é o que a imobiliária quer na frente. Capa horizontal
    # antes de vertical, e mais fotos antes de menos, como desempate.
    candidatos.sort(
        key=lambda im: (_horizontal(foto_do_card(im)), float(im["preco"]), len(im["fotos"])),
        reverse=True,
    )

    escolhidos: list[dict] = []
    bairros: set[str] = set()

    def pegar(im: dict) -> None:
        escolhidos.append(im)
        bairros.add(im["bairro"])

    for im in [c for c in candidatos if c["tipo"] == "apartamento"][:APARTAMENTOS_NA_VITRINE]:
        pegar(im)
    for im in candidatos:
        if len(escolhidos) == quantos:
            return escolhidos
        if im not in escolhidos and im["bairro"] not in bairros:
            pegar(im)
    for im in candidatos:
        if len(escolhidos) == quantos:
            break
        if im not in escolhidos:
            pegar(im)
    return escolhidos


def descricao_vitrine(texto: str) -> str:
    """Tira o "(s)" da descrição do banco.

    Mesmo motivo do título: "1 quarto(s)" existe pro agente não errar
    plural num texto gerado, mas num anúncio parece sistema. Só exibição --
    a descrição no banco continua intacta, e é dela que o RAG indexa."""
    return re.sub(r"(\d+) (quarto|banheiro|vaga|suite)\(s\)",
                  lambda m: f"{m.group(1)} {m.group(2)}" + ("s" if m.group(1) != "1" else ""),
                  texto or "")


def preco_br(valor, finalidade: str) -> str:
    texto = f"R$ {int(valor):,}".replace(",", ".")
    return f"{texto}<span>/mês</span>" if finalidade == "aluguel" else texto


def e(texto) -> str:
    """Escapa pra HTML. Todo dado do banco passa por aqui -- descrição de
    imóvel é texto livre e não pode virar marcação."""
    return html.escape(str(texto or ""))


# ---------------------------------------------------------------- blocos


def cabecalho(titulo: str, app: str, *, raiz: str = "") -> str:
    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titulo)} · {MARCA}</title>
<meta name="description" content="Imóveis para comprar e alugar em São Paulo. Atendimento 24h com a Bia, nossa assistente virtual.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{raiz}style.css">
</head>
<body>
<header class="topo">
  <div class="wrap">
    <a class="marca" href="{raiz}index.html">
      <span class="marca-simbolo">H</span>{MARCA}
    </a>
    <nav class="nav">
      <a href="{raiz}comprar.html">Comprar</a>
      <a href="{raiz}alugar.html">Alugar</a>
      {link_investir(raiz)}
      <a href="{raiz}index.html#bairros">Bairros</a>
      <a href="{raiz}index.html#sobre">Quem somos</a>
    </nav>
    <button class="botao" onclick="bia()">Falar com a Bia</button>
  </div>
</header>
"""


def rodape(app: str, *, raiz: str = "") -> str:
    return f"""<footer class="rodape">
  <div class="wrap">
    <div class="rodape-grid">
      <div>
        <div class="marca" style="margin-bottom:12px"><span class="marca-simbolo">H</span>{MARCA}</div>
        <p style="margin:0;max-width:34ch">Imóveis para comprar e alugar em São Paulo, Grande SP e interior.
        Atendimento 24 horas com a Bia, nossa assistente virtual.</p>
      </div>
      <div>
        <h4>Imóveis</h4>
        <ul>
          <li><a href="{raiz}comprar.html">Comprar</a></li>
          <li><a href="{raiz}alugar.html">Alugar</a></li>
          {link_investir(raiz, item_de_lista=True)}
        </ul>
      </div>
      <div>
        <h4>Atendimento</h4>
        <ul>
          <li><a href="#" onclick="bia();return false">Chat com a Bia</a></li>
          <li>Seg a sex, 9h às 18h</li>
          <li>Assistente virtual: 24h</li>
        </ul>
      </div>
      <div>
        <h4>Institucional</h4>
        <ul>
          <li><a href="{raiz}index.html#sobre">Quem somos</a></li>
          <li>CRECI-SP 000000-J</li>
        </ul>
      </div>
    </div>
    <div class="rodape-fim">
      Projeto acadêmico do Tech Challenge Fase 5 — FIAP Pós Tech IA para Devs. Catálogo, textos e fotos
      cedidos por uma imobiliária parceira para fins de demonstração. Atendimento virtual pelo Agente SDR do projeto.
    </div>
  </div>
</footer>
<script>
// Todo botao do site chama `bia(...)`, nunca a API do widget direto.
//
// Motivo: chamar `Bia` com a variavel INDEFINIDA lanca ReferenceError, e
// erro dentro de onclick inline morre em silencio -- o visitante clica e
// nao acontece nada, sem nenhuma pista. Foi exatamente o que aconteceu
// com a aplicacao fora do ar: o widget.js nao carrega, `Bia` nunca
// existe, e o site parecia quebrado sem dizer por que.
function bia(mensagem) {{
  if (window.Bia) {{
    window.Bia.abrir(mensagem ? {{ message: mensagem }} : undefined);
    return;
  }}
  var aviso = document.getElementById("bia-indisponivel");
  if (aviso) {{
    aviso.hidden = false;
    clearTimeout(aviso._t);
    aviso._t = setTimeout(function () {{ aviso.hidden = true; }}, 7000);
  }}
}}
</script>
<div id="bia-indisponivel" hidden class="aviso">
  <strong>O chat está indisponível.</strong>
  A aplicação precisa estar no ar em <code>{app}</code> — suba com <code>./run.sh</code>.
</div>
<script src="{app}/widget.js" data-brand="{MARCA}" defer></script>
</body>
</html>
"""


def card(im: dict, *, raiz: str = "", posicao: int = 0) -> str:
    foto = foto_do_card(im, posicao)
    titulo = titulo_vitrine(im)
    selo = (
        '<span class="selo selo-marca">Investimento</span>'
        if im["rentabilidade_estimada"]
        else f'<span class="selo">{"Aluguel" if im["finalidade"] == "aluguel" else "Venda"}</span>'
    )
    imagem = (
        f'<img src="{e(foto)}" alt="Foto do {e(titulo)}" loading="lazy" width="800" height="600">'
        if foto else ""
    )
    # Rentabilidade logo abaixo do preco: pro investidor e o numero que
    # decide, nao a metragem. Sai do banco (`rentabilidade_estimada`),
    # nunca de conta feita aqui.
    rendimento = (
        f'<p class="rendimento"><b>{im["rentabilidade_estimada"]}% a.a.</b> de rentabilidade bruta estimada</p>'
        if im["rentabilidade_estimada"] else ""
    )
    return f"""<article class="card">
  <a class="card-foto" href="{raiz}imovel/{e(im["codigo"])}.html">
    {imagem}{selo}
  </a>
  <div class="card-corpo">
    <span class="card-tipo">{e(TIPOS.get(im["tipo"], im["tipo"]))}</span>
    <h3><a href="{raiz}imovel/{e(im["codigo"])}.html">{e(titulo)}</a></h3>
    <p class="card-local">{e(im["bairro"])}, {e(im["cidade"])}</p>
    <p class="card-preco">{preco_br(im["preco"], im["finalidade"])}</p>
    {rendimento}
    <div class="specs">
      {"".join(spec_card(r, v) for r, v in ficha(im))}
    </div>
    <div class="card-rodape">
      <span class="codigo">Cód. {e(im["codigo"])}</span>
      <button class="botao botao-vazado"
        onclick="bia('Quero ver o {e(im["codigo"])}')">Tenho interesse</button>
    </div>
  </div>
</article>"""


def grade(imoveis: list[dict], *, raiz: str = "") -> str:
    if not imoveis:
        return '<div class="vazio"><strong>Nenhum imóvel com esses filtros</strong>Ajuste a busca ou fale com a Bia.</div>'
    return (
        '<div class="grade">'
        + "".join(card(im, raiz=raiz, posicao=i) for i, im in enumerate(imoveis))
        + "</div>"
    )


# ---------------------------------------------------------------- páginas


def pagina_inicial(imoveis: list[dict], app: str) -> str:
    venda = [im for im in imoveis if im["finalidade"] == "venda"]
    aluguel = [im for im in imoveis if im["finalidade"] == "aluguel"]
    destaque_venda = vitrine(venda)
    destaque_aluguel = vitrine(aluguel, evitar={_base(im["codigo"]) for im in destaque_venda})
    bairros = sorted({im["bairro"] for im in imoveis})
    cidades = sorted({im["cidade"] for im in imoveis})

    opcoes_bairro = "".join(f'<option value="{e(b)}">{e(b)}</option>' for b in bairros)
    # Os 12 bairros com mais imóveis -- lista de 50 vira parede de link.
    por_bairro = sorted(
        ({"nome": b, "n": sum(1 for im in imoveis if im["bairro"] == b)} for b in bairros),
        key=lambda x: -x["n"],
    )[:12]
    links_bairro = "".join(
        f'<li><a href="comprar.html#{e(b["nome"])}">{e(b["nome"])} '
        f'<span style="color:var(--tinta-fraca)">· {b["n"]}</span></a></li>'
        for b in por_bairro
    )

    return f"""{cabecalho("Imóveis em São Paulo", app)}
<section class="hero">
  <div class="wrap">
    <h1>O seu próximo endereço <em>começa numa conversa</em>.</h1>
    <p>{len({_base(im['codigo']) for im in imoveis})} imóveis em {len(cidades)} cidades de São Paulo. Fale com a Bia a
    qualquer hora — ela entende o que você procura e agenda a visita com o corretor.</p>

    <form class="busca" onsubmit="return filtrar(event)">
      <div class="busca-abas" role="tablist">
        <button type="button" class="aba" role="tab" aria-selected="true" data-fin="venda">Comprar</button>
        <button type="button" class="aba" role="tab" aria-selected="false" data-fin="aluguel">Alugar</button>
        {'<button type="button" class="aba" role="tab" aria-selected="false" data-fin="investir">Investir</button>' if HA_INVESTIMENTO else ''}
      </div>
      <div class="busca-campos">
        <div class="campo">
          <label for="f-bairro">Onde</label>
          <select id="f-bairro"><option value="">Qualquer bairro</option>{opcoes_bairro}</select>
        </div>
        <div class="campo">
          <label for="f-quartos">Quartos</label>
          <select id="f-quartos">
            <option value="">Qualquer</option>
            <option value="1">1+</option><option value="2">2+</option>
            <option value="3">3+</option><option value="4">4+</option>
          </select>
        </div>
        <div class="campo">
          <label for="f-codigo">Código do imóvel</label>
          <input id="f-codigo" placeholder="{e(destaque_venda[0]["codigo"]) if destaque_venda else ""}" autocomplete="off">
        </div>
        <button class="botao" type="submit">Buscar</button>
      </div>
    </form>
  </div>
</section>

<section class="secao">
  <div class="wrap">
    <div class="secao-topo">
      <div>
        <h2>Destaques para comprar</h2>
        <p>{len(venda)} imóveis à venda, de {preco_br(min(im["preco"] for im in venda), "venda")}
        a {preco_br(max(im["preco"] for im in venda), "venda")}.</p>
      </div>
      <a href="comprar.html">Ver todos →</a>
    </div>
    {grade(destaque_venda)}
  </div>
</section>

<section class="secao secao-suave">
  <div class="wrap">
    <div class="secao-topo">
      <div>
        <h2>Para alugar</h2>
        <p>{len(aluguel)} imóveis disponíveis para locação.</p>
      </div>
      <a href="alugar.html">Ver todos →</a>
    </div>
    {grade(destaque_aluguel)}
  </div>
</section>

<section class="secao" id="bairros">
  <div class="wrap">
    <div class="secao-topo"><div><h2>Bairros mais procurados</h2></div></div>
    <ul class="rodape-grid" style="list-style:none;padding:0;margin:0;
        grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px">
      {links_bairro}
    </ul>
  </div>
</section>

<section class="secao secao-suave" id="sobre">
  <div class="wrap">
    <div class="secao-topo">
      <div>
        <h2>Atendimento que não dorme</h2>
        <p style="max-width:58ch">A Bia é a nossa assistente virtual. Ela entende se você quer comprar,
        alugar ou investir, mostra imóveis reais do nosso portfólio e agenda a visita com o corretor
        certo — às 3 da manhã de um domingo, se for quando você tiver tempo.</p>
      </div>
      <button class="botao" onclick="bia()">Conversar agora</button>
    </div>
  </div>
</section>

<script>
function filtrar(ev) {{
  ev.preventDefault();
  const fin = document.querySelector('.aba[aria-selected="true"]').dataset.fin;
  const codigo = document.getElementById('f-codigo').value.trim().toUpperCase();
  if (codigo) {{ location.href = 'imovel/' + codigo + '.html'; return false; }}
  const p = new URLSearchParams();
  const bairro = document.getElementById('f-bairro').value;
  const quartos = document.getElementById('f-quartos').value;
  if (bairro) p.set('bairro', bairro);
  if (quartos) p.set('quartos', quartos);
  const pagina = fin === 'aluguel' ? 'alugar' : (fin === 'investir' ? 'investir' : 'comprar');
  location.href = pagina + '.html?' + p;
  return false;
}}
document.querySelectorAll('.aba').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.aba').forEach(o => o.setAttribute('aria-selected', o === b));
}}));
</script>
{rodape(app)}"""


def pagina_lista(imoveis: list[dict], finalidade: str, app: str) -> str:
    titulo = "Imóveis à venda" if finalidade == "venda" else "Imóveis para alugar"
    bairros = sorted({im["bairro"] for im in imoveis})
    chips = "".join(
        f'<button class="chip" data-bairro="{e(b)}">{e(b)}</button>' for b in bairros
    )
    return f"""{cabecalho(titulo, app)}
<section class="secao">
  <div class="wrap">
    <div class="secao-topo">
      <div>
        <h2>{titulo}</h2>
        <p id="contador">{len(imoveis)} imóveis</p>
      </div>
      <button class="botao botao-vazado" onclick="bia()">Não achou? Fale com a Bia</button>
    </div>
    <div class="chips">
      <button class="chip" data-bairro="" aria-pressed="true">Todos</button>{chips}
    </div>
    {grade(imoveis)}
  </div>
</section>

<script>
// Filtro no cliente: a lista inteira ja esta no HTML (sao 200 imoveis, nao
// vale a pena paginar nem chamar servidor). O `?bairro=` da home entra por
// aqui tambem.
const cards = [...document.querySelectorAll('.card')];
const contador = document.getElementById('contador');

function aplicar(bairro, quartos) {{
  let n = 0;
  cards.forEach(c => {{
    const local = c.querySelector('.card-local').textContent;
    const q = parseInt(c.querySelector('.specs b').textContent, 10);
    const ok = (!bairro || local.startsWith(bairro)) && (!quartos || q >= quartos);
    c.style.display = ok ? '' : 'none';
    if (ok) n++;
  }});
  contador.textContent = n + (n === 1 ? ' imóvel' : ' imóveis');
}}

document.querySelectorAll('.chip').forEach(chip => chip.addEventListener('click', () => {{
  document.querySelectorAll('.chip').forEach(o => o.setAttribute('aria-pressed', o === chip));
  aplicar(chip.dataset.bairro, 0);
}}));

const url = new URLSearchParams(location.search);
if (url.get('bairro') || url.get('quartos')) {{
  aplicar(url.get('bairro') || '', parseInt(url.get('quartos') || '0', 10));
}}
</script>
{rodape(app)}"""


def pagina_investir(imoveis: list[dict], app: str) -> str:
    """Terceiro fluxo do enunciado. O site tinha so Comprar e Alugar, e o
    agente ja atendia investidor desde sempre -- a vitrine nao refletia o
    produto.

    Ordenado por rentabilidade, nao por preco: quem investe compara
    retorno. Os 25 imoveis sao os que o seed marcou com
    `rentabilidade_estimada` (app/db/seed.py)."""
    por_retorno = sorted(imoveis, key=lambda im: -float(im["rentabilidade_estimada"]))
    melhor = por_retorno[0]["rentabilidade_estimada"]
    pior = por_retorno[-1]["rentabilidade_estimada"]
    ticket_min = min(im["preco"] for im in imoveis)

    return f"""{cabecalho("Imóveis para investir", app)}
<section class="hero" style="padding-bottom:0">
  <div class="wrap">
    <h1>Renda com <em>imóvel que já aluga</em>.</h1>
    <p>{len(imoveis)} imóveis selecionados por rentabilidade, de {pior}% a {melhor}% ao ano.
    Entrada a partir de {preco_br(ticket_min, "venda")}. A Bia te conecta a um especialista
    em investimento imobiliário.</p>
    <div style="margin:28px 0 0">
      <button class="botao" onclick="bia('Quero investir em imóveis para renda')">
        Falar com um especialista
      </button>
    </div>
  </div>
</section>

<section class="secao">
  <div class="wrap">
    <div class="secao-topo">
      <div>
        <h2>Maiores rentabilidades</h2>
        <p>Ordenado pelo retorno estimado ao ano.</p>
      </div>
    </div>
    {grade(por_retorno)}
  </div>
</section>
{rodape(app)}"""


def pagina_imovel(im: dict, app: str) -> str:
    titulo = titulo_vitrine(im)
    fotos = im["fotos"] or []
    principal = (
        f'<img src="{e(fotos[0])}" alt="Foto principal do {e(titulo)}" width="800" height="600">'
        if fotos else ""
    )
    secundarias = "".join(
        f'<img src="{e(f)}" alt="Foto {i} do {e(titulo)}" loading="lazy" width="800" height="600">'
        for i, f in enumerate(fotos[1:3], start=2)
    )
    iptu = (
        f'<div><dt>IPTU</dt><dd>R$ {int(im["iptu"]):,}/mês</dd></div>'.replace(",", ".")
        if im.get("iptu") else ""
    )
    condominio = (
        f'<div><dt>Condomínio</dt><dd>R$ {int(im["condominio"]):,}</dd></div>'.replace(",", ".")
        if im["condominio"] else ""
    )
    rentabilidade = (
        f'<p class="nota">Rentabilidade bruta estimada de {im["rentabilidade_estimada"]}% ao ano — '
        f'{e(metodo_rentabilidade(im))}. Não inclui condomínio, IPTU, vacância nem impostos.</p>'
        if im["rentabilidade_estimada"] else ""
    )

    return f"""{cabecalho(titulo, app, raiz="../")}
<div class="wrap detalhe-topo">
  <a class="volta" href="../{"alugar" if im["finalidade"] == "aluguel" else "comprar"}.html">← Voltar para a lista</a>
  <div class="detalhe-galeria">
    {principal}
    <div class="secundarias">{secundarias}</div>
  </div>
</div>

<div class="wrap detalhe-grid detalhe">
  <div>
    <span class="card-tipo">{e(TIPOS.get(im["tipo"], im["tipo"]))} · {e(im["zona"])}</span>
    <h1>{e(titulo)}</h1>
    <p class="card-local" style="font-size:16px">{e(im["bairro"])}, {e(im["cidade"])}</p>

    <dl class="ficha">
      {"".join(f"<div><dt>{e(r)}</dt><dd>{e(v)}</dd></div>" for r, v in ficha(im))}
      {condominio}
      {iptu}
    </dl>

    <h2 style="font-size:19px;margin:28px 0 8px">Sobre o imóvel</h2>
    <p style="color:var(--tinta-media);margin:0;max-width:62ch">{e(descricao_vitrine(im["descricao"]))}</p>
  </div>

  <aside class="painel">
    <p class="preco">{preco_br(im["preco"], im["finalidade"])}</p>
    <p class="codigo" style="margin:6px 0 0">Cód. {e(im["codigo"])}</p>
    <button class="botao"
      onclick="bia('Quero agendar uma visita ao {e(im["codigo"])}')">
      Agendar visita
    </button>
    <button class="botao botao-vazado" style="width:100%;justify-content:center;margin-top:8px"
      onclick="bia('Tenho dúvidas sobre o {e(im["codigo"])}')">
      Tirar dúvidas
    </button>
    <p class="nota">A Bia responde na hora, 24 horas por dia, e marca a visita com o corretor da região.</p>
    {rentabilidade}
  </aside>
</div>
{rodape(app, raiz="../")}"""


# ---------------------------------------------------------------- build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app", default="http://localhost:8000",
        help="de onde vem o widget.js e o chat (padrão: http://localhost:8000)",
    )
    args = parser.parse_args()
    app = args.app.rstrip("/")

    imoveis = carregar()
    global HA_INVESTIMENTO
    HA_INVESTIMENTO = any(im["rentabilidade_estimada"] for im in imoveis)
    if not imoveis:
        print("banco sem imóveis -- rode `python -m scripts.preparar_banco` antes.", file=sys.stderr)
        return 1

    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "imovel").mkdir(parents=True)

    shutil.copy(RAIZ / "style.css", DIST / "style.css")
    # Fotos copiadas, nao linkadas pro app: o site tem que abrir mesmo com a
    # aplicacao fora do ar -- so o chat depende dela.
    shutil.copytree(
        RAIZ.parent / "app" / "web" / "static" / "imoveis",
        DIST / "static" / "imoveis",
    )

    (DIST / "index.html").write_text(pagina_inicial(imoveis, app), encoding="utf-8")
    for finalidade, arquivo in (("venda", "comprar.html"), ("aluguel", "alugar.html")):
        subconjunto = [im for im in imoveis if im["finalidade"] == finalidade]
        (DIST / arquivo).write_text(pagina_lista(subconjunto, finalidade, app), encoding="utf-8")
    investimento = [im for im in imoveis if im["rentabilidade_estimada"]]
    if investimento:
        (DIST / "investir.html").write_text(pagina_investir(investimento, app), encoding="utf-8")

    for im in imoveis:
        (DIST / "imovel" / f"{im['codigo']}.html").write_text(
            pagina_imovel(im, app), encoding="utf-8"
        )

    paginas = 4 + len(imoveis)
    print(f"{paginas} páginas em {DIST}")
    print(f"widget apontando pra {app}")
    print(f"\nservir:  python -m http.server 5500 --directory {DIST.relative_to(RAIZ.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
