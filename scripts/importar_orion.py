"""Importa o catálogo real da Orion Imóveis para o dataset do projeto.

Por que existe
--------------
A base era sintética: 200 imóveis gerados por `app/db/seed.py`, em bairros
reais mas com preço, área e descrição inventados, ilustrados por fotos
genéricas de banco de imagem. Funciona pra demonstrar o fluxo, mas um
avaliador que abre um anúncio vê que nada ali existe.

Agora o catálogo vem do site da própria imobiliária (orionimoveis.com.br),
**com autorização do proprietário**. Cada imóvel traz foto, descrição,
preço, área, quartos e endereço reais, e o código do anúncio (`CA0432-ORIO`)
é o mesmo que a corretora usa internamente -- então o que a Bia fala com o
cliente bate com o que está no sistema dela.

De onde sai cada campo
----------------------
As páginas de anúncio publicam `schema.org/RealEstateListing` em JSON-LD,
que é dado estruturado feito pra ser lido por máquina -- não há raspagem de
HTML aqui, e por isso uma mudança de layout do site não quebra este script:

    name            -> titulo
    description     -> descricao
    image[]         -> fotos (baixadas, ver abaixo)
    about.numberOfRooms / floorSize / amenityFeature -> quartos, area, vagas
    about.address.addressLocality                    -> cidade
    offers.price / businessFunction                  -> preco, finalidade

O **bairro** não está no JSON-LD do imóvel; vem do `BreadcrumbList` da
mesma página (penúltimo nível). Os anúncios são descobertos pelo
`sitemap.xml`, não por navegação.

Fotos
-----
Baixadas e **reduzidas pra 800px** antes de gravar: no tamanho original
seriam ~65 MB pros 270 anúncios, o que não cabe num repositório que alguém
vai clonar pra avaliar. Ficam versionadas em `static/imoveis/orion/`, pelo
mesmo motivo de sempre -- depender de URL de terceiro em tempo de execução
já nos custou a galeria inteira uma vez (o loremflickr virou 401).

    python -m scripts.importar_orion                 # tudo
    python -m scripts.importar_orion --limite 20     # amostra, pra testar
    python -m scripts.importar_orion --sem-fotos     # só os dados
"""

import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import httpx

from scripts import curadoria

RAIZ = Path(__file__).resolve().parent.parent
FOTOS = RAIZ / "app" / "web" / "static" / "imoveis" / "orion"
DATASET = RAIZ / "app" / "db" / "catalogo_orion.json"

SITE = "https://www.orionimoveis.com.br"
SITEMAP = f"{SITE}/sitemap.xml"

# Identificação honesta. O robots.txt do site libera `Claude-User`, e o
# proprietário autorizou este uso diretamente.
UA = "HorizonteSDR-FIAP/1.0 (Claude-User; projeto academico; autorizado pelo proprietario)"

LARGURA_FOTO = 800
FOTOS_POR_IMOVEL = 3
MIN_BYTES = 12_000   # abaixo disso é ícone ou placeholder, não foto

# Como o lead fala da região. A capital tem zonas; o resto da operação da
# Orion é Grande São Paulo (eixo oeste), litoral e interior. `zona` é o que
# o agente casa contra o que o cliente pede -- ver app/core/busca.py.
ZONA_POR_CIDADE = {
    "barueri": "grande sao paulo",
    "santana de parnaiba": "grande sao paulo",
    "itapevi": "grande sao paulo",
    "jandira": "grande sao paulo",
    "cotia": "grande sao paulo",
    "pirapora do bom jesus": "grande sao paulo",
    "aracariguama": "interior",
    "aguas de santa barbara": "interior",
    "guaruja": "litoral",
    "ilhabela": "litoral",
    "sao sebastiao": "litoral",
    "ubatuba": "litoral",
    "mongagua": "litoral",
}

# Tipos do site -> tipos do nosso schema. O site tem mais categorias do que
# o agente sabe qualificar; `terreno`, `galpao`, `loja`, `predio` e
# `chacara` entram como estão, e o schema ganhou espaço pra eles.
# Na CAPITAL o lead fala de zona (sul, norte, leste, oeste, centro), não de
# cidade -- é a mesma convenção do seed sintético (app/db/seed.py). Mapear
# só por cidade jogava os 6 imóveis de São Paulo em "grande sao paulo", e
# "zona sul" nunca casava com os que existem de verdade (Planalto
# Paulista, Santo Amaro, Brooklin). Chave: bairro sem acento, minúsculo.
ZONA_CAPITAL_POR_BAIRRO = {
    "planalto paulista": "zona sul",
    "santo amaro": "zona sul",
    "brooklin": "zona sul",
    "moema": "zona sul",
    "campo belo": "zona sul",
    "vila mariana": "zona sul",
    "chacara tatuape": "zona leste",
    "tatuape": "zona leste",
    "mooca": "zona leste",
    # Pirituba/Jaraguá: a prefeitura classifica como zona norte.
    "parque sao domingos": "zona norte",
    "santana": "zona norte",
    "pinheiros": "zona oeste",
    "lapa": "zona oeste",
    "perdizes": "zona oeste",
    "centro": "centro",
    "republica": "centro",
}


