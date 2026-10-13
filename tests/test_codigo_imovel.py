"""Código público do imóvel (MOE-0001).

Por que ele existe: o número do card é **posicional**. "Quero o 2" só faz
sentido dentro da última lista apresentada — se o cliente voltar amanhã,
depois de outra busca, o 2 é outro imóvel. O código não muda, aparece na
legenda da foto, no card e na confirmação da visita.
"""

import pytest

from app.core.turno import _codigo_na_mensagem
from tests.conftest import precisa_de_seed


# ---------------------------------------------------------------- extração da fala

@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("quero ver o MOE-0001", "MOE-0001"),
        ("me mostra o vma 0004", "VMA-0004"),          # caixa baixa, sem hífen
        ("CAM0006", "CAM-0006"),                        # colado
        ("gostei do Cód. VMA-0001, pode agendar", "VMA-0001"),
    ],
)
def test_reconhece_codigo_como_o_cliente_digita(mensagem, esperado):
    assert _codigo_na_mensagem(mensagem) == esperado


@pytest.mark.parametrize(
    "mensagem",
    [
        "quero o 2",
        "o segundo mesmo",
        "tem algo em Moema?",
        "até 600 mil",
        "meu telefone é 11 98888-7777",   # dígitos, mas não é código
        "",
    ],
)
def test_nao_inventa_codigo(mensagem):
    assert _codigo_na_mensagem(mensagem) is None


def test_mensagem_vazia_nao_estoura():
    assert _codigo_na_mensagem(None) is None


# ---------------------------------------------------------------- modo demonstração

def test_provedor_de_regras_extrai_o_codigo():
    from app.agents.scheduler_agent import EscolhaVisita
    from app.core import llm_demo

    dados, _ = llm_demo.chat_structured(
        agente="scheduler", system="", user='Mensagem do cliente: "quero o MOE-0001"',
        schema=EscolhaVisita,
    )
    assert dados.codigo_escolhido == "MOE-0001"


def test_codigo_ganha_do_numero_solto_que_ele_contem():
    """"MOE-0001" termina em 1: sem tratar o código primeiro, o extrator
    de número solto o leria como "quero a opção 1"."""
    from app.agents.scheduler_agent import EscolhaVisita
    from app.core import llm_demo

    dados, _ = llm_demo.chat_structured(
        agente="scheduler", system="", user='Mensagem do cliente: "quero o MOE-0001"',
        schema=EscolhaVisita,
    )
    assert dados.numero_escolhido is None


# ---------------------------------------------------------------- banco

pytestmark_banco = precisa_de_seed


@precisa_de_seed
def test_todo_imovel_tem_codigo_unico():
    from app.db import repo

    with repo.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS total, count(DISTINCT codigo) AS distintos,"
            " count(*) FILTER (WHERE codigo IS NULL) AS nulos FROM imoveis"
        )
        linha = cur.fetchone()
    assert linha["nulos"] == 0
    assert linha["distintos"] == linha["total"]


@precisa_de_seed
def test_busca_pelo_codigo_ignora_a_caixa():
    """O cliente digita como quiser; a coluna guarda em maiúscula."""
    from app.db import repo

    with repo.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT codigo FROM imoveis LIMIT 1")
        codigo = cur.fetchone()["codigo"]

    assert repo.get_imovel_por_codigo(codigo.lower())["codigo"] == codigo
    assert repo.get_imovel_por_codigo(codigo)["codigo"] == codigo


@precisa_de_seed
def test_codigo_inexistente_devolve_none():
    from app.db import repo

    assert repo.get_imovel_por_codigo("ZZZ-9999") is None


@precisa_de_seed
def test_card_e_legenda_mostram_o_codigo():
    from app.agents.search import buscar_imoveis
    from app.core.turno import _formatar_cards, _galeria_imoveis

    imoveis = buscar_imoveis(finalidade="venda", limite=2)
    assert imoveis, "seed sem imóvel de venda"

    cards = _formatar_cards(imoveis)
    for im in imoveis:
        assert f"Cód. {im['codigo']}" in cards

    for item, im in zip(_galeria_imoveis(imoveis), imoveis, strict=False):
        assert im["codigo"] in item["legenda"]
        # A legenda é o que aparece colado na foto no Telegram: sem quartos
        # e área ela era mais pobre que o card de texto. Quarto só quando
        # o imóvel tem: os mais baratos à venda são terrenos, e "0 quartos"
        # numa legenda de terreno é dado errado.
        assert "m²" in item["legenda"]
        if im["quartos"]:
            assert "quarto" in item["legenda"]
        else:
            assert "quarto" not in item["legenda"]
