"""Pool de conexao unico para o processo. dict_row para nao trabalhar com tuplas soltas."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.config import get_settings


def _configure(conn: Connection) -> None:
    register_vector(conn)


@lru_cache
def get_pool() -> ConnectionPool:
    """Um pool por processo. `lru_cache` em vez de `global` + checagem de
    None -- mesmo idioma ja usado em `core/config.py::get_settings`."""
    settings = get_settings()
    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=5,
        configure=_configure,
        open=True,
        kwargs={"row_factory": dict_row},
    )


@contextmanager
def get_conn() -> Iterator[Connection]:
    with get_pool().connection() as conn:
        yield conn


# ---------------------------------------------------------------- leads/slots/mensagens
# Camada de acesso a dados do orquestrador de turno (Etapa 4). Sem regra de
# negocio aqui -- so leitura/escrita. As decisoes (o que perguntar, como
# responder) ficam em agents/ e core/turno.py.

SLOTS_CAMPOS = [
    "intencao", "faixa_preco_min", "faixa_preco_max", "quartos", "regiao",
    "urgencia", "perfil_investidor", "ticket", "expectativa_retorno",
]


def get_or_create_lead(*, chat_id: str, canal: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO leads (chat_id, canal) VALUES (%s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET ultima_interacao_em = now()
            RETURNING *
            """,
            (chat_id, canal),
        )
        lead = cur.fetchone()
        conn.commit()
        return lead


def get_slots(lead_id: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(SLOTS_CAMPOS)} FROM slots WHERE lead_id = %s", (lead_id,))
        row = cur.fetchone()
        return dict(row) if row else {}


def salvar_slots(lead_id: int, slots: dict) -> None:
    colunas = ", ".join(SLOTS_CAMPOS)
    valores = ", ".join(f"%({c})s" for c in SLOTS_CAMPOS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in SLOTS_CAMPOS)
    params = {"lead_id": lead_id, **{c: slots.get(c) for c in SLOTS_CAMPOS}}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO slots (lead_id, {colunas}, atualizado_em)
            VALUES (%(lead_id)s, {valores}, now())
            ON CONFLICT (lead_id) DO UPDATE SET {updates}, atualizado_em = now()
            """,
            params,
        )
        conn.commit()


def salvar_mensagem(
    lead_id: int, *, papel: str, conteudo: str, origem: str = "texto", anexos: list[dict] | None = None
) -> None:
    """`anexos` guarda a galeria de fotos enviada junto com a mensagem
    (core/turno.py::RespostaTurno). `Jsonb` explicito porque psycopg3
    adapta lista Python pra ARRAY, nao pra jsonb."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mensagens (lead_id, papel, conteudo, origem, anexos) VALUES (%s, %s, %s, %s, %s)",
            (lead_id, papel, conteudo, origem, Jsonb(anexos or [])),
        )
        conn.commit()


def contar_turnos(lead_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM mensagens WHERE lead_id = %s AND papel = 'user'", (lead_id,))
        return cur.fetchone()["n"]


def get_historico(lead_id: int, limite: int = 6) -> list[dict]:
    """Ultimas N mensagens, mais antiga primeiro -- usado na memoria (Etapa 6)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT papel, conteudo, origem, criado_em FROM mensagens
            WHERE lead_id = %s ORDER BY criado_em DESC LIMIT %s
            """,
            (lead_id, limite),
        )
        return list(reversed(cur.fetchall()))


def atualizar_status_score(lead_id: int, *, score: int, status: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE leads SET score = %s, status = %s, ultima_interacao_em = now() WHERE id = %s",
            (score, status, lead_id),
        )
        conn.commit()


def atualizar_status(lead_id: int, status: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE leads SET status = %s, ultima_interacao_em = now() WHERE id = %s",
            (status, lead_id),
        )
        conn.commit()


def regiao_existe(termo: str) -> bool:
    """A regiao que o cliente citou existe na base, em qualquer preco?

    Mesmo match de `agents/search.py` (zona OU bairro OU cidade), mas SEM
    os filtros de preco/quartos -- serve pra separar dois "nao encontrei"
    que sao problemas diferentes: "a cidade existe mas nao tem imovel
    nessa faixa" pede ajustar preco; "nao atendemos essa cidade" pede
    mostrar onde atendemos."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM imoveis
            WHERE zona = %(termo)s
               OR unaccent(bairro) ILIKE unaccent(%(like)s)
               OR unaccent(cidade) ILIKE unaccent(%(like)s)
            LIMIT 1
            """,
            {"termo": termo, "like": f"%{termo}%"},
        )
        return cur.fetchone() is not None


