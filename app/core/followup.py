"""Follow-up automático (cenário 3 do PDF, seção 6 do PLANO.md).

Fluxo:
1. A cada turno, `agendar_proximo` cancela follow-ups pendentes (o lead
   está ativo agora) e agenda um novo se o status continuar "em aberto"
   (novo, qualificando, aguardando_escolha -- não visita_agendada/
   encaminhado, que já fecharam o ciclo).
2. `channels/telegram.py` roda um job periódico (JobQueue do
   python-telegram-bot, usa APScheduler por baixo) que varre jobs
   vencidos via `repo.get_followups_vencidos` e chama `processar_job`
   pra cada um -- gera a mensagem citando o contexto real da conversa
   (não um lembrete genérico) e envia.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.llm import LLMError, chat_text
from app.core.memory import contexto_conversa
from app.core.traces import registrar_trace
from app.core.turno import _persona_com_data
from app.db import repo

logger = logging.getLogger(__name__)

STATUS_FOLLOWABLE = ("novo", "qualificando", "aguardando_escolha")


def agendar_proximo(lead_id: int, status: str) -> None:
    """Chamado a cada turno -- reseta o timer de reengajamento. Se o
    status já fechou o ciclo, não agenda nada (e cancela o que sobrou).

    `datetime.now(UTC)`, nunca `datetime.now()` sem timezone: achado real
    -- a coluna `executar_em` é `timestamptz`, e o Postgres deste projeto
    roda com `TimeZone=Etc/UTC`. Um datetime naive (hora LOCAL da máquina,
    ex: 17:35 em UTC-3) gravado numa `timestamptz` é interpretado como se
    já estivesse em UTC -- ou seja, grava "17:35 UTC" quando o UTC real é
    "20:35". O job nasce 3h no passado e o scanner (a cada
    `FOLLOWUP_SCAN_S`) o pega como vencido na primeira varredura seguinte,
    ignorando o intervalo configurado por completo. Sintoma visto ao vivo:
    follow-up disparando a cada ~30s em vez de 90/180/300s do DEMO_MODE."""
    repo.cancelar_followups_pendentes(lead_id)
    if status not in STATUS_FOLLOWABLE:
        return
    settings = get_settings()
    intervalo_s = settings.followup_intervalos_s[0]
    repo.criar_followup_job(lead_id, tentativa=1, executar_em=datetime.now(UTC) + timedelta(seconds=intervalo_s))


def gerar_mensagem_reengajamento(lead_id: int, *, turno: int) -> str | None:
    """Mensagem citando o contexto real -- é o que prova "manter contexto
    da conversa" do cenário 3 do PDF, não um "ainda está aí?" genérico."""
    contexto = contexto_conversa(lead_id)
    if not contexto:
        contexto = "O cliente iniciou a conversa mas ainda não deu detalhes."

    # reusa PERSONA+REGRAS (secao 5.3) -- um system prompt escrito do zero
    # aqui, sem herdar as regras ja endurecidas (ex: "nunca chame o cliente
    # de Bia"), reintroduziu exatamente esse erro em teste real.
    system = _persona_com_data(turno)
    user = (
        f"Contexto da conversa:\n{contexto}\n\n"
        "O cliente parou de responder. Escreva uma mensagem curta e cordial "
        "retomando o contato, citando algo específico que ele disse antes -- "
        'nunca um "ainda está aí?" genérico. No máximo 2 frases.'
    )

    try:
        texto, trace = chat_text(agente="followup", system=system, user=user, temperature=0.4)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
        return texto
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        logger.warning("geração de mensagem de follow-up falhou: %s", e)
        return None


