"""Seed de 200 imoveis no estado de Sao Paulo -- 10 cidades, bairros e
faixas de preco reais de mercado.

Composicao (secao 8, Etapa 2 do PLANO.md):
  120 venda | 55 aluguel | 25 venda marcados para investimento

Faixas (pedido do usuario, garantidas por clamp em `_preco_na_faixa`):
  venda   R$ 110 mil a R$ 2 milhoes
  aluguel R$ 1.200 a R$ 30 mil/mes

Modelo de regiao: a coluna `zona` guarda como o LEAD fala da regiao, nao
uma subdivisao administrativa. Na capital isso e zona sul/norte/leste/
oeste/centro; fora dela ninguem diz "zona" -- diz a cidade. Por isso as
demais entram agrupadas em "grande sao paulo" e "interior", e a busca
casa `zona` OU `bairro` OU `cidade` (app/agents/search.py), cobrindo as
tres formas de o cliente responder "onde voce procura?".

Descritivos sao gerados por template combinatorio, nunca por LLM -- 200
chamadas ao 9B so para texto de marketing e tempo que a Etapa 2 nao tem.

Rodar: python -m app.db.seed
"""

import random

from app.core.llm import embed_text
from app.db.repo import get_conn

random.seed(42)  # reproduzivel -- reusado pelo demo_reset (Etapa 10)

# cidade -> (zona como o lead fala, [(bairro, tier de preco)])
CIDADES: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "Sao Paulo": [
        ("zona sul", [
            ("Moema", "alto"), ("Vila Mariana", "alto"), ("Brooklin", "alto"),
            ("Campo Belo", "medio"), ("Santo Amaro", "medio"),
        ]),
        ("zona oeste", [
            ("Pinheiros", "alto"), ("Vila Madalena", "alto"), ("Perdizes", "medio"),
            ("Butanta", "medio"), ("Lapa", "medio"),
        ]),
        ("zona norte", [
            ("Santana", "medio"), ("Tucuruvi", "popular"),
            ("Casa Verde", "popular"), ("Vila Guilherme", "popular"),
        ]),
        ("zona leste", [
            ("Tatuape", "medio"), ("Mooca", "medio"),
            ("Penha", "popular"), ("Itaquera", "popular"),
        ]),
        ("centro", [
            ("Bela Vista", "medio"), ("Consolacao", "alto"),
            ("Republica", "popular"), ("Liberdade", "popular"),
        ]),
    ],
    "Guarulhos": [
        ("grande sao paulo", [
            ("Centro", "medio"), ("Vila Galvao", "medio"),
            ("Picanco", "popular"), ("Jardim Zaira", "popular"),
        ]),
    ],
    "Sao Bernardo do Campo": [
        ("grande sao paulo", [
            ("Jardim do Mar", "alto"), ("Centro", "medio"),
            ("Rudge Ramos", "medio"), ("Baeta Neves", "popular"),
        ]),
    ],
    "Santo Andre": [
        ("grande sao paulo", [
            ("Campestre", "alto"), ("Vila Assuncao", "medio"),
            ("Centro", "medio"), ("Santa Paula", "popular"),
        ]),
    ],
    "Osasco": [
        ("grande sao paulo", [
            ("Vila Yara", "medio"), ("Centro", "medio"),
            ("Presidente Altino", "popular"), ("Jardim das Flores", "popular"),
        ]),
    ],
    "Campinas": [
        ("interior", [
            ("Cambui", "alto"), ("Taquaral", "alto"),
            ("Barao Geraldo", "medio"), ("Centro", "popular"),
        ]),
    ],
    "Sao Jose dos Campos": [
        ("interior", [
            ("Jardim Aquarius", "alto"), ("Urbanova", "alto"),
            ("Jardim Esplanada", "medio"), ("Centro", "popular"),
        ]),
    ],
    "Ribeirao Preto": [
        ("interior", [
            ("Jardim Botanico", "alto"), ("Jardim Iraja", "alto"),
            ("Ribeirania", "medio"), ("Centro", "popular"),
        ]),
    ],
    "Sorocaba": [
        ("interior", [
            ("Parque Campolim", "alto"), ("Campolim", "medio"),
            ("Vila Hortencia", "popular"), ("Centro", "popular"),
        ]),
    ],
    "Sao Jose do Rio Preto": [
        ("interior", [
            ("Redentora", "alto"), ("Bosque da Saude", "medio"),
            ("Vila Imperial", "popular"), ("Centro", "popular"),
        ]),
    ],
}

# R$/m2 de mercado. Venda: preco total. Aluguel: mensal.
TIERS = {
    "alto": {"venda_m2": (9000, 16000), "aluguel_m2": (70, 130)},
    "medio": {"venda_m2": (5500, 9000), "aluguel_m2": (40, 70)},
    "popular": {"venda_m2": (3200, 5500), "aluguel_m2": (22, 40)},
}

