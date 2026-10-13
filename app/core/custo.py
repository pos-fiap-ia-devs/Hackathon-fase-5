"""Custo contrafactual da inferência.

O custo real deste projeto é zero: o modelo roda local, nenhum token sai
da máquina. Isso é o argumento mais forte da arquitetura e, até aqui, só
existia como frase no README.

Aqui ele vira número: `agent_traces` já mede tokens de entrada e saída de
verdade (lidos de `eval_count`/`prompt_eval_count` da resposta do Ollama,
não estimados), então dá pra responder "quanto ESTA conversa teria custado
numa API paga" sem chutar nada além do preço de tabela.

Os preços são de REFERÊNCIA e vêm do `.env` (`PRECO_REF_USD_POR_MI_*`).
Não há chamada a nenhuma API de preço nem de câmbio: cotação que envelhece
em silêncio dentro do código é pior que número declarado na configuração.
"""

from dataclasses import dataclass

from app.core.config import get_settings

UM_MILHAO = 1_000_000


@dataclass(frozen=True)
class CustoEquivalente:
    """Quanto os tokens medidos custariam numa API paga."""

    usd: float
    brl: float
    tokens_entrada: int
    tokens_saida: int

    @property
    def total_tokens(self) -> int:
        return self.tokens_entrada + self.tokens_saida


def calcular(tokens_entrada: int, tokens_saida: int) -> CustoEquivalente:
    s = get_settings()
    usd = (
        (tokens_entrada / UM_MILHAO) * s.preco_ref_usd_por_mi_entrada
        + (tokens_saida / UM_MILHAO) * s.preco_ref_usd_por_mi_saida
    )
    return CustoEquivalente(
        usd=usd,
        brl=usd * s.cotacao_usd_brl,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
    )


def por_conversa(tokens_entrada: int, tokens_saida: int, leads: int) -> CustoEquivalente:
    """Custo equivalente dividido pelo número de leads atendidos na janela.

    `leads` = 0 devolve o custo zerado em vez de estourar: janela sem
    atendimento é o estado normal de um painel recém-aberto, não um erro.
    """
    total = calcular(tokens_entrada, tokens_saida)
    if leads <= 0:
        return CustoEquivalente(0.0, 0.0, 0, 0)
    return CustoEquivalente(
        usd=total.usd / leads,
        brl=total.brl / leads,
        tokens_entrada=tokens_entrada // leads,
        tokens_saida=tokens_saida // leads,
    )
