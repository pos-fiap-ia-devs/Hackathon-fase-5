"""Remove a marca d'água do acervo de fotos, por desfoque.

Por que existe
--------------
O catálogo é real e veio do sistema da imobiliária parceira, mas as fotos
têm a marca d'água da ORIGEM queimada no pixel. O nome da empresa a gente
troca no texto (`scripts/importar_orion.py::_marca`); na imagem, não há
texto pra trocar -- ou se recorta, ou se desfoca, ou se aceita.

Aqui se desfoca: recortar o canto superior direito jogaria fora parte da
foto, e em imagem de fachada é justamente ali que costuma estar o telhado.

Como se acha a marca
--------------------
Não por cor. Tentou-se: a marca é semitransparente e, numa foto de parede
bege, tem a MESMA matiz do fundo (H 0,074-0,118 contra 0,073-0,085) -- cor
não separa os dois.

O que separa é geometria. Medindo nas imagens originais (todas servidas
com 1280 de largura), a marca é ancorada no canto superior direito e seu
tamanho acompanha a ALTURA da imagem, ~19% dela:

    original 1280x2281  ->  marca ~440 de altura   (19,3%)
    original 1280x720   ->  marca ~120 de altura   (16,7%)

A caixa usada aqui é maior que a medida, de propósito: errar pra mais
desfoca um pedaço de parede, errar pra menos deixa "IMÓVEIS" legível --
e já deixou, numa primeira tentativa com caixa justa.

A borda é esfumada (máscara com degradê). Sem isso fica um retângulo
nítido de desfoque, que numa parede lisa salta aos olhos mais que a
própria marca.

    python -m scripts.borrar_marca              # aplica no acervo
    python -m scripts.borrar_marca --amostra    # 3 fotos, pra conferir
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from scripts import curadoria

RAIZ = Path(__file__).resolve().parent.parent
ACERVO = RAIZ / "app" / "web" / "static" / "imoveis" / "orion"

# Fração da ALTURA que a caixa de desfoque ocupa. A marca medida fica entre
# 17% e 20% -- mas a caixa precisa ser bem maior que ela, porque a borda é
# esfumada: com 26% o "IMÓVEIS" caía dentro do degradê e continuava
# legível em 4 de cada 20 fotos. 36% põe o texto inteiro na parte opaca.
ALTURA_CAIXA = 0.36
# Largura em função da própria caixa: a marca é quase quadrada (ícone em
# cima, "ORION" e "IMÓVEIS" embaixo).
LARGURA_SOBRE_ALTURA = 0.95
# Teto de segurança: numa foto muito baixa e larga, a regra acima poderia
# querer quase metade da imagem.
LARGURA_MAXIMA = 0.45

# Esfumado da borda, em fração da menor dimensão da caixa.
ESFUMADO = 0.18
# Raio do desfoque, idem. Precisa ser alto: o texto da marca é grande.
DESFOQUE = 0.16

QUALIDADE = 78   # re-grava mais leve; o acervo inteiro cabe no repositório


def _mascara(largura: int, altura: int) -> Image.Image:
    """Máscara em degradê: opaca no miolo, transparente nas bordas que dão
    pra dentro da foto (esquerda e baixo). Em cima e à direita a caixa
    encosta na borda da imagem, então ali não há o que esfumar."""
    mascara = Image.new("L", (largura, altura), 0)
    desenho = ImageDraw.Draw(mascara)
    passo = max(1, int(min(largura, altura) * ESFUMADO))
    # Retângulos concêntricos cada vez mais claros; o blur depois alisa os
    # degraus. Mais barato e mais previsível que um degradê por pixel.
    for i in range(passo, 0, -1):
        valor = int(255 * (1 - i / passo))
        desenho.rectangle(
            [max(0, passo - i), 0, largura, altura - (passo - i)],
            fill=valor,
        )
    desenho.rectangle([passo, 0, largura, altura - passo], fill=255)
    return mascara.filter(ImageFilter.GaussianBlur(passo / 3))


def caixa_da_marca(largura: int, altura: int) -> tuple[int, int, int, int]:
    """Canto superior direito, dimensionado pela altura da imagem."""
    alto = int(altura * ALTURA_CAIXA)
    largo = min(int(alto * LARGURA_SOBRE_ALTURA), int(largura * LARGURA_MAXIMA))
    return largura - largo, 0, largura, alto


def borrar(caminho: Path, *, destino: Path | None = None) -> bool:
    with Image.open(caminho) as imagem:
        foto = imagem.convert("RGB")

    esquerda, topo, direita, base = caixa_da_marca(*foto.size)
    recorte = foto.crop((esquerda, topo, direita, base))
    largura, altura = recorte.size
    if largura < 8 or altura < 8:
        return False

    raio = max(6, int(min(largura, altura) * DESFOQUE))
    borrado = recorte.filter(ImageFilter.GaussianBlur(raio))
    foto.paste(borrado, (esquerda, topo), _mascara(largura, altura))
    foto.save(destino or caminho, "JPEG", quality=QUALIDADE, optimize=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amostra", action="store_true",
                        help="grava 3 exemplos ao lado, sem tocar no acervo")
    parser.add_argument("--saida", default="", help="pasta das amostras")
    parser.add_argument("--tudo", action="store_true",
                        help="refaz também as já processadas (borra de novo -- só use "
                             "depois de reimportar os originais)")
    args = parser.parse_args()

    fotos = sorted(ACERVO.glob("*.jpg"))
    if not fotos:
        print(f"nenhuma foto em {ACERVO}", file=sys.stderr)
        return 1

    if args.amostra:
        saida = Path(args.saida or ".")
        saida.mkdir(parents=True, exist_ok=True)
        # Uma de cada formato, pra conferir que a regra vale nos extremos.
        por_formato: dict[str, Path] = {}
        for foto in fotos:
            with Image.open(foto) as im:
                largura, altura = im.size
            forma = "retrato" if altura > largura * 1.2 else (
                "paisagem" if largura > altura * 1.2 else "quadrada")
            por_formato.setdefault(forma, foto)
        for forma, foto in por_formato.items():
            alvo = saida / f"amostra_{forma}.jpg"
            borrar(foto, destino=alvo)
            print(f"  {forma:<10} {foto.name} -> {alvo}")
        return 0

    # Borrar é DESTRUTIVO: grava por cima do arquivo. Rodar duas vezes borra
    # duas vezes e a foto vai ficando pior. Por isso só se processa o que o
    # manifesto não registra como feito.
    dados = curadoria.carregar()
    if not args.tudo:
        fotos = [f for f in fotos if f.name not in dados["marca_removida"]]
    if not fotos:
        print("nada novo a borrar")
        return 0

    antes = sum(f.stat().st_size for f in fotos)
    feitas = 0
    for n, foto in enumerate(fotos, start=1):
        if borrar(foto):
            feitas += 1
            dados["marca_removida"].add(foto.name)
        if n % 100 == 0:
            print(f"  {n}/{len(fotos)}")
    curadoria.salvar(dados)
    depois = sum(f.stat().st_size for f in fotos if f.exists())
    print(f"\n{feitas} fotos · {antes // 1024} KB -> {depois // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
