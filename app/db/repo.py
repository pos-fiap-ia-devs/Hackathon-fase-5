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
    lead_id: int, *, papel: str, conteudo: str, origem: str = "texto",
    anexos: list[dict] | None = None, suspeita_injecao: str | None = None,
) -> None:
    """`anexos` guarda a galeria de fotos enviada junto com a mensagem
    (core/turno.py::RespostaTurno). `Jsonb` explicito porque psycopg3
    adapta lista Python pra ARRAY, nao pra jsonb.

    `suspeita_injecao` e o tipo detectado por core/injecao.py (None =
    limpa). Fica NA mensagem, nao numa tabela de evento a parte, porque a
    tentativa so faz sentido lida junto com o que o cliente escreveu."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mensagens (lead_id, papel, conteudo, origem, anexos, suspeita_injecao)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (lead_id, papel, conteudo, origem, Jsonb(anexos or []), suspeita_injecao),
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


# Criterio de "a regiao que o cliente disse casa com este imovel".
#
# UMA definicao, usada pela validacao (`regiao_existe`) e pela busca
# (`agents/search.py`). Elas ja divergiram: a validacao passou a aceitar
# "Sao Paulo (Capital)" e a busca continuou recusando, entao o agente
# dizia "nao encontrei nada" pra uma regiao que ele mesmo tinha acabado
# de aceitar. Duas copias da mesma regra e como isso acontece.
#
# Casa nos DOIS sentidos:
#   - o termo do cliente dentro do dado   ("Moem" -> bairro "Moema")
#   - o dado dentro do termo do cliente   ("Sao Paulo (Capital)" -> cidade
#     "Sao Paulo"), porque o extrator devolve a regiao com qualificador
#
# `length >= 4` no segundo sentido evita o falso positivo barato: nome
# curto casaria dentro de qualquer frase.
#
# Sem sinal de porcentagem aqui dentro, nem em comentario: o psycopg varre
# a query inteira procurando placeholder e recusa a consulta.
SQL_REGIAO_CASA = """(
    zona = {termo}
    OR unaccent(bairro) ILIKE unaccent({like})
    OR unaccent(cidade) ILIKE unaccent({like})
    OR (length(cidade) >= 4 AND strpos(lower(unaccent({termo})), lower(unaccent(cidade))) > 0)
    OR (length(bairro) >= 4 AND strpos(lower(unaccent({termo})), lower(unaccent(bairro))) > 0)
)"""


def sql_regiao_casa(nome_termo: str, nome_like: str) -> str:
    """Monta o criterio com os nomes de parametro que o chamador usa."""
    return SQL_REGIAO_CASA.format(
        termo=f"%({nome_termo})s", like=f"%({nome_like})s"
    )


def regiao_existe(termo: str) -> bool:
    """A regiao que o cliente citou existe na base, em qualquer preco?

    Mesmo match de `agents/search.py` (zona OU bairro OU cidade), mas SEM
    os filtros de preco/quartos -- serve pra separar dois "nao encontrei"
    que sao problemas diferentes: "a cidade existe mas nao tem imovel
    nessa faixa" pede ajustar preco; "nao atendemos essa cidade" pede
    mostrar onde atendemos."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM imoveis WHERE {sql_regiao_casa('termo', 'like')} LIMIT 1",
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
                   s.intencao, s.regiao, s.quartos, s.faixa_preco_max, s.urgencia, s.ticket
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
            "SELECT papel, conteudo, origem, anexos, suspeita_injecao, criado_em "
            "FROM mensagens WHERE lead_id = %s ORDER BY criado_em",
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


# ---------------------------------------------------------------- login do dashboard
# Usuarios, sessoes e convites. Mesma regra do resto do arquivo: so
# leitura/escrita, nenhuma decisao -- hash e geracao de token moram em
# core/auth.py, a politica de quem pode o que mora em web/api.py.


