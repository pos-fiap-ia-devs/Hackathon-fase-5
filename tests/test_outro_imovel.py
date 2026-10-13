"""Cliente pede "outro imóvel": a busca recomeça e a Bia pergunta o tipo.

Achado em uso real, antes deste tratamento:
  * logo depois das opções, a Bia ignorava o pedido e insistia pra o
    cliente escolher entre as MESMAS 3;
  * com a visita já marcada, prometia "vamos te enviar as opções
    disponíveis assim que possível" -- um envio que nada no sistema faz.

Agora o pedido é detectado em código (como a despedida), vale em qualquer
ponto da conversa e a resposta é montada sem LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.router import pediu_outro_imovel
from tests.conftest import precisa_de_banco

# ---------------------------------------------------------------- detecção


@pytest.mark.parametrize("mensagem", [
    "quero ver outro imóvel",
    "outro imóvel",
    "tem outras opções?",
    "quero ver outra casa",
    "quero ver outro apartamento em Barueri",
    "podemos fazer uma nova busca?",
])
def test_reconhece_pedido_de_outro_imovel(mensagem):
    assert pediu_outro_imovel(mensagem)


@pytest.mark.parametrize("mensagem", [
    # Remarcar a visita NÃO é trocar de imóvel: limpar a busca de quem só
    # queria mudar a data seria um defeito bem pior que não reconhecer.
    "quero outro horário",
    "pode ser outro dia?",
    "tem outra opção de horário?",
    "quero remarcar",
    "gostei do 2",
    "obrigado",
])
def test_nao_confunde_com_remarcar_nem_escolher(mensagem):
    assert not pediu_outro_imovel(mensagem)


# ---------------------------------------------------------------- o recomeço


def _lead_com_busca_feita(repo, *, com_visita: bool):
    lead = repo.get_or_create_lead(chat_id=f"cli:teste_outro_{uuid.uuid4().hex[:8]}", canal="cli")
    repo.salvar_slots(lead["id"], {
        "intencao": "aluguel", "faixa_preco_max": 10_000, "quartos": 3,
        "regiao": "Santana de Parnaíba", "urgencia": "media", "tipo_imovel": "casa",
    })
    repo.atualizar_status(lead["id"], "aguardando_escolha")
    if com_visita:
        repo.criar_visita(
            lead["id"], imovel_id=None, corretor="Marina",
            data_hora=datetime.now(timezone.utc) + timedelta(days=3),
            horario_solicitado="quinta à tarde",
        )
        repo.atualizar_status(lead["id"], "visita_agendada")
    return lead


@precisa_de_banco
def test_recomeco_limpa_o_imovel_e_mantem_o_cliente():
    from app.db import repo

    lead = _lead_com_busca_feita(repo, com_visita=False)
    repo.recomecar_busca(lead["id"], None)

    slots = repo.get_slots(lead["id"])
    assert slots["intencao"] == "aluguel"          # quem o cliente é fica
    for campo in ("faixa_preco_max", "quartos", "regiao", "urgencia", "tipo_imovel"):
        assert slots.get(campo) is None, campo     # o que ele procurava sai
    assert repo.get_lead(lead["id"])["status"] == "qualificando"


@precisa_de_banco
def test_visita_marcada_nao_e_desmarcada():
    from app.core.turno import _recomecar_busca
    from app.db import repo

    lead = _lead_com_busca_feita(repo, com_visita=True)
    resposta = _recomecar_busca(repo.get_lead(lead["id"]), "quero ver outra casa")

    assert repo.get_visita_ativa(lead["id"]) is not None
    # O cliente precisa saber que não perdeu o horário.
    assert "continua de pé" in resposta
    # Tipo já dito na frase: não se pergunta de novo, segue pro preço.
    assert repo.get_slots(lead["id"])["tipo_imovel"] == "casa"
    assert "faixa de preço" in resposta


@precisa_de_banco
def test_sem_tipo_na_frase_pergunta_o_tipo():
    from app.core.turno import _recomecar_busca
    from app.db import repo

    lead = _lead_com_busca_feita(repo, com_visita=False)
    resposta = _recomecar_busca(repo.get_lead(lead["id"]), "quero ver outro imóvel")

    assert "Que tipo" in resposta
    assert "continua de pé" not in resposta        # não há visita pra citar
    # Nenhuma promessa de envio futuro -- o defeito original.
    assert "assim que possível" not in resposta
    assert "vamos te enviar" not in resposta.lower()
