"""O agente nunca pode repetir a mesma mensagem indefinidamente.

Achado no Telegram real e depois generalizado por uma sonda: o roteador é
função pura dos slots, então cliente que não responde o que foi perguntado
não muda slot nenhum, a decisão não muda, e a mesma frase sai pra sempre.
Eram **6 de 6** cenários de cliente pouco cooperativo travando.

O mecanismo tem duas metades, e as duas são necessárias:

- `repo.registrar_acao` conta decisões **consecutivas** iguais;
- `repo.dispensar_campo` tira a pergunta do roteiro na 3ª vez.

Só a primeira trocava um laço por outro: a pergunta parava, mas o campo
continuava faltando e a mesma escalada disparava pra sempre.

Critério: o cliente sente laço ao **receber a mesma mensagem de novo**.
Slot parado não é laço — numa conversa saudável o agente reformula, oferece
alternativa ou escala, e o slot pode continuar vazio.
"""

import uuid

import pytest

from tests.conftest import precisa_de_ollama, precisa_de_seed

# Duas iguais seguidas é tolerável (o cliente pode ter se distraído);
# três é teimosia do sistema.
MAX_IGUAIS_SEGUIDAS = 3


def _maior_sequencia_igual(respostas: list[str]) -> int:
    pior = atual = 1
    for i in range(1, len(respostas)):
        atual = atual + 1 if respostas[i] == respostas[i - 1] else 1
        pior = max(pior, atual)
    return pior


def _conversar(slots: dict, mensagens: list[str]) -> list[str]:
    from app.core.turno import processar_turno
    from app.db import repo

    chat_id = f"tg:{uuid.uuid4().int % 10**9}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="telegram")
    if slots:
        repo.salvar_slots(lead["id"], slots)
    return [
        str(processar_turno(chat_id=chat_id, canal="telegram", mensagem=m, origem="texto"))
        for m in mensagens
    ]


CENARIOS = [
    pytest.param(
        {"intencao": "compra", "faixa_preco_max": 400_000, "quartos": 2},
        ["quero em Bauru", "sim", "sim", "pode ser", "ok"],
        id="regiao-fora-da-area",
    ),
    pytest.param(
        {"intencao": "compra", "faixa_preco_max": 50_000, "quartos": 4,
         "regiao": "Moema", "urgencia": "alta"},
        ["e agora?", "sim", "pode ser", "ok", "aham"],
        id="busca-sempre-vazia",
    ),
    pytest.param(
        {"intencao": "compra"},
        ["sim", "sim", "sim", "sim", "sim"],
        id="cliente-so-diz-sim",
    ),
    pytest.param(
        {"intencao": "compra", "faixa_preco_max": 600_000, "quartos": 2,
         "regiao": "zona sul", "urgencia": "alta"},
        ["quero ver", "sim", "esse", "aquele", "o bom"],
        id="escolha-nunca-resolvida",
    ),
    pytest.param({}, ["oi", "tudo bem?", "e ai", "hmm", "sei la"], id="intencao-nunca-dita"),
    pytest.param(
        {"intencao": "investimento", "perfil_investidor": "renda"},
        ["sim", "nao sei", "depende", "ainda nao sei", "sei la"],
        id="investimento-sem-ticket",
    ),
]


@precisa_de_seed
@precisa_de_ollama
@pytest.mark.parametrize(("slots", "mensagens"), CENARIOS)
def test_cliente_nao_cooperativo_nao_trava_o_agente(slots, mensagens):
    respostas = _conversar(slots, mensagens)
    maior = _maior_sequencia_igual(respostas)
    assert maior < MAX_IGUAIS_SEGUIDAS, (
        f"mesma resposta {maior}x seguidas:\n  " + "\n  ".join(r.splitlines()[0][:70] for r in respostas)
    )


# ---------------------------------------------------------------- o mecanismo

@precisa_de_seed
def test_contador_zera_quando_a_decisao_muda():
    """É a insistência CONSECUTIVA que importa, não o total histórico."""
    from app.db import repo

    lead = repo.get_or_create_lead(chat_id=f"tg:{uuid.uuid4().int % 10**9}", canal="telegram")
    assert repo.registrar_acao(lead["id"], "perguntar_preco") == 1
    assert repo.registrar_acao(lead["id"], "perguntar_preco") == 2
    assert repo.registrar_acao(lead["id"], "perguntar_quartos") == 1
    assert repo.registrar_acao(lead["id"], "perguntar_preco") == 1


@precisa_de_seed
def test_campo_dispensado_sai_do_roteiro():
    from app.agents.router import decidir_proximo

    slots = {"intencao": "compra"}
    assert decidir_proximo(slots) == "perguntar_preco"
    assert decidir_proximo(slots, None, ("perguntar_preco",)) == "perguntar_quartos"


@precisa_de_seed
def test_dispensar_o_mesmo_campo_duas_vezes_nao_duplica():
    from app.db import repo

    lead = repo.get_or_create_lead(chat_id=f"tg:{uuid.uuid4().int % 10**9}", canal="telegram")
    repo.dispensar_campo(lead["id"], "perguntar_preco")
    repo.dispensar_campo(lead["id"], "perguntar_preco")
    assert repo.get_campos_dispensados(lead["id"]) == ("perguntar_preco",)