def processar_job(job: dict) -> str | None:
    """Processa um follow_up_job vencido -- gera a mensagem, persiste,
    agenda a próxima tentativa ou encerra o lead. Devolve o texto a
    enviar, ou None se não deve enviar nada (lead já saiu do status
    followable entre o agendamento e a execução, ou a geração falhou)."""
    lead_id = job["lead_id"]
    job_id = job["job_id"]

    lead = repo.get_lead(lead_id)
    if lead["status"] not in STATUS_FOLLOWABLE:
        repo.marcar_followup_cancelado(job_id)
        return None

    turnos_no_inicio = repo.contar_turnos(lead_id)
    turno = turnos_no_inicio + 1
    mensagem = gerar_mensagem_reengajamento(lead_id, turno=turno)
    if not mensagem:
        repo.marcar_followup_cancelado(job_id)
        return None

    # Corrida real, vista ao vivo no Telegram: o cliente respondeu um turno
    # de verdade ENQUANTO este follow-up gerava (1 chamada de LLM, ~5-8s --
    # mais rapido que um turno completo, que faz 2). `agendar_proximo` do
    # turno real so cancela job pendente no FIM do turno, tarde demais pra
    # segurar este envio. Reconfere aqui, depois da geracao (mais lenta),
    # antes de mandar. Contagem de turnos, nao timestamp: comparar
    # `datetime.now()` do processo Python contra `now()` do Postgres tem
    # risco de clock skew entre os dois relogios (medido: ~1ms de diferenca
    # numa chamada sequencial sem nada no meio -- pequeno, mas mostra que
    # os relogios nao sao a mesma fonte de verdade). `contar_turnos` e
    # 100% server-side e monotonico, sem esse risco.
    if repo.contar_turnos(lead_id) != turnos_no_inicio:
        repo.marcar_followup_cancelado(job_id)
        return None

    repo.salvar_mensagem(lead_id, papel="assistant", conteudo=mensagem, origem="followup")
    repo.marcar_followup_enviado(job_id)

    settings = get_settings()
    tentativa_atual = job["tentativa"]
    if tentativa_atual < settings.followup_max_tentativas:
        proximo_intervalo = settings.followup_intervalos_s[tentativa_atual]
        repo.criar_followup_job(
            lead_id, tentativa=tentativa_atual + 1,
            executar_em=datetime.now(UTC) + timedelta(seconds=proximo_intervalo),
        )
    else:
        repo.atualizar_status(lead_id, "perdido")

    return mensagem


# ---------------------------------------------------------------- fechamento por inatividade (pedido do usuario)
#
# Separado do follow-up acima de proposito: o follow-up TENTA reengajar
# citando contexto (ate 3x); isso aqui e um corte definitivo por SILENCIO
# TOTAL depois de `INATIVIDADE_FECHAMENTO_MIN` -- roda em paralelo, nao
# troca um pelo outro. Mensagem fixa, nao gerada pelo LLM: e um aviso
# formal de encerramento, nao uma reacao a algo que o cliente disse --
# nao ha nada aqui que se beneficie de variacao, e um aviso ruim de
# entender se parafraseado tende a soar evasivo.

MENSAGEM_ENCERRAMENTO_INATIVIDADE = (
    "Por motivo de inatividade, vamos encerrar esse atendimento por aqui. "
    "Quando quiser continuar, é só me chamar de novo! 👋"
)


def verificar_inatividade() -> list[dict]:
    """Varre leads em atendimento aberto sem interação há
    `INATIVIDADE_FECHAMENTO_MIN` minutos, encerra e devolve o que precisa
    ser enviado -- `channels/telegram.py` entrega de fato, mesmo padrão de
    `processar_job` (esta função nunca fala com a API do Telegram)."""
    settings = get_settings()
    a_encerrar = repo.leads_inativos(settings.inatividade_fechamento_min)

    resultados = []
    for lead in a_encerrar:
        lead_id = lead["id"]
        repo.salvar_mensagem(lead_id, papel="assistant", conteudo=MENSAGEM_ENCERRAMENTO_INATIVIDADE, origem="texto")
        repo.cancelar_followups_pendentes(lead_id)
        repo.atualizar_status(lead_id, "encerrado")
        resultados.append({
            "lead_id": lead_id,
            "chat_id": lead["chat_id"],
            "canal": lead["canal"],
            "mensagem": MENSAGEM_ENCERRAMENTO_INATIVIDADE,
        })
    return resultados
