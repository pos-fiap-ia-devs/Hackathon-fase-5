"""Score do lead (secao 5.7). Regra determinística, nunca pedida ao LLM --
um numero estavel no dashboard vale mais que um numero "inteligente".

    score = 40 * (slots_preenchidos / slots_totais)
          + 30 * peso_urgencia      (alta=1,0 · media=0,6 · baixa=0,3)
          + 20 * peso_ticket        (faixa normalizada)
          + 10 * (visita_agendada ? 1 : 0)

O numero sozinho nao bastava. O corretor via "66" no painel e tinha que
confiar: nada na tela dizia de onde saiu, nem o que o cliente precisa
informar pra subir. Score deterministico que ninguem entende tem o mesmo
problema pratico de um score alucinado -- ninguem age em cima dele.

Por isso a unidade aqui e a PARCELA, nao o total: `avaliar_criterios`
devolve as quatro com pontos, maximo e um motivo legivel, e o total e a
soma delas. A explicacao nao e narrada depois do calculo (o que permitiria
as duas coisas divergirem) -- ela E o calculo.
"""

from dataclasses import dataclass

from app.agents.router import tipo_sem_quarto

# intencao fica de fora -- e sempre preenchida pelo schema (Literal obrigatorio),
# nao mede progresso de qualificacao.
CAMPOS_QUALIFICACAO = [
    "faixa_preco_min",
    "faixa_preco_max",
    "quartos",
    "regiao",
    "urgencia",
    "perfil_investidor",
    "ticket",
    "expectativa_retorno",
]

PESO_URGENCIA = {"alta": 1.0, "media": 0.6, "baixa": 0.3}

# normaliza o ticket ate R$ 2 mi -- acima disso o peso satura em 1.0.
# Ajustado ao mercado do seed (estado de Sao Paulo): venda vai de
# R$110 mil a R$2 mi, entao o teto acompanha o topo real da base.
TICKET_TETO = 2_000_000

# Faixas da prioridade. O numero continua visivel; o rotulo e o que diz ao
# corretor por quem ligar primeiro.
SCORE_QUENTE = 70
SCORE_MORNO = 40


@dataclass(frozen=True)
class Criterio:
    """Uma parcela da soma, com o porquê já escrito."""

    nome: str
    pontos: int
    maximo: int
    motivo: str


@dataclass(frozen=True)
class ScoreDetalhado:
    total: int
    prioridade: str          # quente | morno | frio
    criterios: list[Criterio]
    justificativa: str
    campos_faltantes: list[str]


# Campos que CADA fluxo realmente pergunta. Fonte única da completude e de
# `campos_faltantes` -- as duas coisas aparecem juntas na tela e não podem
# se contradizer.
#
# Achado ao abrir a conta no painel: contando sempre os 8 campos, um lead
# de compra lia "Dados coletados 20/40 — 4 de 8" logo acima de
# "Qualificação completa". Não era erro de texto: `perfil_investidor`,
# `ticket` e `expectativa_retorno` NUNCA são perguntados a quem quer
# comprar, então esse lead estava estruturalmente limitado a 25 dos 40
# pontos, por campos que ninguém jamais iria preencher. A explicabilidade
# expôs um defeito da fórmula, não da frase.
CAMPOS_POR_INTENCAO: dict[str, dict[str, str]] = {
    "compra": {"faixa_preco_max": "orçamento", "quartos": "quartos",
               "regiao": "região", "urgencia": "urgência"},
    "aluguel": {"faixa_preco_max": "orçamento", "quartos": "quartos",
                "regiao": "região", "urgencia": "urgência"},
    "investimento": {"perfil_investidor": "perfil de investidor", "ticket": "ticket",
                     "expectativa_retorno": "expectativa de retorno", "regiao": "região"},
}


def _campos_relevantes(slots: dict) -> dict[str, str]:
    """Sem intenção declarada ainda não dá pra saber qual fluxo é: cai na
    lista completa, que é o comportamento antigo.

    Quem quer terreno, galpão, loja ou sala não é perguntado sobre quartos
    (agents/router.py::TIPOS_SEM_QUARTO). Se `quartos` continuasse na conta,
    esse lead nasceria com a qualificação incompleta por um campo que o
    agente nunca vai pedir -- o mesmo defeito que `CAMPOS_POR_INTENCAO` já
    corrigiu para investimento."""
    intencao = slots.get("intencao")
    if intencao in CAMPOS_POR_INTENCAO:
        campos = CAMPOS_POR_INTENCAO[intencao]
        if tipo_sem_quarto(slots.get("tipo_imovel")):
            campos = {c: n for c, n in campos.items() if c != "quartos"}
        return campos
    return {campo: campo for campo in CAMPOS_QUALIFICACAO}


