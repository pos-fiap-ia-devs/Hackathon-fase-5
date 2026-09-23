"""Orquestrador de turno via LangGraph.

Reveste a MESMA logica de negocio ja validada em ~15 rodadas de bugs reais
achados testando no Telegram real (core/turno.py) com um grafo de estados
explicito: nao reescreve NADA da extracao/guardrails/resolucao de data,
so troca o `match`/`if` aninhado de `processar_turno` por nos e arestas
condicionais. Cada no deste grafo e uma chamada direta a uma funcao ja
testada em core/turno.py -- preservar e re-hospedar, nao redesenhar.

Por que so LangGraph, nao LangChain completo pra chamada de LLM:
core/llm.py tem ~15 correcoes de bug validadas contra o servidor Ollama
real (think:false, anyOf->type array, ordem de campo no schema, null
dentro de enum -- ver secao 5.1/5.3 do PLANO.md). O `with_structured_output`
do LangChain usa `model_json_schema()` puro do Pydantic por padrao -- o
MESMO formato que causou esses bugs. Trocar a camada de chamada ao LLM
reabriria risco ja fechado, sem necessidade: LangGraph so decide QUAL no
roda em qual ordem, nunca COMO a chamada ao LLM e feita.

Sem checkpointer persistente de proposito (mesma decisao do PLANO.md
original pra LangGraph): o Postgres ja e a fonte de verdade de
slots/mensagens/visitas -- usar um checkpointer do LangGraph junto
duplicaria estado. O grafo e reconstruido do zero a cada turno a partir
do que ja esta gravado no banco (via `lead` carregado em
core/turno.py::processar_turno), roda, persiste no banco de novo nos
proprios nos (reaproveitados de core/turno.py) e termina -- sem estado
proprio entre turnos.
"""

import logging
import threading
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agents.router import decidir_proximo, pediu_para_encerrar
from app.agents.scheduler_agent import extrair_escolha
from app.agents.summarizer import gerar_resumo
from app.core.followup import agendar_proximo
from app.core.llm import LLMError
from app.core.memory import atualizar_memoria
from app.core.prompts import INSTRUCOES_POR_ACAO
from app.core.traces import registrar_trace
from app.core.turno import (
    RESPOSTA_FALLBACK,
    _apresentar_imoveis,
    _cidades_atendidas,
    _confirmar_visita,
    _despedir,
    _encaminhar_especialista,
    _extrair_e_mesclar,
    _finalizar_qualificacao,
    _listar_regioes,
    _proxima_pergunta_agendamento,
    _resolver_escolha,
    _responder_generico_pos_desfecho,
    _tentar_reagendar,
)
from app.db import repo

logger = logging.getLogger(__name__)


class TurnState(TypedDict, total=False):
    """Estado que trafega pelo grafo. `lead` e a linha carregada do banco
    no inicio do turno (core/turno.py::processar_turno); os nos so
    persistem no Postgres via `repo`, nunca guardam estado so no grafo."""

    lead: dict
    mensagem: str
    turno: int
    slots: dict
    proxima_acao: str
    escolha: object  # LeadQualification/EscolhaVisita do agente -- tipo dinamico de proposito
    resolvido: dict
    resposta: str


# ---------------------------------------------------------------- ramo A: qualificacao (cenarios 1 e 2)

def _no_extrair_slots(state: TurnState) -> dict:
    lead = state["lead"]
    slots_atuais = repo.get_slots(lead["id"])
    # Qual pergunta o agente acabou de fazer, ANTES de mesclar a mensagem
    # nova -- fecha ambiguidade de resposta curta ("2" pra "quantos
    # quartos?"). Ver docstring de qualifier.py::extrair_slots.
    pergunta_pendente = INSTRUCOES_POR_ACAO.get(decidir_proximo(slots_atuais))
    slots_novos = _extrair_e_mesclar(
        state["mensagem"], slots_atuais, lead_id=lead["id"], turno=state["turno"],
        pergunta_pendente=pergunta_pendente,
    )
    repo.salvar_slots(lead["id"], slots_novos)
    return {"slots": slots_novos}