def cidades_disponiveis(finalidade: str) -> list[dict]:
    """Cidades atendidas, com quantidade e preco de entrada -- resposta pra
    quem pediu uma cidade que a imobiliaria nao cobre."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cidade, count(*) AS n, min(preco) AS preco_min
            FROM imoveis WHERE finalidade = %s
            GROUP BY cidade ORDER BY count(*) DESC
            """,
            (finalidade,),
        )
        return cur.fetchall()


def regioes_disponiveis(
    *, finalidade: str, preco_max: int | None = None, quartos_min: int | None = None
) -> list[dict]:
    """Zonas/bairros que REALMENTE tem imovel batendo com os filtros ja
    coletados. Achado em uso real: o cliente perguntou "quais bairros tem
    com 2 quartos" e o agente respondeu "tem preferencia por algum desses
    bairros?" -- sem listar nenhum, porque nao existia lugar nenhum no
    fluxo que respondesse isso. Lista montada em SQL e formatada em
    codigo: nome de bairro e exatamente o tipo de dado que o LLM nao pode
    inventar (mesmo guardrail do card de imovel, secao 5.6)."""
    where = ["finalidade = %(finalidade)s"]
    params: dict = {"finalidade": finalidade}
    if preco_max is not None:
        where.append("preco <= %(preco_max)s")
        params["preco_max"] = preco_max
    if quartos_min is not None:
        where.append("quartos >= %(quartos_min)s")
        params["quartos_min"] = quartos_min

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT cidade, zona, bairro, count(*) AS n, min(preco) AS preco_min
            FROM imoveis
            WHERE {" AND ".join(where)}
            GROUP BY cidade, zona, bairro
            ORDER BY count(*) DESC, cidade, bairro
            """,
            params,
        )
        return cur.fetchall()


def leads_inativos(minutos: int) -> list[dict]:
    """Leads em atendimento aberto (novo/qualificando/aguardando_escolha)
    sem interacao ha `minutos` -- usado pelo fechamento por inatividade
    (core/followup.py::verificar_inatividade). `ultima_interacao_em` so
    muda em acao de turno de verdade (get_or_create_lead no inbound,
    atualizar_status*) -- mensagem de follow-up enviada pelo bot NAO conta
    como interacao, entao o relogio mede silencio real do cliente."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, chat_id, canal FROM leads
            WHERE status IN ('novo', 'qualificando', 'aguardando_escolha')
              AND ultima_interacao_em <= now() - (%s::text || ' minutes')::interval
            """,
            (minutos,),
        )
        return cur.fetchall()


def retomar_atendimento(lead_id: int) -> None:
    """Cliente voltou depois de encerrado por inatividade.

    Antes isso era um "novo atendimento" que zerava slots, score e imoveis
    apresentados. Virou retomada (pedido do usuario): nada do que o cliente
    ja contou e apagado -- nome, telefone, slots, score, historico, memoria
    e desfechos continuam gravados, e o agente recebe esses dados de volta
    no contexto do turno (core/turno.py::_contexto_cliente_conhecido), pra
    nao perguntar duas vezes a mesma coisa.

    A unica coisa limpa aqui e o estado PARCIAL do sub-fluxo de agendamento
    (`visita_*`): ele descreve uma escolha em andamento sobre uma lista de
    imoveis que nao esta mais na conversa. Visita ja confirmada nao mora
    nesses campos, e sim na tabela `visitas` -- essa nunca e tocada.

    Status volta pro ramo de qualificacao: 'qualificando' se ja havia
    intencao definida (o agente retoma de onde parou), 'novo' se nao.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE leads SET
                status = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM slots s
                        WHERE s.lead_id = leads.id
                          AND s.intencao IS NOT NULL AND s.intencao <> 'indefinido'
                    ) THEN 'qualificando'
                    ELSE 'novo'
                END,
                visita_imovel_escolhido = NULL, visita_horario_texto = NULL,
                visita_nome_texto = NULL, visita_telefone_texto = NULL,
                ultima_interacao_em = now()
            WHERE id = %s
            """,
            (lead_id,),
        )
        conn.commit()


# ---------------------------------------------------------------- busca / agendamento / encaminhamento (Etapa 5)

def salvar_imoveis_apresentados(lead_id: int, imovel_ids: list[int]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE leads
            SET imoveis_apresentados = %s, visita_imovel_escolhido = NULL,
                visita_horario_texto = NULL, visita_nome_texto = NULL,
                visita_telefone_texto = NULL, ultima_interacao_em = now()
            WHERE id = %s
            """,
            (imovel_ids, lead_id),
        )
        conn.commit()


