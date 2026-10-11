"""Fotos junto com cada imovel apresentado (pedido do usuario).

Funcoes puras -- nao precisam de Postgres nem de Ollama, ao contrario do
resto da suite: a montagem da galeria e so formatacao em codigo, e e
exatamente por ser codigo (e nao prompt) que preco e bairro na legenda
nao podem sair errados.
"""

from app.core.turno import RespostaTurno, _galeria_imoveis

FOTOS = ["https://fotos/1.jpg", "https://fotos/2.jpg", "https://fotos/3.jpg"]


def _imovel(**kw) -> dict:
    base = {
        "id": 7, "codigo": "MOE-0007", "titulo": "Apartamento reformado",
        "bairro": "Moema", "preco": 650_000, "finalidade": "venda",
        "quartos": 2, "area": 72, "fotos": FOTOS,
    }
    return {**base, **kw}


def test_galeria_segue_a_numeracao_dos_cards():
    galeria = _galeria_imoveis([_imovel(id=1), _imovel(id=2), _imovel(id=3)])

    assert [g["numero"] for g in galeria] == [1, 2, 3]
    assert [g["imovel_id"] for g in galeria] == [1, 2, 3]
    assert galeria[1]["legenda"].startswith("2. ")


def test_legenda_traz_preco_formatado_em_codigo():
    """A legenda e o que aparece colado na foto no Telegram -- precisa ser
    tao informativa quanto o card de texto, nao menos."""
    legenda = _galeria_imoveis([_imovel()])[0]["legenda"]
    assert legenda == (
        "1. Apartamento reformado — Moema\n"
        "R$ 650.000 · 2 quarto(s) · 72m²\n"
        "Cód. MOE-0007"
    )


def test_legenda_sem_codigo_nao_quebra():
    """Imovel de base antiga, antes da coluna `codigo` existir."""
    legenda = _galeria_imoveis([_imovel(codigo=None)])[0]["legenda"]
    assert "Cód." not in legenda
    assert "R$ 650.000" in legenda


def test_aluguel_marca_por_mes():
    galeria = _galeria_imoveis([_imovel(finalidade="aluguel", preco=3_200)])
    assert "R$ 3.200/mês" in galeria[0]["legenda"]


def test_imovel_sem_foto_fica_de_fora_mas_nao_desloca_a_numeracao():
    """Sem foto no banco o card continua no texto -- so nao tem imagem.
    O numero do imovel seguinte NAO pode mudar por causa disso, senao
    'quero o 2' passa a apontar pro imovel errado."""
    galeria = _galeria_imoveis([_imovel(id=1, fotos=[]), _imovel(id=2), _imovel(id=3, fotos=None)])

    assert [g["numero"] for g in galeria] == [2]
    assert galeria[0]["imovel_id"] == 2


def test_resposta_turno_e_string_para_quem_so_quer_texto():
    """Contrato que mantem os canais e a suite antigos funcionando."""
    r = RespostaTurno("Encontrei essas opções:", [{"imovel_id": 1, "numero": 1, "legenda": "x", "fotos": FOTOS}])

    assert isinstance(r, str)
    assert r == "Encontrei essas opções:"
    assert "opções" in r
    assert r.galeria[0]["fotos"] == FOTOS


def test_resposta_turno_sem_galeria():
    assert RespostaTurno("oi").galeria == []
