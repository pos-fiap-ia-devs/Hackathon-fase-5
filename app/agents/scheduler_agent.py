"""Agendamento de visita (cenario 1) e encaminhamento a especialista
(cenario 2) -- os dois desfechos sao diferentes, secao 5.9 do PLANO.md.

Sem calendario real integrado -- fora de escopo do POC (base de imoveis ja
e simulada; agenda de corretor seria o mesmo tipo de simulacao). A visita
recebe um horario util deterministico (`proximo_horario_util`); o que o
cliente disse em texto livre ("sabado de tarde") fica preservado em
`visitas.horario_solicitado`, e a confirmacao ao cliente cita as DUAS coisas
-- nunca finge que o texto livre virou uma agenda real.
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.core.llm import chat_structured
from app.core.traces import TraceInfo

CORRETORES = ["Ana Ribeiro", "Carlos Mendes", "Fernanda Souza"]


class EscolhaVisita(BaseModel):
    numero_escolhido: int | None = None
    horario_texto: str | None = None
    nome: str | None = None
    telefone: str | None = None


SYSTEM_ESCOLHA = """Voce esta lendo a resposta de um cliente durante o \
agendamento de uma visita a um imovel (ele ja viu ate 3 opcoes numeradas \
1, 2 e 3, e o agente pode ja ter pedido nome/telefone tambem).

Extraia:
- numero_escolhido: qual numero (1, 2 ou 3) ele escolheu. So preencha se \
ele claramente indicou um numero ou disse "o primeiro"/"o segundo"/"o \
terceiro"/"esse ultimo", etc.
- horario_texto: quando ele quer visitar, EXATAMENTE como ele disse (ex: \
"amanha de manha", "sabado a tarde"). So preencha se ele mencionar horario/dia.
- nome: o nome do cliente, SO se a mensagem for claramente uma \
apresentacao pessoal (ex: "meu nome e...", "sou o/a...", ou so um nome \
isolado). Nunca confunda com nome de bairro/imovel que o cliente esteja \
mencionando (ex: "pedro ludovico" pode ser um BAIRRO, nao uma pessoa).
- telefone: o telefone do cliente, so os numeros/formato que ele mandou, \
se ele disser.

Nao invente nada que ele nao disse. Campo nao mencionado = null."""


def extrair_escolha(mensagem: str) -> tuple[EscolhaVisita, TraceInfo]:
    return chat_structured(
        agente="scheduler",
        system=SYSTEM_ESCOLHA,
        user=f'Mensagem do cliente: "{mensagem}"',
        schema=EscolhaVisita,
    )


def proximo_horario_util(dias: int = 2, hora: int = 10) -> datetime:
    """Placeholder deterministico -- ver docstring do modulo.

    UTC-aware (`datetime.now(UTC)`), nao naive: a coluna e `timestamptz`
    com o Postgres deste projeto em `TimeZone=Etc/UTC` -- naive local
    (achado real em `core/followup.py`, mesma classe de bug) gravaria um
    horario deslocado pelo fuso da maquina."""
    alvo = datetime.now(UTC) + timedelta(days=dias)
    return alvo.replace(hour=hora, minute=0, second=0, microsecond=0)


def escolher_corretor(lead_id: int) -> str:
    """Distribuicao redonda-robin simples, sem estado externo -- suficiente
    pro POC ("distribuir entre corretores" nao e requisito do PDF)."""
    return CORRETORES[lead_id % len(CORRETORES)]


class NovaPreferenciaHorario(BaseModel):
    horario_texto: str | None = None


SYSTEM_REAGENDAR = """O cliente ja tem uma visita marcada e pode estar \
mencionando um novo dia ou horario pra essa visita (ex: "dia 22", "pode \
ser sexta de manha", "aquele dia nao vai dar, prefiro sabado").

Extraia horario_texto: o dia/data/horario/periodo que o cliente mencionou \
NESTA mensagem, exatamente como ele disse. Se a mensagem nao mencionar \
nenhum dia, data ou horario (so pergunta, agradecimento, ou pede pra \
reagendar sem dizer quando), deixe null. Nao invente."""


def extrair_reagendamento(mensagem: str) -> tuple[NovaPreferenciaHorario, TraceInfo]:
    """So extrai o que ESTA mensagem diz -- nao pede pro LLM decidir se e
    correcao ou substituicao total nem gerar a frase ja combinada com o
    valor anterior. Essa versao (com o valor atual no prompt, pedindo
    "combine numa frase coerente") falhou 2 de 2 vezes exatamente no caso
    de substituicao total ("pode ser sexta que vem, de manha" depois de
    "tarde" voltou null) -- mesma licao de sempre: tarefa generativa
    composta (julgar + escrever) e menos confiavel que extracao simples.
    A decisao de mesclar ou substituir agora e de `_combinar_horario`,
    em codigo, no core/turno.py."""
    return chat_structured(
        agente="scheduler",
        system=SYSTEM_REAGENDAR,
        user=f'Mensagem do cliente: "{mensagem}"',
        schema=NovaPreferenciaHorario,
    )