def zona_do_imovel(cidade: str, bairro: str) -> str:
    """Zona como o lead fala. Capital: pelo bairro; resto: pela cidade.
    Bairro da capital que não está no mapa cai em "grande sao paulo" em vez
    de ser chutado -- zona errada é pior que zona genérica."""
    cid = _sem_acento(cidade).lower()
    if cid == "sao paulo":
        return ZONA_CAPITAL_POR_BAIRRO.get(_sem_acento(bairro).lower(), "grande sao paulo")
    return ZONA_POR_CIDADE.get(cid, "grande sao paulo")


# Duas grafias porque há duas fontes: `property_type` do cadastro vem em
# inglês maiúsculo (HOUSE, LAND), a trilha da página vem em português. A
# trilha é a reserva quando o cadastro traz um tipo que não conhecemos.
TIPO_NORMALIZADO = {
    # trilha (português)
    "apartamento": "apartamento", "casa": "casa", "studio": "studio",
    "cobertura": "cobertura", "sala": "sala", "terreno": "terreno",
    "galpao": "galpao", "loja": "loja", "predio": "predio",
    "chacara": "chacara", "resort": "resort", "sobrado": "casa",
    # cadastro (inglês)
    "house": "casa", "apartment": "apartamento", "land": "terreno",
    "studio_apartment": "studio", "penthouse": "cobertura",
    "commercial_room": "sala", "office": "sala", "store": "loja",
    "warehouse": "galpao", "building": "predio", "farm": "chacara",
    "country_house": "chacara", "townhouse": "casa", "flat": "apartamento",
    "commercial_building": "predio", "shed": "galpao", "resort": "resort",
}


# Comodidades: o cadastro mistura característica do imóvel com flag
# interna do CRM (`pictures`, `tag_site`, `tag_full_url` não são
# comodidade nenhuma). Só entra o que está neste dicionário -- lista de
# permissão, não de bloqueio: um código novo e desconhecido fica de fora
# em vez de vazar "tag_full_url" pro cliente.
COMODIDADES_PT = {
    "gated_community": "condomínio fechado",
    "swimming_pool": "piscina",
    "barbecue_grill": "churrasqueira",
    "fireplace": "lareira",
    "sauna": "sauna",
    "gym": "academia",
    "playground": "playground",
    "party_room": "salão de festas",
    "gourmet_area": "espaço gourmet",
    "balcony": "varanda",
    "gourmet_balcony": "varanda gourmet",
    "garden": "jardim",
    "backyard": "quintal",
    "elevator": "elevador",
    "furnished": "mobiliado",
    "air_conditioning": "ar-condicionado",
    "cable_tv": "TV a cabo",
    "office": "escritório",
    "pantry": "despensa",
    "kitchen": "cozinha",
    "laundry_room": "lavanderia",
    "laundry_room_br": "área de serviço",
    "maid_room": "dependência de empregada",
    "maid_bathroom": "banheiro de empregada",
    "breakfast_nook": "copa",
    "cabinet_closet": "armários embutidos",
    "hydromassage_bathtub": "banheira de hidromassagem",
    "halfbath": "lavabo",
    "sports_court": "quadra",
    "concierge_24h": "portaria 24h",
    "security": "segurança",
    "sewer": "esgoto",
    "water": "água encanada",
    "paving": "rua pavimentada",
    "electric_energy": "energia elétrica",
}


def _comodidades(brutas) -> list[str]:
    """Traduz e filtra. Mantém a ordem do cadastro e não repete."""
    saida, vistas = [], set()
    for bruta in brutas or []:
        nome = COMODIDADES_PT.get(str(bruta).lower())
        if nome and nome not in vistas:
            vistas.add(nome)
            saida.append(nome)
    return saida


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def _get(http: httpx.Client, url: str, *, tentativas: int = 4):
    """GET com recuo. Um 429 no meio de 270 páginas perderia o lote."""
    espera = 2.0
    for _ in range(tentativas):
        try:
            resp = http.get(url)
        except httpx.HTTPError:
            time.sleep(espera)
            espera *= 2
            continue
        if resp.status_code == 429:
            time.sleep(espera)
            espera *= 2
            continue
        if resp.status_code >= 500:
            time.sleep(espera)
            espera *= 2
            continue
        return resp if resp.status_code == 200 else None
    return None