PRECO_VENDA_MIN, PRECO_VENDA_MAX = 110_000, 2_000_000
PRECO_ALUGUEL_MIN, PRECO_ALUGUEL_MAX = 1_200, 30_000

TIPOS = ["apartamento", "casa", "studio", "cobertura"]
# investimento tende a unidade compacta -- e o que de fato se aluga bem
TIPOS_INVESTIMENTO = ["apartamento", "studio"]

ADJETIVOS = [
    "reformado",
    "recem construido",
    "com acabamento de alto padrao",
    "amplo e bem iluminado",
    "pronto para morar",
    "todo planejado",
]

FEATURES = [
    "varanda gourmet",
    "churrasqueira",
    "piscina",
    "academia no condominio",
    "salao de festas",
    "portaria 24 horas",
    "elevador",
    "armarios planejados",
    "aceita pet",
    "vista panoramica da cidade",
    "quintal amplo",
    "espaco gourmet",
    "perto de estacao de metro",
    "proximo a parque",
    "facil acesso a rodovia",
    "rua arborizada",
    "condominio fechado",
    "coworking no predio",
]

FRASES_INVESTIMENTO = [
    "otimo para renda com locacao",
    "imovel com historico de ocupacao constante",
    "rentabilidade estimada acima da media da regiao",
    "ideal para quem busca retorno mensal",
    "unidade compacta, baixo custo de manutencao e alta procura para aluguel",
]


def _preco_na_faixa(bruto: float, minimo: int, maximo: int, passo: int) -> int:
    """Mantem o preco dentro da faixa pedida sem empilhar tudo no teto.

    Um clamp seco (`min(max(...))`) criaria um monte de imovel exatamente
    em R$ 2.000.000 -- artificial e feio no card. Fora da faixa, sorteia
    perto do limite em vez de grudar nele."""
    if bruto > maximo:
        bruto = random.uniform(maximo * 0.8, maximo)
    elif bruto < minimo:
        bruto = random.uniform(minimo, minimo * 1.45)
    return round(bruto / passo) * passo


def _quartos_area(tipo: str) -> tuple[int, int]:
    if tipo == "studio":
        return 1, random.randint(25, 42)
    if tipo == "apartamento":
        return random.choices([1, 2, 3, 4], weights=[15, 35, 35, 15])[0], random.randint(40, 150)
    if tipo == "casa":
        return random.choices([2, 3, 4, 5], weights=[25, 40, 25, 10])[0], random.randint(80, 260)
    if tipo == "cobertura":
        return random.choices([3, 4, 5], weights=[40, 40, 20])[0], random.randint(110, 240)
    raise ValueError(tipo)


def _gerar_descricao(tipo: str, bairro: str, cidade: str, quartos: int, area: int, investimento: bool) -> str:
    adjetivo = random.choice(ADJETIVOS)
    feats = random.sample(FEATURES, k=2)
    partes = [
        f"{tipo.capitalize()} de {quartos} quarto(s) no {bairro}, {cidade}, com {area} m2.",
        f"{adjetivo.capitalize()}, {feats[0]} e {feats[1]}.",
    ]
    if investimento:
        partes.append(random.choice(FRASES_INVESTIMENTO).capitalize() + ".")
    return " ".join(partes)


def _sortear_local() -> tuple[str, str, str, str]:
    """(cidade, zona, bairro, tier). A capital pesa mais no sorteio porque
    concentra a maior parte do estoque real -- sem isso, Sao Paulo teria o
    mesmo numero de imoveis que Sorocaba."""
    cidade = random.choices(
        list(CIDADES), weights=[40, 8, 8, 7, 7, 8, 8, 6, 4, 4], k=1
    )[0]
    zona, bairros = random.choice(CIDADES[cidade])
    bairro, tier = random.choice(bairros)
    return cidade, zona, bairro, tier


def _montar(
    *, cidade: str, zona: str, bairro: str, tier: str, tipo: str, quartos: int, area: int,
    finalidade: str, investimento: bool,
) -> dict:
    if finalidade == "aluguel":
        m2_min, m2_max = TIERS[tier]["aluguel_m2"]
        preco = _preco_na_faixa(
            area * random.uniform(m2_min, m2_max), PRECO_ALUGUEL_MIN, PRECO_ALUGUEL_MAX, 50
        )
        condominio = round(area * random.uniform(9, 22) / 10) * 10 if tipo != "casa" else None
    else:
        m2_min, m2_max = TIERS[tier]["venda_m2"]
        preco = _preco_na_faixa(
            area * random.uniform(m2_min, m2_max), PRECO_VENDA_MIN, PRECO_VENDA_MAX, 1000
        )
        condominio = round(area * random.uniform(12, 28) / 10) * 10 if tipo != "casa" else None

    return {
        "titulo": f"{tipo.capitalize()} {quartos} quarto(s) - {bairro}",
        "tipo": tipo,
        "finalidade": finalidade,
        "bairro": bairro,
        "zona": zona,
        "cidade": cidade,
        "preco": preco,
        "quartos": quartos,
        "banheiros": max(1, quartos - random.choice([0, 1])),
        "vagas": {"studio": random.randint(0, 1), "apartamento": random.randint(1, 2),
                  "casa": random.randint(2, 4), "cobertura": random.randint(2, 4)}[tipo],
        "area": area,
        "condominio": condominio,
        "rentabilidade_estimada": round(random.uniform(4.5, 7.8), 2) if investimento else None,
        "descricao": _gerar_descricao(tipo, bairro, cidade, quartos, area, investimento),
    }


