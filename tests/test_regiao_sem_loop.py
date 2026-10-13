"""Região que não casa com a base não pode travar o atendimento.

Achado no Telegram real: o cliente respondeu "capital" e o agente repetiu
a mesma pergunta cinco vezes seguidas, depois passou a repetir *"ainda não
atendemos na capital"* — numa base com 200 imóveis em São Paulo.

Dois defeitos somados, testados separadamente aqui:

1. **Match em um sentido só.** `regiao_existe` fazia
   `cidade ILIKE '<termo>'` com curinga em volta do termo. O extrator
   devolve a região com qualificador ("São Paulo (Capital)"), e o valor
   guardado no banco é *substring* do que o cliente disse, não o
   contrário — então nunca casava.

2. **Beco sem saída.** O nó `cidade_nao_atendida` avisava e **não limpava**
   `slots.regiao`. O valor ruim ficava gravado, e toda mensagem seguinte
   voltava a cair no mesmo nó. Mesmo com uma cidade legitimamente fora da
   área (Bauru), repetir o mesmo aviso para sempre é um beco sem saída.
"""

import uuid

import pytest

from tests.conftest import precisa_de_ollama, precisa_de_seed


# ---------------------------------------------------------------- match da região

@precisa_de_seed
@pytest.mark.parametrize(
    "termo",
    [
        "São Paulo (Capital)",   # o valor exato que travou o lead
        "Sao Paulo capital",
        "Sao Paulo",
        "Barueri",
        "Aldeia da Serra",
        "zona sul",
    ],
)
def test_regiao_atendida_e_reconhecida(termo):
    from app.db import repo

    assert repo.regiao_existe(termo)


@precisa_de_seed
@pytest.mark.parametrize("termo", ["Bauru", "Rio de Janeiro", "Piracicaba", "Belo Horizonte", "Campinas"])
def test_regiao_fora_da_area_continua_recusada(termo):
    """O conserto do match não pode passar a aceitar qualquer coisa."""
    from app.db import repo

    assert not repo.regiao_existe(termo)


@precisa_de_seed
@pytest.mark.parametrize("termo", ["São Paulo (Capital)", "Barueri", "Aldeia da Serra", "zona sul"])
def test_validacao_e_busca_concordam(termo):
    """O defeito mais traiçoeiro desta família: `regiao_existe` dizia que
    sim e a busca voltava vazia, então o agente respondia "não encontrei
    nada" para uma região que ele mesmo acabara de aceitar. Hoje as duas
    usam `repo.sql_regiao_casa`, uma definição só."""
    from app.agents.search import buscar_imoveis
    from app.db import repo

    assert repo.regiao_existe(termo)
    assert buscar_imoveis(finalidade="venda", zona=termo, limite=3)


# ---------------------------------------------------------------- o laço

@precisa_de_seed
@precisa_de_ollama
def test_cidade_fora_da_area_nao_prende_o_lead():
    """Avisa uma vez, limpa a região, e a próxima resposta do cliente vale.

    Sem o `regiao: None`, "quero em Bauru" condenava o lead a ouvir o
    mesmo aviso em toda mensagem seguinte, sem nenhuma forma de corrigir."""
    from app.core.turno import processar_turno
    from app.db import repo

    chat_id = f"tg:{uuid.uuid4().int % 10**9}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="telegram")
    repo.salvar_slots(lead["id"], {"intencao": "compra", "faixa_preco_max": 400_000, "quartos": 2})

    processar_turno(chat_id=chat_id, canal="telegram", mensagem="quero em Bauru", origem="texto")
    # O slot tem que sair limpo, senao o proximo turno cai no mesmo no.
    assert repo.get_slots(lead["id"]).get("regiao") is None

    processar_turno(chat_id=chat_id, canal="telegram", mensagem="então Barueri", origem="texto")
    assert repo.get_slots(lead["id"]).get("regiao") is not None


@precisa_de_seed
@precisa_de_ollama
def test_capital_avanca_o_roteiro_em_vez_de_repetir():
    """A regressão do print: depois de informar a região, o próximo passo
    é urgência — não a mesma pergunta de novo."""
    from app.agents.router import decidir_proximo
    from app.core.turno import processar_turno
    from app.db import repo

    chat_id = f"tg:{uuid.uuid4().int % 10**9}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="telegram")
    repo.salvar_slots(lead["id"], {"intencao": "compra", "faixa_preco_max": 400_000, "quartos": 2})

    processar_turno(chat_id=chat_id, canal="telegram", mensagem="capital", origem="texto")
    slots = repo.get_slots(lead["id"])

    assert slots.get("regiao") is not None
    assert decidir_proximo(slots) != "perguntar_regiao"


@precisa_de_seed
@precisa_de_ollama
def test_nao_repete_a_mesma_resposta_na_sequencia_do_print():
    """Resposta idêntica duas vezes seguidas é o sintoma que o cliente vê."""
    from app.core.turno import processar_turno
    from app.db import repo

    chat_id = f"tg:{uuid.uuid4().int % 10**9}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="telegram")
    repo.salvar_slots(lead["id"], {"intencao": "compra", "faixa_preco_max": 200_000})

    respostas = [
        str(processar_turno(chat_id=chat_id, canal="telegram", mensagem=m, origem="texto"))
        for m in ("oi", "2", "capital", "sim")
    ]
    assert len(set(respostas)) == len(respostas)
