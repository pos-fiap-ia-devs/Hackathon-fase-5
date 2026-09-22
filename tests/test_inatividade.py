"""Fechamento por inatividade (pedido do usuario, separado do follow-up).
Roda contra Postgres real -- mesmo padrao de test_qualifier.py contra
Ollama real, a suite ja assume infra viva."""

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


def test_cliente_voltando_reseta_qualificacao():
    lead = _lead_qualificando("tg:teste_inatividade_reset")
    lead_id = lead["id"]
    repo.atualizar_status(lead_id, "encerrado")

    processar_turno(chat_id="tg:teste_inatividade_reset", canal="telegram", mensagem="Oi de novo", origem="texto")

    slots_apos = repo.get_slots(lead_id)
    # "indefinido" e a sentinela do schema pra "sem intencao ainda"
    # (LeadQualification.intencao e Literal obrigatorio, nunca None) --
    # o que importa aqui e que "compra" (setado antes do reset) sumiu.
    assert slots_apos.get("intencao") in (None, "indefinido")
    assert slots_apos.get("faixa_preco_max") is None