def _gerar_imovel(finalidade: str, investimento: bool = False) -> dict:
    cidade, zona, bairro, tier = _sortear_local()
    tipo = random.choice(TIPOS_INVESTIMENTO if investimento else TIPOS)
    quartos, area = _quartos_area(tipo)
    return _montar(
        cidade=cidade, zona=zona, bairro=bairro, tier=tier, tipo=tipo,
        quartos=quartos, area=area, finalidade=finalidade, investimento=investimento,
    )


def _gerar_imovel_faixa(cidade: str, *, quartos: int, finalidade: str, preco_min: int, preco_max: int) -> dict:
    """Imovel com preco FORCADO num intervalo comum de demo/teste e quartos
    fixos -- garante que a combinacao mais procurada sempre tem opcao em
    TODA cidade, nao so onde o tier aleatorio do bairro calhou de cair ali.
    Sem isso, testar "2 quartos ate 600 mil em Campinas" podia voltar vazio
    por azar do sorteio, nao por falta real de estoque."""
    zona, bairros = random.choice(CIDADES[cidade])
    bairro, _tier = random.choice(bairros)
    tipo = "studio" if quartos == 1 else "apartamento"
    area = (
        random.randint(25, 42) if quartos == 1
        else random.randint(45 + (quartos - 2) * 15, 70 + (quartos - 2) * 20)
    )
    imovel = _montar(
        cidade=cidade, zona=zona, bairro=bairro, tier="medio", tipo=tipo,
        quartos=quartos, area=area, finalidade=finalidade, investimento=False,
    )
    passo = 50 if finalidade == "aluguel" else 1000
    imovel["preco"] = round(random.randint(preco_min, preco_max) / passo) * passo
    return imovel


def gerar_dataset() -> list[dict]:
    imoveis = []
    imoveis += [_gerar_imovel("venda") for _ in range(100)]
    imoveis += [_gerar_imovel("aluguel") for _ in range(35)]
    imoveis += [_gerar_imovel("venda", investimento=True) for _ in range(25)]

    # Estoque garantido por cidade nas faixas mais procuradas (ver docstring
    # de `_gerar_imovel_faixa`): 2 de venda + 2 de aluguel em cada uma.
    for cidade in CIDADES:
        imoveis.append(_gerar_imovel_faixa(cidade, quartos=1, finalidade="venda", preco_min=250_000, preco_max=450_000))
        imoveis.append(_gerar_imovel_faixa(cidade, quartos=2, finalidade="venda", preco_min=400_000, preco_max=700_000))
        imoveis.append(_gerar_imovel_faixa(cidade, quartos=1, finalidade="aluguel", preco_min=1_500, preco_max=3_000))
        imoveis.append(_gerar_imovel_faixa(cidade, quartos=2, finalidade="aluguel", preco_min=2_500, preco_max=5_000))

    random.shuffle(imoveis)
    return imoveis


def run() -> None:
    imoveis = gerar_dataset()
    print(f"gerados {len(imoveis)} imoveis -- calculando embeddings e inserindo...")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE imoveis RESTART IDENTITY CASCADE")

            for i, im in enumerate(imoveis, start=1):
                vetor = embed_text(im["descricao"])
                cur.execute(
                    """
                    INSERT INTO imoveis
                        (titulo, tipo, finalidade, bairro, zona, cidade, preco,
                         quartos, banheiros, vagas, area, condominio,
                         rentabilidade_estimada, descricao, embedding)
                    VALUES
                        (%(titulo)s, %(tipo)s, %(finalidade)s, %(bairro)s, %(zona)s,
                         %(cidade)s, %(preco)s, %(quartos)s, %(banheiros)s, %(vagas)s,
                         %(area)s, %(condominio)s, %(rentabilidade_estimada)s,
                         %(descricao)s, %(embedding)s)
                    """,
                    {**im, "embedding": vetor},
                )
                if i % 25 == 0:
                    print(f"  {i}/{len(imoveis)}")

        conn.commit()

    print("seed concluido.")


if __name__ == "__main__":
    run()
