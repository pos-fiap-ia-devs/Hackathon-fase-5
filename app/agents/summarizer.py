"""Resumo inteligente pro corretor (Requisito Funcional do PDF).

Diferente de core/memory.py (resumo rolante pro AGENTE lembrar do que ja
foi dito) -- este e o card que aparece no dashboard pro CORRETOR HUMANO
entender o lead rapido, sem ler a conversa inteira. Guardrail igual ao
resto do projeto: so cita dado que ja esta nos slots/desfecho reais,
nunca inventa.
"""

import logging
import re

from app.core.llm import LLMError, chat_text
from app.core.traces import registrar_trace
from app.db import repo

logger = logging.getLogger(__name__)

_VALORES = re.compile(r"R\$\s*([\d.,]+)")
_CAMPOS_COM_VALOR = ("faixa_preco_min", "faixa_preco_max", "ticket")


def _valores_conferem(texto: str, slots: dict) -> bool:
    """Todo valor em reais citado no resumo tem que existir nos slots.

    O resumo e a unica mensagem do sistema escrita INTEIRA pelo LLM a
    partir de dado real -- em todo o resto o fato e montado em codigo
    (secao 5.6). Como aqui nao da pra tirar o modelo do caminho sem perder
    o "resumo inteligente" que o PDF pede, a checagem e na saida: se ele
    inventar ou distorcer um numero, o resumo e descartado."""
    reais_citados = {
        int(float(v.replace(".", "").replace(",", "."))) for v in _VALORES.findall(texto)
    }
    if not reais_citados:
        return True
    permitidos = {int(slots[c]) for c in _CAMPOS_COM_VALOR if slots.get(c) is not None}
    # "R$ 400 mil" vira 400 no regex -- aceita tambem o valor em milhares.
    permitidos |= {v // 1000 for v in permitidos}
    return reais_citados <= permitidos


def _contexto_desfecho(lead: dict) -> str:
    if lead["status"] == "visita_agendada":
        visita = repo.get_ultima_visita(lead["id"])
        if visita:
            imovel = repo.get_imovel(visita["imovel_id"])
            titulo = imovel["titulo"] if imovel else "imóvel"
            bairro = imovel["bairro"] if imovel else ""
            return (
                f"Visita agendada: {titulo} ({bairro}), corretor(a) {visita['corretor']}, "
                f'preferência de horário "{visita.get("horario_solicitado")}".'
            )
    if lead["status"] == "encaminhado":
        enc = repo.get_ultimo_encaminhamento(lead["id"])
        if enc:
            return f"Encaminhado para {enc['especialista']}: {enc['motivo']}."
    return ""


# Desfechos: aqui o card do corretor precisa estar completo e final, entao
# o resumo sempre regenera, independente do turno.
_STATUS_DESFECHO = ("visita_agendada", "encaminhado")


def gerar_resumo(lead_id: int, *, turno: int) -> str | None:
    """Gera e persiste o resumo pro corretor. Pula quando ainda não há
    nada de útil pra resumir (lead recém chegou, intenção indefinida) --
    barato: so 1 leitura de banco, sem chamada de LLM nesse caso.

    Roda a cada 2 turnos, não a cada um: medido em `agent_traces`, cada
    resumo custa 4-9s de GPU, e mesmo rodando em segundo plano
    (core/graph.py::_tarefas_de_fundo) ele disputa o Ollama com o turno
    SEGUINTE do cliente -- turnos com resumo concorrente subiram de ~7s
    pra ~12s na extração. O corretor não precisa do card atualizado a cada
    mensagem; precisa dele atualizado e completo quando for ligar."""
    slots = repo.get_slots(lead_id)
    if not slots.get("intencao") or slots["intencao"] == "indefinido":
        return None

    lead = repo.get_lead(lead_id)
    if turno % 2 != 0 and lead["status"] not in _STATUS_DESFECHO:
        return None

    desfecho = _contexto_desfecho(lead)

    system = (
        "Você escreve resumos curtos pra corretores de imóveis lerem antes de "
        "ligar pro cliente. 2 a 4 frases, direto ao ponto: o que o cliente "
        "quer, os dados coletados, urgência, e o que já foi feito (visita "
        "marcada, encaminhamento). Nunca invente dado que não esteja nos "
        "dados abaixo."
    )
    user = (
        f"Dados coletados do lead: {slots}\n"
        f"Score: {lead.get('score')}\n"
        f"Status: {lead['status']}\n" + (f"Desfecho: {desfecho}\n" if desfecho else "")
    )

    try:
        texto, trace = chat_text(agente="summarizer", system=system, user=user, temperature=0.2)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        logger.warning("resumo pro corretor falhou: %s", e)
        return None

    if not _valores_conferem(texto, slots):
        logger.warning("resumo citou valor que nao esta nos slots, descartado: %r", texto)
        return None

    repo.salvar_resumo(lead_id, texto)
    return texto