def _no_decidir_proximo(state: TurnState) -> dict:
    # A mensagem entra aqui (e nao em `_no_extrair_slots`, que usa
    # `decidir_proximo` so pra saber que pergunta FOI feita antes) porque o
    # atalho de "mostra o que tem" e decisao do turno atual.
    acao = decidir_proximo(state["slots"], state["mensagem"])

    # Cidade fora da area de atuacao e avisada AGORA, nao la na busca
    # (pedido do usuario). Achado testando: "Bauru" era aceita como regiao
    # e o roteiro seguia perguntando urgencia -- o cliente so descobriria
    # que a cidade nao e atendida varios turnos depois. O router e funcao
    # pura de proposito (testavel sem banco), entao a checagem que precisa
    # do Postgres mora aqui no no, nao la dentro.
    regiao = state["slots"].get("regiao")
    if regiao and acao != "listar_regioes" and not repo.regiao_existe(regiao):
        acao = "cidade_nao_atendida"

    return {"proxima_acao": acao}


def _rota_pos_decisao(state: TurnState) -> str:
    return {
        "buscar_imoveis": "apresentar_imoveis",
        "encaminhar_especialista": "encaminhar_especialista",
        "listar_regioes": "listar_regioes",
        "cidade_nao_atendida": "cidade_nao_atendida",
    }.get(state["proxima_acao"], "finalizar_qualificacao")


