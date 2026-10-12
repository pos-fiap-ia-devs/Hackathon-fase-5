"""Acha, no acervo, as fotos que NÃO são da imobiliária.

Por que existe
--------------
O catálogo veio do sistema da imobiliária parceira, com autorização dela.
Só que nem toda imagem anexada a um anúncio é dela: alguns corretores
anexaram **captura do Google Street View** (comum em terreno, onde não há
o que fotografar por dentro), **print de mapa** e **"Imagem Ilustrativa"**
de material de lançamento.

A autorização do dono do site não cobre isso. Street View é imagem da
Google, e redistribuí-la num trabalho entregue é problema de direito
autoral -- o mesmo motivo pelo qual não se raspou OLX nem VivaReal.

Como se acha
------------
Por OCR do rodapé, não por palpite. Tentou-se primeiro uma heurística de
brilho (a barra do Street View tem texto branco embaixo): não separou
nada -- a captura conhecida pontuou 0,059 e fotos legítimas de piso claro
chegaram a 0,98. O que distingue é o TEXTO, então lê-se o texto.

O rodapé é ampliado antes de passar pelo tesseract: a barra do Street View
ocupa ~4% da altura, e nessa escala a palavra "Google" sai como ruído.

    python -m scripts.achar_terceiros            # lista, não apaga
    python -m scripts.achar_terceiros --apagar   # apaga e tira do catálogo
"""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from PIL import Image

from scripts import curadoria

RAIZ = Path(__file__).resolve().parent.parent
ACERVO = RAIZ / "app" / "web" / "static" / "imoveis" / "orion"
CATALOGO = RAIZ / "app" / "db" / "catalogo_orion.json"

# Fração da altura lida. A barra do Street View fica nos últimos ~5%; o
# rodapé do mapa (Leaflet) e o "SEM ESCALA" da imagem ilustrativa podem
# subir um pouco mais.
FAIXA = 0.12
# O OCR erra feio em texto de 8 px. Amplia antes de ler.
AMPLIACAO = 4

# Marcas que significam "esta imagem é da GOOGLE". É o que sempre se remove:
# a autorização do dono do site não cobre imagem de terceiro. Minúsculas e
# sem acento -- o texto do OCR é normalizado antes de comparar.
MARCAS_GOOGLE = (
    "google",
    "captura da imagem",
    "dados cartograficos",
    "leaflet",
    "openstreetmap",
    "mapbox",
    "street view",
)

# "Imagem Ilustrativa" é render de material de lançamento. NÃO é de
# terceiro -- é da própria imobiliária, e portanto coberta pela autorização
# --, mas também não é foto do imóvel. Fica fora da remoção padrão: é
# decisão editorial (mostra o projeto, não a obra), não de direito autoral.
MARCAS_ILUSTRATIVA = ("imagem ilustrativa", "sem escala")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def _ler(foto, faixa: float, psm: int) -> str:
    largura, altura = foto.size
    recorte = foto.crop((0, int(altura * (1 - faixa)), largura, altura))
    recorte = recorte.resize((recorte.width * AMPLIACAO, recorte.height * AMPLIACAO),
                             Image.LANCZOS)
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "por+eng", "--psm", str(psm)],
        input=_para_png(recorte), capture_output=True,
    )
    if proc.returncode != 0:
        return ""
    return _sem_acento(proc.stdout.decode("utf-8", "replace")).lower()


def texto_do_rodape(caminho: Path) -> str:
    """Duas leituras, texto unido. Uma só deixa passar: `--psm 6` (bloco
    uniforme) acha a barra do Street View, `--psm 11` (texto esparso, faixa
    maior) acha a atribuição solta de um mapa. Cada uma perde o que a outra
    pega."""
    with Image.open(caminho) as imagem:
        foto = imagem.convert("L")
    return _ler(foto, FAIXA, 6) + "\n" + _ler(foto, 0.20, 11)


def _para_png(imagem: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    imagem.save(buffer, "PNG")
    return buffer.getvalue()


def marcas_encontradas(texto: str, marcas: tuple[str, ...] = MARCAS_GOOGLE) -> list[str]:
    # `\b` nos dois lados: "google" dentro de outra palavra não conta, e
    # sem isso o OCR ruidoso gera falso positivo com facilidade.
    return [m for m in marcas if re.search(rf"\b{re.escape(m)}\b", texto)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apagar", action="store_true",
                        help="apaga os arquivos e remove do catálogo")
    parser.add_argument("--tudo", action="store_true",
                        help="reverifica também as já conferidas (lento: OCR em cada uma)")
    parser.add_argument("--incluir-ilustrativas", action="store_true",
                        help="também trata renders 'Imagem Ilustrativa' como alvo")
    args = parser.parse_args()

    alvo = MARCAS_GOOGLE + (MARCAS_ILUSTRATIVA if args.incluir_ilustrativas else ())
    dados = curadoria.carregar()
    fotos = sorted(ACERVO.glob("*.jpg"))
    if not args.tudo:
        # OCR em duas configurações por foto é o passo mais lento do
        # acervo (~8 min pras 489). Só se lê o que ainda não foi conferido.
        fotos = [f for f in fotos if f.name not in dados["terceiros_verificadas"]]
    if not fotos:
        print("nada novo a verificar")
        return 0

    suspeitas: dict[str, list[str]] = {}
    for n, foto in enumerate(fotos, start=1):
        if not foto.exists():      # sumiu no meio (outro processo apagou)
            continue
        marcas = marcas_encontradas(texto_do_rodape(foto), alvo)
        dados["terceiros_verificadas"].add(foto.name)
        if marcas:
            suspeitas[foto.name] = marcas
            print(f"  {foto.name:<24} {', '.join(marcas)}")
        if n % 100 == 0:
            print(f"  ... {n}/{len(fotos)}")

    print(f"\n{len(suspeitas)} de {len(fotos)} fotos são de terceiro")
    if not args.apagar:
        curadoria.salvar(dados)
        print("(nada foi apagado -- use --apagar)")
        return 0
    dados["excluidas"].update(suspeitas)
    curadoria.salvar(dados)

    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))
    removidas = {f"/static/imoveis/orion/{nome}" for nome in suspeitas}
    sem_foto = 0
    for imovel in catalogo:
        antes = imovel.get("fotos") or []
        imovel["fotos"] = [f for f in antes if f not in removidas]
        if antes and not imovel["fotos"]:
            sem_foto += 1
    CATALOGO.write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for nome in suspeitas:
        (ACERVO / nome).unlink(missing_ok=True)

    print(f"apagadas · {sem_foto} imóveis ficaram sem nenhuma foto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