def contar_usuarios() -> int:
    """Zero = instalacao nova, e `/registro` abre pro primeiro admin."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM usuarios")
        return cur.fetchone()["n"]


def criar_usuario(*, email: str, nome: str, senha_hash: str, papel: str) -> dict | None:
    """None quando o e-mail ja existe -- o chamador transforma isso na
    mensagem da tela, em vez de estourar IntegrityError na cara do usuario."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO usuarios (email, nome, senha_hash, papel)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
            RETURNING id, email, nome, papel, ativo, criado_em
            """,
            (email, nome, senha_hash, papel),
        )
        usuario = cur.fetchone()
        conn.commit()
        return usuario


def get_usuario_por_email(email: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, nome, senha_hash, papel, ativo FROM usuarios WHERE email = %s",
            (email,),
        )
        return cur.fetchone()


def listar_usuarios() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, nome, papel, ativo, criado_em FROM usuarios ORDER BY criado_em"
        )
        return cur.fetchall()


def atualizar_senha(usuario_id: int, senha_hash: str) -> None:
    """Usado no rehash silencioso do login (core/auth.py::verificar_senha)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE usuarios SET senha_hash = %s WHERE id = %s", (senha_hash, usuario_id))
        conn.commit()


def definir_usuario_ativo(usuario_id: int, ativo: bool) -> None:
    """Desativar corta o acesso na hora: as sessoes do usuario somem junto.
    E a razao de a sessao ser server-side e nao um JWT assinado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE usuarios SET ativo = %s WHERE id = %s", (ativo, usuario_id))
        if not ativo:
            cur.execute("DELETE FROM sessoes WHERE usuario_id = %s", (usuario_id,))
        conn.commit()


def criar_sessao(*, token_hash: str, usuario_id: int, expira_em) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessoes (token_hash, usuario_id, expira_em) VALUES (%s, %s, %s)",
            (token_hash, usuario_id, expira_em),
        )
        conn.commit()


def get_sessao(token_hash: str) -> dict | None:
    """Sessao valida -> dados do usuario. Junta com `usuarios` pra que
    usuario desativado nao passe nem se a linha da sessao sobrar."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.email, u.nome, u.papel, s.expira_em
            FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id
            WHERE s.token_hash = %s AND s.expira_em > now() AND u.ativo
            """,
            (token_hash,),
        )
        return cur.fetchone()


def apagar_sessao(token_hash: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessoes WHERE token_hash = %s", (token_hash,))
        conn.commit()


def limpar_sessoes_expiradas() -> int:
    """Chamado no login -- barato, e evita um job so pra isso numa POC."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessoes WHERE expira_em <= now()")
        apagadas = cur.rowcount
        conn.commit()
        return apagadas


def criar_convite(*, token_hash: str, email: str, papel: str, criado_por: int, expira_em) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO convites (token_hash, email, papel, criado_por, expira_em)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (token_hash, email, papel, criado_por, expira_em),
        )
        conn.commit()


def get_convite_valido(token_hash: str) -> dict | None:
    """So convite no prazo e ainda nao usado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT token_hash, email, papel, expira_em FROM convites
            WHERE token_hash = %s AND usado_em IS NULL AND expira_em > now()
            """,
            (token_hash,),
        )
        return cur.fetchone()


def marcar_convite_usado(token_hash: str) -> bool:
    """True so se ESTA chamada fechou o convite. O `usado_em IS NULL` no
    WHERE faz o banco resolver a corrida de dois cadastros simultaneos com
    o mesmo link -- quem chegar depois leva False e nao cria usuario."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE convites SET usado_em = now() WHERE token_hash = %s AND usado_em IS NULL",
            (token_hash,),
        )
        fechou = cur.rowcount == 1
        conn.commit()
        return fechou


def listar_convites_pendentes() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT email, papel, expira_em, criado_em FROM convites
            WHERE usado_em IS NULL AND expira_em > now() ORDER BY criado_em DESC
            """
        )
        return cur.fetchall()


# ---------------------------------------------------------------- indicadores de observabilidade