def _criterio_completude(slots: dict) -> Criterio:
    relevantes = _campos_relevantes(slots)
    preenchidos = sum(1 for c in relevantes if slots.get(c) is not None)
    total = len(relevantes)
    return Criterio(
        nome="Dados coletados",
        pontos=round(40 * preenchidos / total) if total else 0,
        maximo=40,
        motivo=f"{preenchidos} de {total} campos de qualificação preenchidos.",
    )


def _criterio_urgencia(slots: dict) -> Criterio:
    urgencia = slots.get("urgencia")
    peso = PESO_URGENCIA.get(urgencia, 0.0)
    motivo = (
        f"Urgência {urgencia}." if urgencia
        else "Urgência ainda não informada — vale perguntar, pesa 30 pontos."
    )
    return Criterio(nome="Urgência", pontos=round(30 * peso), maximo=30, motivo=motivo)


def _criterio_ticket(slots: dict) -> Criterio:
    valor = slots.get("ticket") or slots.get("faixa_preco_max") or 0
    peso = min(valor / TICKET_TETO, 1.0)
    motivo = (
        f"Ticket de R$ {valor:,.0f}".replace(",", ".") + f" (teto de referência: R$ {TICKET_TETO:,.0f}".replace(",", ".") + ")."
        if valor else "Orçamento ainda não informado."
    )
    return Criterio(nome="Ticket", pontos=round(20 * peso), maximo=20, motivo=motivo)


def _criterio_visita(visita_agendada: bool) -> Criterio:
    return Criterio(
        nome="Visita agendada",
        pontos=10 if visita_agendada else 0,
        maximo=10,
        motivo="Visita confirmada." if visita_agendada else "Sem visita marcada ainda.",
    )


def avaliar_criterios(slots: dict, *, visita_agendada: bool = False) -> list[Criterio]:
    return [
        _criterio_completude(slots),
        _criterio_urgencia(slots),
        _criterio_ticket(slots),
        _criterio_visita(visita_agendada),
    ]


def campos_faltantes(slots: dict) -> list[str]:
    """O que ainda falta perguntar, no vocabulário do corretor. Mesma fonte
    da completude (`CAMPOS_POR_INTENCAO`) -- é o que garante que "4 de 4
    campos" e "Qualificação completa" nunca apareçam juntos de "falta
    perguntar X" na mesma tela."""
    if slots.get("intencao") not in CAMPOS_POR_INTENCAO:
        return ["intenção"]
    relevantes = CAMPOS_POR_INTENCAO[slots["intencao"]]
    return [rotulo for campo, rotulo in relevantes.items() if slots.get(campo) is None]


def _tem_orcamento(slots: dict) -> bool:
    return bool(slots.get("ticket") or slots.get("faixa_preco_max"))


def prioridade_de(total: int, slots: dict) -> str:
    """Rótulo a partir do total já gravado em `leads.score`.

    Existe para a LISTA do dashboard, que lê o score da coluna em vez de
    recalcular linha a linha. Sem ela, a lista classificaria só por faixa
    e ignoraria a regra do orçamento -- um lead apareceria "quente" na
    tabela e "morno" ao abrir o detalhe. Mesma função nos dois lugares é o
    que impede a tela de se contradizer."""
    if total >= SCORE_QUENTE:
        return "morno" if not _tem_orcamento(slots) else "quente"
    return "morno" if total >= SCORE_MORNO else "frio"


def calcular_score_detalhado(slots: dict, *, visita_agendada: bool = False) -> ScoreDetalhado:
    """Score com a conta aberta. É a função de verdade -- `calcular_score`
    abaixo é só o total dela."""
    criterios = avaliar_criterios(slots, visita_agendada=visita_agendada)
    total = sum(c.pontos for c in criterios)

    if total >= SCORE_QUENTE:
        prioridade = "quente"
    elif total >= SCORE_MORNO:
        prioridade = "morno"
    else:
        prioridade = "frio"

    motivos = [c.motivo for c in criterios if c.pontos or "não informad" in c.motivo]

    # Regra de negócio sobreposta ao número: sem orçamento não existe lead
    # quente. Um lead pode somar 70+ só com urgência alta e muitos campos
    # preenchidos e ainda assim não ter dito quanto pode pagar -- mandar o
    # corretor ligar primeiro pra esse é desperdiçar a ligação. O score
    # continua mostrando o engajamento; a prioridade é que não mente.
    if prioridade == "quente" and not _tem_orcamento(slots):
        prioridade = "morno"
        motivos.append("Limitado a morno: lead quente exige orçamento informado.")

    return ScoreDetalhado(
        total=total,
        prioridade=prioridade,
        criterios=criterios,
        justificativa=" ".join(motivos),
        campos_faltantes=campos_faltantes(slots),
    )


def calcular_score(slots: dict, *, visita_agendada: bool = False) -> int:
    """Só o total. Mantida com a assinatura original porque é o que
    `core/turno.py` grava na coluna `leads.score` em 4 pontos."""
    return calcular_score_detalhado(slots, visita_agendada=visita_agendada).total