def _no_apresentar_imoveis(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    resposta = _apresentar_imoveis(lead_id, state["slots"], state["mensagem"], turno=state["turno"])
    return {"resposta": resposta}


def _no_listar_regioes(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    resposta = _listar_regioes(lead_id, state["slots"], turno=state["turno"])
    return {"resposta": resposta}


def _no_cidade_nao_atendida(state: TurnState) -> dict:
    slots = state["slots"]
    finalidade = "aluguel" if slots.get("intencao") == "aluguel" else "venda"
    resposta = _cidades_atendidas(
        slots["regiao"], finalidade=finalidade,
        lead_id=state["lead"]["id"], turno=state["turno"],
    )
    return {"resposta": resposta}


def _no_encaminhar_especialista(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    resposta = _encaminhar_especialista(lead_id, state["slots"], state["mensagem"], turno=state["turno"])
    return {"resposta": resposta}


def _no_finalizar_qualificacao(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    resposta = _finalizar_qualificacao(
        state["mensagem"], state["slots"], state["proxima_acao"], lead_id=lead_id, turno=state["turno"]
    )
    return {"resposta": resposta}


# ---------------------------------------------------------------- ramo B: escolha de imovel + horario da visita

def _no_extrair_escolha(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    try:
        escolha, trace = extrair_escolha(state["mensagem"])
        registrar_trace(trace, lead_id=lead_id, turno=state["turno"])
        return {"escolha": escolha}
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=state["turno"])
        return {"resposta": RESPOSTA_FALLBACK, "escolha": None}


def _rota_pos_extrair_escolha(state: TurnState) -> str:
    return "fim" if state.get("resposta") else "resolver_escolha"


def _no_resolver_escolha(state: TurnState) -> dict:
    resolvido = _resolver_escolha(state["lead"], state["mensagem"], state["escolha"])
    return {"resolvido": resolvido}


def _rota_pos_resolver_escolha(state: TurnState) -> str:
    return "confirmar_visita" if state["resolvido"]["completo"] else "proxima_pergunta_agendamento"


def _no_confirmar_visita(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    r = state["resolvido"]
    resposta = _confirmar_visita(
        lead_id,
        imovel_id=r["imovel_id"],
        horario_texto=r["horario"],
        nome=r["nome"],
        telefone=r["telefone"],
        turno=state["turno"],
    )
    return {"resposta": resposta}


def _no_proxima_pergunta_agendamento(state: TurnState) -> dict:
    lead_id = state["lead"]["id"]
    r = state["resolvido"]
    repo.salvar_escolha_visita_parcial(
        lead_id, imovel_id=r["imovel_id"], horario_texto=r["horario"], nome=r["nome"], telefone=r["telefone"]
    )
    resposta = _proxima_pergunta_agendamento(
        tem_imovel=bool(r["imovel_id"]), tem_horario=bool(r["horario"]),
        tem_nome=bool(r["nome"]), tem_telefone=bool(r["telefone"]),
    )
    return {"resposta": resposta}


# ---------------------------------------------------------------- ramo C: pos-desfecho (visita marcada / encaminhado)

def _no_checar_reagendamento(state: TurnState) -> dict:
    resposta = _tentar_reagendar(state["lead"], state["mensagem"], turno=state["turno"])
    return {"resposta": resposta} if resposta else {}


def _rota_pos_reagendamento(state: TurnState) -> str:
    return "fim" if state.get("resposta") else "responder_generico"


def _no_responder_generico_pos_desfecho(state: TurnState) -> dict:
    resposta = _responder_generico_pos_desfecho(state["lead"], state["mensagem"], turno=state["turno"])
    return {"resposta": resposta}


# ---------------------------------------------------------------- finalizacao: todo ramo passa por aqui (Etapa 6)

def _tarefas_de_fundo(lead_id: int, turno: int) -> None:
    """Memoria + resumo pro corretor. Medido com `agent_traces` reais: o
    resumo sozinho custava 4-9s POR TURNO, e a memoria mais uma chamada a
    cada 6 -- tudo isso com o cliente esperando a resposta que ja estava
    pronta. Nenhum dos dois alimenta a resposta do turno atual (o resumo e
    pro corretor ver no dashboard; a memoria so entra no PROXIMO turno),
    entao sai do caminho critico. Excecao nunca sobe: e thread solta, sem
    ninguem pra capturar, e falhar aqui nao pode derrubar o turno."""
    try:
        atualizar_memoria(lead_id, turno=turno)
    except Exception:
        logger.exception("falha atualizando memoria em segundo plano (lead_id=%s)", lead_id)
    try:
        gerar_resumo(lead_id, turno=turno)
    except Exception:
        logger.exception("falha gerando resumo em segundo plano (lead_id=%s)", lead_id)


def _no_finalizar(state: TurnState) -> dict:
    """Passos que rodam depois de QUALQUER resposta ja decidida, antes do
    fim do turno. Reune essa cauda num no so pra nao duplica-la em cada um
    dos 7 nos terminais do grafo.

    So o reagendamento do follow-up fica sincrono: e DB puro (rapido) e a
    corrida com o scanner depende dele rodar antes do turno terminar
    (core/followup.py::processar_job). Memoria e resumo vao pra thread de
    fundo -- ver `_tarefas_de_fundo`."""
    lead_id = state["lead"]["id"]
    turno = state["turno"]

    threading.Thread(
        target=_tarefas_de_fundo, args=(lead_id, turno), daemon=True,
        name=f"pos-turno-lead{lead_id}",
    ).start()

    status_atual = repo.get_lead(lead_id)["status"]
    agendar_proximo(lead_id, status_atual)

    return {}


# ---------------------------------------------------------------- entrada: roteamento por status do lead

def _no_despedir(state: TurnState) -> dict:
    return {"resposta": _despedir(state["lead"], turno=state["turno"])}


def _rota_por_status(state: TurnState) -> str:
    # Despedida vem ANTES do status: "obrigado, era so isso" encerra em
    # qualquer ramo -- qualificando, escolhendo visita ou pos-desfecho.
    # Sem isso, o ramo pos-desfecho respondia repetindo a confirmacao da
    # visita a cada "ok"/"obrigado" (achado no Telegram real).
    if pediu_para_encerrar(state["mensagem"]):
        return "despedir"
    status = state["lead"]["status"]
    if status == "aguardando_escolha":
        return "extrair_escolha"
    if status in ("visita_agendada", "encaminhado"):
        return "checar_reagendamento"
    return "extrair_slots"  # novo | qualificando


def _construir_grafo():
    g = StateGraph(TurnState)

    g.add_node("extrair_slots", _no_extrair_slots)
    g.add_node("decidir_proximo", _no_decidir_proximo)
    g.add_node("apresentar_imoveis", _no_apresentar_imoveis)
    g.add_node("encaminhar_especialista", _no_encaminhar_especialista)
    g.add_node("listar_regioes", _no_listar_regioes)
    g.add_node("cidade_nao_atendida", _no_cidade_nao_atendida)
    g.add_node("finalizar_qualificacao", _no_finalizar_qualificacao)

    g.add_node("extrair_escolha", _no_extrair_escolha)
    g.add_node("resolver_escolha", _no_resolver_escolha)
    g.add_node("confirmar_visita", _no_confirmar_visita)
    g.add_node("proxima_pergunta_agendamento", _no_proxima_pergunta_agendamento)

    g.add_node("checar_reagendamento", _no_checar_reagendamento)
    g.add_node("responder_generico", _no_responder_generico_pos_desfecho)

    g.add_node("despedir", _no_despedir)
    g.add_node("finalizar", _no_finalizar)

    g.set_conditional_entry_point(
        _rota_por_status,
        {
            "extrair_slots": "extrair_slots",
            "extrair_escolha": "extrair_escolha",
            "checar_reagendamento": "checar_reagendamento",
            "despedir": "despedir",
        },
    )

    # ramo A
    g.add_edge("extrair_slots", "decidir_proximo")
    g.add_conditional_edges(
        "decidir_proximo",
        _rota_pos_decisao,
        {
            "apresentar_imoveis": "apresentar_imoveis",
            "encaminhar_especialista": "encaminhar_especialista",
            "listar_regioes": "listar_regioes",
            "cidade_nao_atendida": "cidade_nao_atendida",
            "finalizar_qualificacao": "finalizar_qualificacao",
        },
    )
    g.add_edge("apresentar_imoveis", "finalizar")
    g.add_edge("encaminhar_especialista", "finalizar")
    g.add_edge("listar_regioes", "finalizar")
    g.add_edge("cidade_nao_atendida", "finalizar")
    g.add_edge("finalizar_qualificacao", "finalizar")

    # ramo B
    g.add_conditional_edges(
        "extrair_escolha", _rota_pos_extrair_escolha, {"fim": "finalizar", "resolver_escolha": "resolver_escolha"}
    )
    g.add_conditional_edges(
        "resolver_escolha",
        _rota_pos_resolver_escolha,
        {"confirmar_visita": "confirmar_visita", "proxima_pergunta_agendamento": "proxima_pergunta_agendamento"},
    )
    g.add_edge("confirmar_visita", "finalizar")
    g.add_edge("proxima_pergunta_agendamento", "finalizar")

    # ramo C
    g.add_conditional_edges(
        "checar_reagendamento", _rota_pos_reagendamento, {"fim": "finalizar", "responder_generico": "responder_generico"}
    )
    g.add_edge("responder_generico", "finalizar")
    g.add_edge("despedir", "finalizar")

    g.add_edge("finalizar", END)

    return g.compile()


_GRAFO = _construir_grafo()


def processar_turno_grafo(lead: dict, mensagem: str, *, turno: int) -> str:
    """Ponto de entrada usado por `core/turno.py::processar_turno` no lugar
    do `match`/`if` aninhado. Mesma assinatura de efeito (le e grava no
    Postgres via os nos), so a orquestracao muda."""
    resultado = _GRAFO.invoke({"lead": lead, "mensagem": mensagem, "turno": turno})
    return resultado["resposta"]


def exportar_mermaid() -> str:
    """Diagrama de arquitetura direto do grafo compilado -- usado em
    ARQUITETURA.md (entregavel do PLANO.md, secao 11)."""
    return _GRAFO.get_graph().draw_mermaid()
