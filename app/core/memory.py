"""Memoria conversacional -- resumo rolante (secao 5.5 do PLANO.md).

Diferencial do PDF, mas SEM RAG -- RAG e um diferencial separado, ja
coberto pela busca de imoveis (agents/search.py, secao 5.4). Conversa de
qualificacao de lead tem 5-10 turnos ate fechar (visita/encaminhamento);
o historico inteiro cabe em 8K de contexto sem precisar de recuperacao
vetorial seletiva. Resumo rolante resolve o problema real (nao deixar o
contexto crescer sem limite numa conversa longa) sem a complexidade extra
de embutir cada turno e buscar por similaridade.

Estrategia: ultimos 6 turnos literais (repo.get_historico) + um resumo em
texto de tudo que veio antes, regerado a cada 6 turnos novos. Isso mantem
o contexto do LLM abaixo de 8K permanentemente, independente de quantas
mensagens o lead ja mandou.
"""

import logging

from app.core.llm import LLMError, chat_text
from app.core.traces import registrar_trace
from app.db import repo

logger = logging.getLogger(__name__)

JANELA_TURNOS_LITERAIS = 6  # turnos recentes mantidos literais, sem resumir
REGERAR_A_CADA = 6  # turnos novos desde o ultimo resumo pra regerar


def contexto_conversa(lead_id: int) -> str:
    """Texto pronto pra colar no prompt: resumo do que veio antes (se
    houver) + as ultimas mensagens literais. Chamado pelos agentes de
    resposta -- nunca gera resumo aqui, so le o que ja esta gravado
    (gerar e responsabilidade de `atualizar_memoria`, chamado uma vez por
    turno no orquestrador)."""
    memoria = repo.get_memoria(lead_id)
    historico = repo.get_historico(lead_id, limite=JANELA_TURNOS_LITERAIS)

    partes = []
    if memoria and memoria.get("resumo"):
        partes.append(f"Resumo da conversa até aqui: {memoria['resumo']}")

    if historico:
        linhas = [f"{'Cliente' if m['papel'] == 'user' else 'Você'}: {m['conteudo']}" for m in historico]
        partes.append("Últimas mensagens:\n" + "\n".join(linhas))

    return "\n\n".join(partes)


def atualizar_memoria(lead_id: int, *, turno: int) -> None:
    """Regera o resumo a cada `REGERAR_A_CADA` turnos, comprimindo tudo que
    ficou fora da janela literal. Chamado uma vez por turno pelo
    orquestrador (core/graph.py), depois de persistir a mensagem do
    turno -- barato quando nao regenera (so leitura), uma chamada de LLM
    extra quando regenera."""
    memoria = repo.get_memoria(lead_id)
    turnos_resumidos_ate_agora = memoria.get("turnos_resumidos", 0) if memoria else 0

    if turno - turnos_resumidos_ate_agora < REGERAR_A_CADA:
        return  # ainda cabe na janela literal, nao precisa resumir

    # tudo ANTES da janela literal atual entra no resumo -- pega o
    # historico inteiro e descarta os ultimos JANELA_TURNOS_LITERAIS na
    # hora de montar o prompt (esses continuam literais via get_historico).
    historico_completo = repo.get_historico(lead_id, limite=1000)
    a_resumir = historico_completo[:-JANELA_TURNOS_LITERAIS] if len(historico_completo) > JANELA_TURNOS_LITERAIS else []
    if not a_resumir:
        return

    resumo_anterior = memoria.get("resumo", "") if memoria else ""
    transcricao = "\n".join(f"{'Cliente' if m['papel'] == 'user' else 'Agente'}: {m['conteudo']}" for m in a_resumir)

    system = (
        "Você resume conversas de qualificação de leads imobiliários para o "
        "próprio agente reler depois. Seja objetivo: fatos ditos pelo cliente "
        "(intenção, região, preço, urgência, nome), decisões tomadas. Sem "
        "floreio, sem repetir o que não foi dito. No máximo 4 frases."
    )
    user = (
        (f"Resumo anterior: {resumo_anterior}\n\n" if resumo_anterior else "")
        + f"Trechos novos da conversa:\n{transcricao}\n\nAtualize o resumo incluindo esses trechos."
    )

    try:
        novo_resumo, trace = chat_text(agente="memoria", system=system, user=user, temperature=0.2)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        logger.warning("resumo de memoria falhou, mantendo o anterior: %s", e)
        return

    repo.salvar_memoria(lead_id, resumo=novo_resumo, turnos_resumidos=turno)
