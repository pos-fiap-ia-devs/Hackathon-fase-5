"""Fechamento por inatividade (pedido do usuario, separado do follow-up).
Roda contra Postgres real -- mesmo padrao de test_qualifier.py contra
Ollama real, a suite ja assume infra viva."""

from datetime import UTC, datetime, timedelta

from app.core import followup
from app.core.turno import processar_turno
from app.db import repo


def _lead_qualificando(chat_id: str) -> dict:
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="telegram")
    repo.salvar_slots(lead["id"], {"intencao": "compra", "faixa_preco_max": 300_000})
    repo.atualizar_status_score(lead["id"], score=20, status="qualificando")
    return lead


def test_lead_recente_nao_encerra():
    lead = _lead_qualificando("tg:teste_inatividade_recente")
    encerrados = followup.verificar_inatividade()
    assert lead["id"] not in [e["lead_id"] for e in encerrados]
    assert repo.get_lead(lead["id"])["status"] == "qualificando"


def test_lead_antigo_encerra_com_mensagem_fixa():
    lead = _lead_qualificando("tg:teste_inatividade_antiga")
    lead_id = lead["id"]
    with repo.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE leads SET ultima_interacao_em = now() - interval '999 minutes' WHERE id = %s",
            (lead_id,),
        )
        conn.commit()

    encerrados = followup.verificar_inatividade()
    ids = [e["lead_id"] for e in encerrados]
    assert lead_id in ids
    item = next(e for e in encerrados if e["lead_id"] == lead_id)
    assert item["mensagem"] == followup.MENSAGEM_ENCERRAMENTO_INATIVIDADE

    assert repo.get_lead(lead_id)["status"] == "encerrado"


def test_cliente_voltando_preserva_o_que_ja_contou():
    """Retomada, nao "novo atendimento" (pedido do usuario): o que o cliente
    ja disse continua valendo quando ele volta depois do encerramento."""
    lead = _lead_qualificando("tg:teste_inatividade_reset")
    lead_id = lead["id"]
    repo.atualizar_contato(lead_id, nome="Wellson", telefone_mascarado="(11) ****-1234")
    repo.atualizar_status(lead_id, "encerrado")

    processar_turno(chat_id="tg:teste_inatividade_reset", canal="telegram", mensagem="Oi de novo", origem="texto")

    slots_apos = repo.get_slots(lead_id)
    assert slots_apos.get("intencao") == "compra"
    assert slots_apos.get("faixa_preco_max") == 300_000

    lead_apos = repo.get_lead(lead_id)
    assert lead_apos["nome"] == "Wellson"
    assert lead_apos["telefone_mascarado"] == "(11) ****-1234"
    # volta pro ramo de qualificacao, de onde parou
    assert lead_apos["status"] == "qualificando"


def test_cliente_voltando_com_visita_marcada_nao_reabre_qualificacao():
    """Visita vale ate o dia dela (pedido do usuario) -- encerrar por
    inatividade no meio nao pode apagar o compromisso do contexto."""
    lead = _lead_qualificando("tg:teste_inatividade_visita")
    lead_id = lead["id"]
    repo.criar_visita(
        lead_id,
        imovel_id=None,
        corretor="Marina",
        data_hora=datetime.now(UTC) + timedelta(days=3),
        horario_solicitado="sexta de manha",
    )
    repo.atualizar_status(lead_id, "encerrado")

    assert repo.get_visita_ativa(lead_id) is not None

    processar_turno(chat_id="tg:teste_inatividade_visita", canal="telegram", mensagem="Oi, tudo certo?", origem="texto")

    # ramo pos-desfecho, nao qualificacao do zero
    assert repo.get_lead(lead_id)["status"] == "visita_agendada"


def test_visita_que_ja_passou_nao_conta_como_ativa():
    lead = _lead_qualificando("tg:teste_visita_passada")
    lead_id = lead["id"]
    repo.criar_visita(
        lead_id,
        imovel_id=None,
        corretor="Marina",
        data_hora=datetime.now(UTC) - timedelta(days=2),
        horario_solicitado="terca de tarde",
    )
    assert repo.get_visita_ativa(lead_id) is None
