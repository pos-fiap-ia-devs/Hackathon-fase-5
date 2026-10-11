"""Baixa, UMA VEZ, as fotos de demonstração dos imóveis.

Por que existe
--------------
O seed usava URLs do `loremflickr.com` direto nos cards. Isso quebrou: o
serviço passou a responder **401** e, de um dia pro outro, o Telegram
parou de enviar foto nenhuma (o álbum é pulado em silêncio quando o
download falha) e o chat web passou a mostrar ícone de imagem quebrada.
Nada no nosso código mudou.

Depender de serviço de terceiro em tempo de execução, numa demonstração
ao vivo, é risco sem contrapartida: basta a rede do auditório engasgar e a
galeria some na frente da banca. As fotos agora moram no repositório, e
este script só é rodado de novo se alguém quiser trocá-las.

As imagens são genéricas e NÃO correspondem aos imóveis -- a base é
simulada. Isso está dito no manifesto de imagens e no README.

    python -m scripts.baixar_fotos            # baixa o que falta
    python -m scripts.baixar_fotos --forcar   # rebaixa tudo
"""

import argparse
import sys
from pathlib import Path

import httpx

DESTINO = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "imoveis"

# Um pool pequeno, reaproveitado entre os 200 imóveis do seed: 3 fotos por
# imóvel × 200 seriam 600 arquivos pra uma base que é simulada de qualquer
# forma. 8 por tipo dá variedade suficiente pra duas opções lado a lado na
# mesma lista não saírem idênticas (app/db/seed.py::_fotos_do_imovel).
TIPOS = ("apartamento", "casa", "studio", "cobertura", "sala")
POR_TIPO = 8

FONTE = "https://picsum.photos/seed/{semente}/800/600"
# Assinatura de JPEG. O arquivo vem de fora e vai ser servido pela
# aplicação: confere-se que é mesmo imagem antes de gravar.
MAGIC_JPEG = b"\xff\xd8\xff"


def _nome(tipo: str, i: int) -> str:
    return f"{tipo}-{i:02d}.jpg"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forcar", action="store_true", help="rebaixa mesmo o que já existe")
    args = parser.parse_args()

    DESTINO.mkdir(parents=True, exist_ok=True)
    baixados = pulados = 0

    with httpx.Client(timeout=30, follow_redirects=True) as http:
        for tipo in TIPOS:
            for i in range(1, POR_TIPO + 1):
                caminho = DESTINO / _nome(tipo, i)
                if caminho.exists() and not args.forcar:
                    pulados += 1
                    continue
                # Semente fixa por arquivo: rodar de novo traz a MESMA foto,
                # então trocar o pool é decisão explícita, não acidente.
                resp = http.get(FONTE.format(semente=f"{tipo}-{i}"))
                resp.raise_for_status()
                if not resp.content.startswith(MAGIC_JPEG):
                    print(f"  {caminho.name}: resposta não é JPEG, ignorada", file=sys.stderr)
                    continue
                caminho.write_bytes(resp.content)
                baixados += 1
                print(f"  {caminho.name}  ({len(resp.content) // 1024} KB)")

    total = sum(f.stat().st_size for f in DESTINO.glob("*.jpg"))
    print(f"\n{baixados} baixadas, {pulados} já existiam · {total // 1024} KB em {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