def resumo_traces(janela_horas: int = 24) -> dict:
    """Agregado de `agent_traces` na janela -- o que vira os cartoes no topo
    da aba de observabilidade.

    p95 e nao media de proposito: media esconde a cauda, e a cauda e
    exatamente o que o cliente sente. Com 9-10s de turno tipico, uma
    chamada de 25s e a diferenca entre "pensando" e "travou".

    `percentile_cont` interpola entre as duas amostras vizinhas; com
    poucas chamadas o numero oscila, e por isso o template mostra a
    contagem ao lado -- p95 de 7 chamadas nao e p95 de nada.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*)                                           AS chamadas,
                count(*) FILTER (WHERE erro IS NOT NULL)           AS erros,
                coalesce(sum(prompt_tokens), 0)                    AS tokens_entrada,
                coalesce(sum(completion_tokens), 0)                AS tokens_saida,
                percentile_cont(0.5)  WITHIN GROUP (ORDER BY latencia_ms) AS p50_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY latencia_ms) AS p95_ms,
                max(latencia_ms)                                   AS pior_ms,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY tokens_por_s)
                    FILTER (WHERE tokens_por_s IS NOT NULL)        AS tps_mediano
            FROM agent_traces
            WHERE criado_em > now() - make_interval(hours => %s)
            """,
            (janela_horas,),
        )
        return cur.fetchone()


def resumo_traces_por_agente(janela_horas: int = 24) -> list[dict]:
    """Mesmo recorte, quebrado por agente -- mostra QUEM custa caro. Medido
    assim que o resumo e a memoria sairam do caminho critico do turno
    (core/graph.py::_tarefas_de_fundo)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                agente,
                count(*)                                           AS chamadas,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY latencia_ms) AS p95_ms,
                -- coalesce por campo, nao na soma: um unico NULL dentro de
                -- `prompt_tokens + completion_tokens` anularia a linha toda.
                sum(coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)) AS tokens
            FROM agent_traces
            WHERE criado_em > now() - make_interval(hours => %s)
            GROUP BY agente
            ORDER BY count(*) DESC
            """,
            (janela_horas,),
        )
        return cur.fetchall()


def contar_leads_atendidos(janela_horas: int = 24) -> int:
    """Leads com ao menos uma chamada de LLM na janela -- denominador do
    "custo por conversa". Conta `agent_traces`, nao `leads`: lead que
    entrou pelo CRM e nunca conversou nao consumiu inferencia nenhuma e
    baixaria a media artificialmente."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(DISTINCT lead_id) AS n FROM agent_traces
            WHERE lead_id IS NOT NULL AND criado_em > now() - make_interval(hours => %s)
            """,
            (janela_horas,),
        )
        return cur.fetchone()["n"]


def resumo_injecoes(janela_horas: int = 24) -> list[dict]:
    """Tentativas de prompt injection na janela, por tipo (core/injecao.py).

    Alimenta o cartao da aba de observabilidade. Lista vazia e o estado
    normal e o template trata como tal -- "0 tentativas" e informacao boa,
    nao ausencia de dado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT suspeita_injecao AS tipo, count(*) AS tentativas
            FROM mensagens
            WHERE suspeita_injecao IS NOT NULL
              AND criado_em > now() - make_interval(hours => %s)
            GROUP BY suspeita_injecao
            ORDER BY count(*) DESC
            """,
            (janela_horas,),
        )
        return cur.fetchall()


def listar_mensagens_suspeitas(limite: int = 20) -> list[dict]:
    """As tentativas em si, pro corretor ver o que foi escrito -- numero
    agregado sem o texto nao deixa ninguem julgar se e falso positivo."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT lead_id, conteudo, suspeita_injecao, criado_em
            FROM mensagens WHERE suspeita_injecao IS NOT NULL
            ORDER BY criado_em DESC LIMIT %s
            """,
            (limite,),
        )
        return cur.fetchall()