def salvar_escolha_visita_parcial(
    lead_id: int,
    *,
    imovel_id: int | None,
    horario_texto: str | None,
    nome: str | None = None,
    telefone: str | None = None,
) -> None:
    """Persiste o que ja foi dito ate agora no sub-fluxo de agendamento
    (status=aguardando_escolha), pra nao perder entre turnos."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE leads
            SET visita_imovel_escolhido = %s, visita_horario_texto = %s,
                visita_nome_texto = %s, visita_telefone_texto = %s, ultima_interacao_em = now()
            WHERE id = %s
            """,
            (imovel_id, horario_texto, nome, telefone, lead_id),
        )
        conn.commit()


def atualizar_contato(lead_id: int, *, nome: str | None, telefone_mascarado: str | None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE leads SET nome = %s, telefone_mascarado = %s, ultima_interacao_em = now() WHERE id = %s",
            (nome, telefone_mascarado, lead_id),
        )
        conn.commit()


def get_lead(lead_id: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
        return cur.fetchone()


def get_imovel(imovel_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM imoveis WHERE id = %s", (imovel_id,))
        return cur.fetchone()


def criar_visita(
    lead_id: int, *, imovel_id: int, corretor: str, data_hora, horario_solicitado: str | None
) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO visitas (lead_id, imovel_id, corretor, data_hora, horario_solicitado)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (lead_id, imovel_id, corretor, data_hora, horario_solicitado),
        )
        visita = cur.fetchone()
        conn.commit()
        return visita


def criar_encaminhamento(lead_id: int, *, especialista: str, motivo: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO encaminhamentos (lead_id, especialista, motivo)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (lead_id, especialista, motivo),
        )
        enc = cur.fetchone()
        conn.commit()
        return enc


def get_visita_ativa(lead_id: int) -> dict | None:
    """Visita agendada que ainda nao passou -- o compromisso vale ate o DIA
    da visita (pedido do usuario). Comparacao contra `date_trunc('day')`,
    nao contra `now()`: visita marcada pra hoje as 10h continua ativa as
    15h, o cliente ainda esta dentro do dia dela.

    Usado em dois pontos: nao transformar o retorno do cliente em "novo
    atendimento" quando ha visita marcada (core/turno.py::processar_turno)
    e lembrar a visita no contexto do agente (_contexto_cliente_conhecido).
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM visitas
            WHERE lead_id = %s AND status = 'agendada'
              AND data_hora >= date_trunc('day', now())
            ORDER BY data_hora LIMIT 1
            """,
            (lead_id,),
        )
        return cur.fetchone()


def get_ultima_visita(lead_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM visitas WHERE lead_id = %s ORDER BY criado_em DESC LIMIT 1",
            (lead_id,),
        )
        return cur.fetchone()


def get_ultimo_encaminhamento(lead_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM encaminhamentos WHERE lead_id = %s ORDER BY criado_em DESC LIMIT 1",
            (lead_id,),
        )
        return cur.fetchone()


def atualizar_horario_visita(visita_id: int, *, horario_solicitado: str, data_hora=None) -> None:
    """Reagendamento (secao 5.9/pos-desfecho) -- achado no Telegram real: o
    cliente corrigiu o horario duas vezes e o agente respondeu "anotado!"
    sem gravar nada, entao a proxima pergunta sobre a reserva ainda citava
    o horario antigo. Sem isso o "anotado" do LLM e uma mentira.

    `data_hora` so e passado quando core/turno.py::_resolver_data conseguiu
    calcular uma data real a partir do texto (ex: "quinta que vem" -> uma
    data de verdade) -- nunca vem do LLM."""
    with get_conn() as conn, conn.cursor() as cur:
        if data_hora is not None:
            cur.execute(
                "UPDATE visitas SET horario_solicitado = %s, data_hora = %s WHERE id = %s",
                (horario_solicitado, data_hora, visita_id),
            )
        else:
            cur.execute(
                "UPDATE visitas SET horario_solicitado = %s WHERE id = %s",
                (horario_solicitado, visita_id),
            )
        conn.commit()


# ---------------------------------------------------------------- memoria (resumo rolante, Etapa 6)

