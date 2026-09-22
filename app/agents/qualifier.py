"""Extracao de slots (secao 5.1 do PLANO.md). Chamada 1 das "duas por turno" (5.2).

Sistema-prompt validado manualmente contra o servidor real, em duas rodadas:

1. A primeira versao ("mencao a intencao...") classificou errado o cenario 1
   do PDF ("Estou procurando apartamento" virou aluguel em vez de compra).
   A versao abaixo, com regra explicita por padrao de frase, acerta os 3
   cenarios do enunciado.
2. Adicionar uma regra de 4 linhas para desambiguar preco ("Regra para
   preco -- ambiguidade real...") quebrou a extracao de `regiao` em 6/6
   testes, mesmo regiao nao tendo nada a ver com preco -- o modelo parece
   ter um orcamento de atencao curto: mais paragrafo de instrucao, pior a
   extracao de campos nao relacionados aquele paragrafo. A regra de preco
   final e 1 linha soldada ao final da frase sobre valores, nao um bloco
   proprio -- mesmo efeito pratico, sem competir por atencao.
"""

from typing import Literal

from pydantic import BaseModel

from app.core.llm import chat_structured
from app.core.traces import TraceInfo


class LeadQualification(BaseModel):
    """Ordem dos campos importa de verdade -- nao e so estetica.

    Testado manualmente contra o servidor real: com `regiao` mais perto do
    fim do schema (depois dos campos de preco), a mesma mensagem
    ("Estou procurando apartamento na zona sul.") devolvia `regiao: null`.
    Movendo `regiao` para logo depois de `intencao`, o mesmo modelo, mesmo
    prompt, mesma mensagem, extrai corretamente. O grammar-constrained
    decoding do llama.cpp parece "gastar atencao" nos primeiros campos --
    entao os campos mais citados nas frases curtas do enunciado (regiao,
    quartos, preco) vem logo depois de intencao; os de investimento, que so
    aparecem em mensagens mais longas e explicitas, vem por ultimo.

    `description` por campo foi removida de proposito -- ver `_simplificar_schema`
    em core/llm.py, achado documentado la.
    """

    intencao: Literal["compra", "aluguel", "investimento", "indefinido"] = "indefinido"
    regiao: str | None = None
    quartos: int | None = None
    faixa_preco_min: int | None = None
    faixa_preco_max: int | None = None
    urgencia: Literal["alta", "media", "baixa"] | None = None
    perfil_investidor: str | None = None
    ticket: int | None = None
    expectativa_retorno: str | None = None


SYSTEM_PROMPT = """Voce e um extrator de dados para qualificacao de leads de uma \
imobiliaria, que atua em compra, aluguel e investimento de imoveis.

Regras para o campo intencao:
- mencao a comprar, apartamento a venda, ou so "procurando apartamento" sem \
falar em alugar -> compra
- mencao a alugar, aluguel, morar de aluguel -> aluguel
- mencao a investir, imovel para renda, retorno, rentabilidade -> investimento
- se realmente nao der pra saber -> indefinido

Outras regras:
- Leia o estado atual dos dados ja coletados e a ultima mensagem do cliente.
- Devolva so os campos que a ultima mensagem confirma ou corrige.
- Nunca invente valor que o cliente nao disse. Campo nao mencionado = null.
- faixa_preco_min/max e ticket sao valores em reais (numero inteiro, sem "R$"). \
Se intencao for compra/aluguel, um valor de preco unico vai em faixa_preco_max \
(faixa_preco_min so com piso explicito, tipo "a partir de"). Se intencao for \
investimento, um valor de preco unico vai em ticket, nunca em faixa_preco_max. \
Excecao: se o cliente pedir para CONFIRMAR o preco de um imovel especifico \
(ex: "confirma que custa X?", "esse aqui e X mesmo?"), isso NAO e o orcamento \
dele -- devolva faixa_preco_min/max e ticket como null, sem alterar valor \
ja coletado.
- urgencia: "preciso rapido"/"esse mes"/"urgente" -> alta; "sem pressa"/"nao \
tenho pressa"/"pode ser mais pra frente" -> baixa; algo no meio (ex: "nos \
proximos meses") -> media.
"""


def extrair_slots(
    mensagem: str, slots_atuais: dict, *, pergunta_pendente: str | None = None
) -> tuple[LeadQualification, TraceInfo]:
    """`pergunta_pendente` (opcional): qual pergunta o agente acabou de
    fazer ao cliente (texto de `core/prompts.py::INSTRUCOES_POR_ACAO`).

    Achado real: sem esse contexto, uma resposta curta e ambigua tipo "2"
    (resposta a "quantos quartos?") voltou com `quartos: null` -- o
    extrator so ve os slots ja preenchidos e a mensagem, sem saber QUAL
    pergunta gerou essa resposta, entao nao tem como saber que "2" e
    quartos e nao, por exemplo, um numero de imovel ou algo do preco. A
    resposta de texto (gerada depois, com a mesma mensagem) alucinou
    "2 quartos, ótimo tamanho!" como se tivesse entendido -- mas o dado
    nunca foi pro slot, o fluxo ficou preso silenciosamente. Passar a
    pergunta pendente fecha essa ambiguidade sem pedir pro LLM adivinhar."""
    contexto_pergunta = f'Pergunta que acabou de ser feita ao cliente: "{pergunta_pendente}"\n\n' if pergunta_pendente else ""
    user = f'{contexto_pergunta}Dados ja coletados: {slots_atuais}\n\nUltima mensagem do cliente: "{mensagem}"'
    return chat_structured(
        agente="qualifier",
        system=SYSTEM_PROMPT,
        user=user,
        schema=LeadQualification,
    )


def merge_slots(atuais: dict, novo: LeadQualification) -> dict:
    """Valor novo so sobrescreve None -- exceto quando o lead corrige
    explicitamente um campo ja preenchido, caso em que o proprio LLM devolve
    o novo valor (schema nao tem como "nao mencionar" via null nesse caso).

    Excecao: `intencao` e sempre preenchida pelo schema (Literal obrigatorio).
    Quando a mensagem atual nao fala de intencao, o modelo devolve
    "indefinido" -- isso nao pode apagar uma intencao ja conhecida.
    """
    resultado = dict(atuais)
    dados = novo.model_dump()

    for campo, valor in dados.items():
        if valor is None:
            continue
        if campo == "intencao" and valor == "indefinido" and resultado.get("intencao") not in (None, "indefinido"):
            continue
        resultado[campo] = valor

    return resultado
