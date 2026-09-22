"""CLI de teste da busca hibrida. Criterio de pronto da Etapa 2 (PLANO.md).

Uso:
    python -m scripts.buscar --finalidade venda --zona "zona sul" --quartos 3 \
        --preco-max 800000 --texto "varanda gourmet"
"""

import argparse

from app.agents.search import buscar_imoveis


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--finalidade", choices=["venda", "aluguel"])
    p.add_argument("--zona")
    p.add_argument("--preco-min", type=int)
    p.add_argument("--preco-max", type=int)
    p.add_argument("--quartos", type=int, help="minimo de quartos")
    p.add_argument("--texto", help="descritivo livre, ex: 'varanda gourmet perto do parque'")
    p.add_argument("--limite", type=int, default=5)
    args = p.parse_args()

    resultados = buscar_imoveis(
        finalidade=args.finalidade,
        zona=args.zona,
        preco_min=args.preco_min,
        preco_max=args.preco_max,
        quartos_min=args.quartos,
        texto_livre=args.texto,
        limite=args.limite,
    )

    if not resultados:
        print("Nenhum imovel encontrado com esses criterios.")
        return

    for im in resultados:
        score = f" score={im['score']:.3f}" if im.get("score") is not None else ""
        print(f"\n[{im['id']}] {im['titulo']} -- {im['bairro']} ({im['zona']}){score}")
        print(f"    {im['finalidade']} · R$ {im['preco']:,.0f} · {im['quartos']}q {im['banheiros']}b {im['vagas']}v · {im['area']}m2")
        if im.get("rentabilidade_estimada"):
            print(f"    rentabilidade estimada: {im['rentabilidade_estimada']}% a.a.")
        print(f"    {im['descricao']}")


if __name__ == "__main__":
    main()