def get_imovel_por_codigo(codigo: str) -> dict | None:
    """Imovel pelo codigo publico (MOE-0001). `upper()` porque o cliente
    digita como quiser -- a coluna guarda sempre em maiuscula (seed)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM imoveis WHERE codigo = %s", (codigo.upper(),))
        return cur.fetchone()


def registrar_acao(lead_id: int, acao: str) -> int:
    """Conta quantas vezes SEGUIDAS o roteador decidiu a mesma coisa.

    Devolve a contagem ja atualizada (1 = primeira vez). Acao diferente
    zera o contador -- o que importa e a insistencia consecutiva, nao o
    total historico.

    Existe porque `decidir_proximo` e funcao pura dos slots: cliente que
    nao responde o que foi perguntado nao muda slot nenhum, e o agente
    repetia a mesma pergunta indefinidamente."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE leads
               SET acao_repeticoes = CASE WHEN ultima_acao = %(acao)s
                                          THEN acao_repeticoes + 1 ELSE 1 END,
                   ultima_acao = %(acao)s
             WHERE id = %(lead_id)s
            RETURNING acao_repeticoes
            """,
            {"acao": acao, "lead_id": lead_id},
        )
        linha = cur.fetchone()
        conn.commit()
        return linha["acao_repeticoes"] if linha else 1


def faixa_de_precos(finalidade: str) -> dict | None:
    """Menor e maior preco da base, pra situar o cliente que nao sabe
    informar orcamento. Numero vem do banco, nunca do LLM (secao 5.6)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT min(preco) AS minimo, max(preco) AS maximo FROM imoveis WHERE finalidade = %s",
            (finalidade,),
        )
        return cur.fetchone()


def dispensar_campo(lead_id: int, pergunta: str) -> None:
    """Marca que o cliente nao vai informar este campo. O roteiro para de
    perguntar (agents/router.py::decidir_proximo) e segue com o que tem.

    `array_append` condicionado ao `NOT ... = ANY(...)` pra nao duplicar:
    o mesmo campo pode ser dispensado mais de uma vez numa corrida."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE leads
               SET campos_dispensados = array_append(campos_dispensados, %(p)s)
             WHERE id = %(lead_id)s
               AND NOT (%(p)s = ANY(campos_dispensados))
            """,
            {"p": pergunta, "lead_id": lead_id},
        )
        conn.commit()


def get_campos_dispensados(lead_id: int) -> tuple[str, ...]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT campos_dispensados FROM leads WHERE id = %s", (lead_id,))
        linha = cur.fetchone()
        return tuple(linha["campos_dispensados"] or ()) if linha else ()


# ---------------------------------------------------------------- painel: funil e indicadores

# Etapas do funil, na ordem em que o lead anda. `encerrado` e `perdido`
# NAO entram no quadro: sao saidas, nao etapas -- misturar os dois com as
# colunas ativas faria 85 cartoes mortos esconderem os 7 que importam.
ETAPAS_FUNIL = (
    ("novo", "Novo"),
    ("qualificando", "Qualificando"),
    ("aguardando_escolha", "Escolhendo imóvel"),
    ("visita_agendada", "Visita marcada"),
    ("encaminhado", "Com especialista"),
)


def funil() -> dict[str, list[dict]]:
    """Leads por etapa, pro quadro Kanban. Dentro da coluna, o de maior
    score primeiro -- a pergunta do corretor e "por quem ligo agora"."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.chat_id, l.canal, l.nome, l.status, l.score,
                   l.telefone_mascarado, l.ultima_interacao_em,
                   s.intencao, s.regiao, s.quartos, s.faixa_preco_max, s.ticket, s.urgencia,
                   r.texto AS resumo,
                   (SELECT conteudo FROM mensagens m
                     WHERE m.lead_id = l.id AND m.papel = 'user'
                     ORDER BY m.criado_em DESC LIMIT 1) AS ultima_fala
              FROM leads l
              LEFT JOIN slots s   ON s.lead_id = l.id
              LEFT JOIN resumos r ON r.lead_id = l.id
             WHERE l.status = ANY(%s)
             ORDER BY l.score DESC, l.ultima_interacao_em DESC
            """,
            ([etapa for etapa, _ in ETAPAS_FUNIL],),
        )
        linhas = cur.fetchall()

    colunas: dict[str, list[dict]] = {etapa: [] for etapa, _ in ETAPAS_FUNIL}
    for linha in linhas:
        colunas[linha["status"]].append(linha)
    return colunas