# ---------------------------------------------------------------- descoberta

def urls_dos_anuncios(http: httpx.Client) -> list[str]:
    """Anúncios individuais, pelo sitemap -- não por navegação. O sitemap é
    a lista que o próprio site publica pra ser lida por máquina."""
    indice = _get(http, SITEMAP)
    if indice is None:
        return []
    sitemaps = re.findall(r"<loc>([^<]+)</loc>", indice.text)

    urls: set[str] = set()
    for sm in sitemaps:
        if "/imoveis/" not in sm:
            continue
        pagina = _get(http, sm)
        if pagina is None:
            continue
        for u in re.findall(r"<loc>([^<]+)</loc>", pagina.text):
            # `/imovel/<slug>/<CODIGO>` é o anúncio; `/imoveis/...` é
            # listagem de categoria e não tem imóvel nenhum pra extrair.
            if "/imovel/" in u:
                urls.add(u)
        time.sleep(0.4)
    return sorted(urls)


# ---------------------------------------------------------------- extração

def _blocos_ld(html: str) -> list[dict]:
    saida = []
    for bruto in re.findall(
        r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            saida.append(json.loads(bruto))
        except json.JSONDecodeError:
            continue
    return saida


def _bairro_do_breadcrumb(blocos: list[dict]) -> str:
    """Penúltimo nível da trilha. A estrutura é
    Home > Imóveis > <finalidade> > <tipo> > <cidade> > <bairro> > <imóvel>,
    então o bairro é o item anterior ao próprio anúncio."""
    for bloco in blocos:
        if bloco.get("@type") != "BreadcrumbList":
            continue
        itens = bloco.get("itemListElement") or []
        if len(itens) >= 2:
            anterior = itens[-2].get("item") or {}
            return (anterior.get("name") or "").strip(" .,-")
    return ""


def _trilha(blocos: list[dict]) -> list[str]:
    for bloco in blocos:
        if bloco.get("@type") == "BreadcrumbList":
            return [(i.get("item") or {}).get("name", "") for i in bloco.get("itemListElement") or []]
    return []


# A imobiliária da demonstração é a Horizonte; o catálogo é real e vem do
# sistema da Orion, com autorização. Troca-se a marca no TEXTO -- código do
# anúncio, título e descrição -- pra que a Bia não se apresente como uma
# empresa e cite o código de outra.
#
# O que isto NÃO resolve: a marca d'água "ORION IMÓVEIS" está queimada no
# pixel das fotos. Renomear texto não mexe em imagem; ou se recorta, ou se
# aceita que a foto mostra a origem do acervo.
SUFIXO_ORIGEM = "-ORIO"
SUFIXO_MARCA = "-HORZ"

_TROCAS_MARCA = (
    ("Orion Imóveis", "Horizonte Imóveis"),
    ("Orion Imoveis", "Horizonte Imoveis"),
    ("ORION", "HORIZONTE"),
    ("Orion", "Horizonte"),
    ("orion", "horizonte"),
)


def _marca(texto: str) -> str:
    """Troca a marca de origem pela da demonstração, inclusive no sufixo do
    código do anúncio (`CA0432-ORIO` -> `CA0432-HORZ`)."""
    saida = (texto or "").replace(SUFIXO_ORIGEM, SUFIXO_MARCA)
    for de, para in _TROCAS_MARCA:
        saida = saida.replace(de, para)
    return saida


def _um(valor):
    """O JSON-LD do site às vezes entrega um objeto, às vezes uma lista com
    um só elemento. Normaliza pra objeto."""
    if isinstance(valor, list):
        return valor[0] if valor else {}
    return valor or {}


def _faixa(valor, padrao: int = 0) -> int:
    """O cadastro guarda número como faixa `[min, max]` (preço "a partir
    de", área "de X a Y"). Pro catálogo interessa o primeiro valor."""
    if isinstance(valor, list):
        valor = valor[0] if valor else padrao
    try:
        return int(valor or padrao)
    except (TypeError, ValueError):
        return padrao


def payload_do_anuncio(html: str) -> dict | None:
    """O objeto `imob_property` embutido na página.

    Vale muito mais que o JSON-LD: traz condomínio, IPTU, suítes, vagas e
    a lista de comodidades, que o schema.org do site não expõe.

    Parte do marcador e tenta decodificar a partir de cada `{` anterior, do
    mais próximo ao mais distante, até um objeto inteiro cujo `type` seja
    mesmo `imob_property`. `raw_decode` entende string e escape.

    Versão anterior contava chaves à mão com teto de 80 mil caracteres pra
    trás. Falhou em 84 de 268 anúncios, justamente os com mais fotos e
    descrição longa (o objeto passa de 100 mil caracteres), e entre eles
    estavam quase todos os apartamentos. Dois defeitos juntos: o teto, e
    contar `{` e `}` que aparecem dentro de texto."""
    marca = html.find('"type":"imob_property"')
    if marca < 0:
        return None

    decodificador = json.JSONDecoder()
    posicao, tentativas = marca, 0
    while tentativas < 600:
        posicao = html.rfind("{", 0, posicao)
        if posicao < 0:
            return None
        tentativas += 1
        try:
            objeto, fim = decodificador.raw_decode(html, posicao)
        except json.JSONDecodeError:
            continue
        # Tem que ser o objeto que CONTÉM o marcador, não um aninhado antes.
        if (isinstance(objeto, dict) and objeto.get("type") == "imob_property"
                and fim > marca):
            return objeto
    return None


def _banheiros(dados: dict, tipo: str, quartos: int) -> tuple[int, bool]:
    """Banheiros, e se o número foi DERIVADO.

    O cadastro da imobiliária deixa `bathrooms` em zero na maior parte dos
    anúncios -- não porque o imóvel não tenha, mas porque ninguém
    preencheu. Quando falta, estima-se pelo que existe: uma suíte já é um
    banheiro, e um imóvel de N quartos raramente tem menos de um banheiro
    social além das suítes.

    O booleano volta junto de propósito: campo estimado não pode ser dito
    ao cliente como se fosse do cadastro (ver `catalogo_orion.json` ->
    `campos_estimados`)."""
    informado = _faixa(dados.get("bathrooms"))
    if informado > 0:
        return informado, False
    if tipo in ("terreno",):
        return 0, False          # terreno não tem banheiro: zero é o certo
    suites = _faixa(dados.get("suites"))
    if quartos <= 0:
        return max(suites, 1), True
    return max(suites + 1, 1), True


def extrair(html: str, url: str) -> dict | None:
    dados = payload_do_anuncio(html)
    if not dados:
        return None

    blocos = _blocos_ld(html)
    anuncio = next((b for b in blocos if b.get("@type") == "RealEstateListing"), {})

    # Finalidade pelo cadastro, não pela trilha: `property_purposes` é o
    # campo que a própria imobiliária usa, e um imóvel anunciado pras duas
    # coisas traz as duas aqui.
    propositos = [str(p).upper() for p in (dados.get("property_purposes") or [])]
    aluguel = "FOR_RENT" in propositos
    finalidade = "aluguel" if aluguel else "venda"
    preco = _faixa(dados.get("rent_price") if aluguel else dados.get("sale_price"))
    if preco <= 0:
        return None

    tipo = TIPO_NORMALIZADO.get(
        _sem_acento(str(dados.get("property_type") or "")).lower(), ""
    ) or _tipo_pela_trilha(blocos)

    quartos = _faixa(dados.get("bedrooms"))
    banheiros, banheiros_estimados = _banheiros(dados, tipo, quartos)

    # Área útil primeiro; `area` e `gross_floor_area` como reserva. Terreno
    # costuma ter só a bruta.
    area = (_faixa(dados.get("usable_floor_area"))
            or _faixa(dados.get("area"))
            or _faixa(dados.get("gross_floor_area")))

    cidade = (dados.get("city") or "").strip()
    condominio = _faixa(dados.get("condo_fees")) or None
    iptu = _faixa(dados.get("property_tax")) or None

    estimados = []
    if banheiros_estimados:
        estimados.append("banheiros")

    return {
        # `CA0432-ORIO` -> `CA0432-HORZ`: o catálogo é real, a imobiliária
        # da demonstração é a Horizonte (ver _marca()).
        "codigo": _marca(dados.get("property_full_reference") or url.rsplit("/", 1)[-1]),
        "url": url,
        "titulo": _marca(dados.get("heading1") or dados.get("website_title") or anuncio.get("name") or ""),
        "descricao": _marca(dados.get("listing_description") or anuncio.get("description") or ""),
        "tipo": tipo,
        "categoria": (dados.get("property_category") or "").lower(),
        "finalidade": finalidade,
        "bairro": (dados.get("neighborhood_display") or dados.get("neighborhood") or "").strip(" .,-"),
        "cidade": cidade,
        "zona": zona_do_imovel(cidade, (dados.get("neighborhood_display") or dados.get("neighborhood") or "").strip(" .,-")),
        "preco": float(preco),
        "condominio": float(condominio) if condominio else None,
        "iptu": float(iptu) if iptu else None,
        "quartos": quartos,
        "suites": _faixa(dados.get("suites")),
        "banheiros": banheiros,
        "vagas": _faixa(dados.get("garages")),
        "area": area,
        "comodidades": _comodidades(dados.get("amenities")),
        # Marca o que NÃO veio do cadastro. O agente não afirma estes como
        # fato -- é o que mantém de pé a garantia de não inventar dado.
        "campos_estimados": estimados,
        "fotos_origem": [f for f in (anuncio.get("image") or [])][:FOTOS_POR_IMOVEL],
    }


def _tipo_pela_trilha(blocos: list[dict]) -> str:
    trilha = _trilha(blocos)
    bruto = _sem_acento(trilha[3] if len(trilha) > 3 else "").lower().strip()
    return TIPO_NORMALIZADO.get(bruto, bruto or "imovel")


# ---------------------------------------------------------------- fotos

def _reduzir(bruto: bytes, destino: Path) -> bool:
    """Reduz pra LARGURA_FOTO com ffmpeg -- já é dependência do projeto
    (a síntese de voz usa). No tamanho original os 270 anúncios passariam
    de 65 MB, e ninguém clona um repositório desses pra avaliar."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "pipe:0",
         "-vf", f"scale='min({LARGURA_FOTO},iw)':-2",
         "-q:v", "4", "-f", "mjpeg", str(destino)],
        input=bruto, capture_output=True,
    )
    return proc.returncode == 0 and destino.exists() and destino.stat().st_size > 0


def baixar_fotos(http: httpx.Client, imovel: dict) -> list[str]:
    FOTOS.mkdir(parents=True, exist_ok=True)
    caminhos = []
    # Foto excluída de propósito (captura do Google, por exemplo) não volta:
    # o arquivo não existe mais, e sem esta checagem a próxima importação
    # a rebaixaria como se faltasse.
    excluidas = curadoria.carregar()["excluidas"]
    for i, url in enumerate(imovel["fotos_origem"], start=1):
        nome = f"{imovel['codigo']}-{i}.jpg"
        if nome in excluidas:
            continue
        destino = FOTOS / nome
        if destino.exists():
            caminhos.append(f"/static/imoveis/orion/{nome}")
            continue
        resp = _get(http, url)
        if resp is None or len(resp.content) < MIN_BYTES:
            continue
        if not resp.content.startswith(b"\xff\xd8\xff"):
            continue
        if _reduzir(resp.content, destino):
            caminhos.append(f"/static/imoveis/orion/{nome}")
        time.sleep(0.2)
    return caminhos


# ---------------------------------------------------------------- principal

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=0, help="só os N primeiros (teste)")
    parser.add_argument("--sem-fotos", action="store_true", help="só os dados")
    args = parser.parse_args()

    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as http:
        print("==> descobrindo anúncios pelo sitemap")
        urls = urls_dos_anuncios(http)
        if args.limite:
            urls = urls[: args.limite]
        print(f"    {len(urls)} anúncios")

        catalogo, falhas = [], []
        for n, url in enumerate(urls, start=1):
            resp = _get(http, url)
            if resp is None:
                falhas.append(url)
                continue
            imovel = extrair(resp.text, url)
            if not imovel or not imovel["titulo"] or imovel["preco"] is None:
                falhas.append(url)
                continue

            imovel["fotos"] = [] if args.sem_fotos else baixar_fotos(http, imovel)
            catalogo.append(imovel)
            if n % 10 == 0 or n == len(urls):
                print(f"    {n}/{len(urls)}  {imovel['codigo']}  {imovel['bairro']}, {imovel['cidade']}")
            time.sleep(0.4)

    DATASET.write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    peso = sum(f.stat().st_size for f in FOTOS.glob("*.jpg")) // 1024 if FOTOS.exists() else 0
    com_foto = sum(1 for i in catalogo if i["fotos"])
    print(f"\n{len(catalogo)} imóveis em {DATASET.relative_to(RAIZ)}")
    print(f"{com_foto} com foto · {peso // 1024} MB em {FOTOS.relative_to(RAIZ)}")
    if falhas:
        print(f"{len(falhas)} sem dado estruturado (ignorados)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