def get_memoria(lead_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT resumo, turnos_resumidos FROM memoria WHERE lead_id = %s", (lead_id,))
        return cur.fetchone()


def salvar_memoria(lead_id: int, *, resumo: str, turnos_resumidos: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memoria (lead_id, resumo, turnos_resumidos, atualizado_em)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (lead_id) DO UPDATE SET
                resumo = EXCLUDED.resumo, turnos_resumidos = EXCLUDED.turnos_resumidos, atualizado_em = now()
            """,
            (lead_id, resumo, turnos_resumidos),
        )
        conn.commit()


# ---------------------------------------------------------------- resumo pro corretor (Etapa 6)

def salvar_resumo(lead_id: int, texto: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumos (lead_id, texto, atualizado_em)
            VALUES (%s, %s, now())
            ON CONFLICT (lead_id) DO UPDATE SET texto = EXCLUDED.texto, atualizado_em = now()
            """,
            (lead_id, texto),
        )
        conn.commit()


def get_resumo(lead_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT texto, atualizado_em FROM resumos WHERE lead_id = %s", (lead_id,))
        return cur.fetchone()


# ---------------------------------------------------------------- follow-up (cenario 3, Etapa 6)

def criar_followup_job(lead_id: int, *, tentativa: int, executar_em) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO follow_up_jobs (lead_id, tentativa, executar_em) VALUES (%s, %s, %s)",
            (lead_id, tentativa, executar_em),
        )
        conn.commit()


def cancelar_followups_pendentes(lead_id: int) -> None:
    """Chamado sempre que o lead manda uma mensagem nova -- ele esta ativo,
    nao faz sentido reengajar quem acabou de responder."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_up_jobs SET status = 'cancelado' WHERE lead_id = %s AND status = 'pendente'",
            (lead_id,),
        )
        conn.commit()


def get_followups_vencidos() -> list[dict]:
    """Jobs pendentes cujo horario ja chegou, com os dados do lead juntos
    (pra decidir se ainda faz sentido reengajar e pra montar a mensagem)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.id AS job_id, f.lead_id, f.tentativa, l.chat_id, l.canal, l.status AS lead_status
            FROM follow_up_jobs f
            JOIN leads l ON l.id = f.lead_id
            WHERE f.status = 'pendente' AND f.executar_em <= now()
            ORDER BY f.executar_em
            """
        )
        return cur.fetchall()


def marcar_followup_enviado(job_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_up_jobs SET status = 'enviado', executado_em = now() WHERE id = %s",
            (job_id,),
        )
        conn.commit()


def marcar_followup_cancelado(job_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE follow_up_jobs SET status = 'cancelado' WHERE id = %s", (job_id,))
        conn.commit()




# ---------------------------------------------------------------- dashboard (Etapa 7)

def listar_leads(limite: int = 200) -> list[dict]:
    """Ordenado por score -- e o que a secao 5.7/1.1 do PLANO.md pede:
    "priorizar leads quentes" tem que ser visivel de cara no dashboard."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.chat_id, l.canal, l.nome, l.status, l.score,
                   l.criado_em, l.ultima_interacao_em,
                   s.intencao, s.regiao, s.quartos, s.faixa_preco_max, s.urgencia
            FROM leads l LEFT JOIN slots s ON s.lead_id = l.id
            ORDER BY l.score DESC, l.ultima_interacao_em DESC
            LIMIT %s
            """,
            (limite,),
        )
        return cur.fetchall()


def listar_mensagens(lead_id: int) -> list[dict]:
    """Conversa inteira, mais antiga primeiro -- pro painel de detalhe
    (get_historico e limitado, pensado pro prompt do LLM, nao pra tela)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT papel, conteudo, origem, anexos, criado_em FROM mensagens WHERE lead_id = %s ORDER BY criado_em",
            (lead_id,),
        )
        return cur.fetchall()


def listar_traces(lead_id: int | None = None, limite: int = 100) -> list[dict]:
    """Observabilidade (diferencial) -- latencia/tokens medidos, nao
    estimados (secao 7 do PLANO.md, argumento de escalabilidade)."""
    with get_conn() as conn, conn.cursor() as cur:
        if lead_id is not None:
            cur.execute(
                """
                SELECT id, lead_id, turno, agente, modelo, prompt_tokens, completion_tokens,
                       latencia_ms, tokens_por_s, erro, criado_em
                FROM agent_traces WHERE lead_id = %s ORDER BY criado_em DESC LIMIT %s
                """,
                (lead_id, limite),
            )
        else:
            cur.execute(
                """
                SELECT id, lead_id, turno, agente, modelo, prompt_tokens, completion_tokens,
                       latencia_ms, tokens_por_s, erro, criado_em
                FROM agent_traces ORDER BY criado_em DESC LIMIT %s
                """,
                (limite,),
            )
        return cur.fetchall()