def indicadores() -> dict:
    """Os números do topo do painel. Todos saem de dado medido, nenhum é
    estimativa -- mesma regra do painel de observabilidade."""
    with get_conn() as conn, conn.cursor() as cur:
        # Tempo até a 1a resposta: diferença entre a 1a fala do cliente e a
        # 1a resposta do agente, por lead. É o indicador que o enunciado
        # cita primeiro ("tempo de resposta elevado" perde lead).
        cur.execute(
            """
            WITH primeira AS (
                SELECT lead_id,
                       min(criado_em) FILTER (WHERE papel = 'user')      AS cliente,
                       min(criado_em) FILTER (WHERE papel = 'assistant') AS agente
                  FROM mensagens GROUP BY lead_id
            )
            SELECT avg(EXTRACT(EPOCH FROM (agente - cliente))) AS segundos
              FROM primeira WHERE cliente IS NOT NULL AND agente > cliente
            """
        )
        resposta = cur.fetchone()["segundos"]

        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE l.status <> 'novo')                       AS atendidos,
                count(*) FILTER (WHERE s.intencao IS NOT NULL
                                   AND s.intencao <> 'indefinido')               AS qualificados,
                (SELECT count(*) FROM visitas WHERE status = 'agendada')         AS visitas,
                (SELECT count(*) FROM encaminhamentos)                           AS encaminhados,
                -- Recuperado: recebeu follow-up e VOLTOU a falar depois dele.
                -- É o cenário 3 do enunciado funcionando, medido.
                (SELECT count(DISTINCT f.lead_id) FROM follow_up_jobs f
                  WHERE f.status = 'enviado'
                    AND EXISTS (SELECT 1 FROM mensagens m
                                 WHERE m.lead_id = f.lead_id AND m.papel = 'user'
                                   AND m.criado_em > f.executar_em))             AS recuperados
              FROM leads l LEFT JOIN slots s ON s.lead_id = l.id
            """
        )
        linha = cur.fetchone()

    atendidos = linha["atendidos"] or 0
    return {
        "primeira_resposta_s": round(resposta) if resposta else None,
        "taxa_qualificacao": round(100 * linha["qualificados"] / atendidos) if atendidos else 0,
        "visitas": linha["visitas"],
        "encaminhados": linha["encaminhados"],
        "recuperados": linha["recuperados"],
    }


# Etapas que o corretor pode arrastar livremente: nenhuma delas faz o
# grafo prometer algo ao cliente.
ETAPAS_LIVRES = ("novo", "qualificando", "aguardando_escolha")

# Estas duas mudam o RAMO do grafo e fazem o agente falar de um
# compromisso concreto (`core/turno.py::_resumo_desfecho` cita data,
# corretor e imovel). Sem o registro correspondente, o proximo turno do
# cliente cairia num ramo que nao tem o que citar -- e o guardrail de nao
# alucinar so funciona quando o dado real existe.
ETAPAS_COM_REGISTRO = {
    "visita_agendada": "visitas",
    "encaminhado": "encaminhamentos",
}


def mover_lead(lead_id: int, etapa: str) -> str | None:
    """Move o lead de etapa. Devolve None em caso de sucesso, ou a razao
    da recusa -- que o painel mostra ao corretor.

    Recusar e deliberado: o `status` nao e so rotulo de quadro, e o que
    roteia o grafo (core/graph.py::_rota_por_status)."""
    valido = {e for e, _ in ETAPAS_FUNIL}
    if etapa not in valido:
        return f"Etapa desconhecida: {etapa}"

    tabela = ETAPAS_COM_REGISTRO.get(etapa)
    with get_conn() as conn, conn.cursor() as cur:
        if tabela:
            cur.execute(f"SELECT 1 FROM {tabela} WHERE lead_id = %s LIMIT 1", (lead_id,))
            if not cur.fetchone():
                rotulo = dict(ETAPAS_FUNIL)[etapa]
                return (
                    f"Não dá pra mover para “{rotulo}”: não há "
                    f"{'visita agendada' if tabela == 'visitas' else 'encaminhamento'} "
                    "registrado para este lead. O agente passaria a falar de um "
                    "compromisso que não existe."
                )
        cur.execute("UPDATE leads SET status = %s WHERE id = %s", (etapa, lead_id))
        movidos = cur.rowcount
        conn.commit()
    return None if movidos else "Lead não encontrado."
