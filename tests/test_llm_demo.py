"""Provedor de demonstração (core/llm_demo.py).

Roda sem Ollama e sem Postgres -- é o ponto inteiro do módulo.

O foco está nos erros que a extração por regra comete e um LLM não
cometeria. Três deles apareceram testando o fluxo real e estão fixados
aqui:

1. O prompt de extração carrega a PERGUNTA que o agente acabou de fazer.
   "Pergunte se o cliente quer comprar, alugar ou INVESTIR" fez
   "Estou procurando apartamento na zona sul" sair como investimento.
2. "sem pressa" contém "pressa": quem disse que podia esperar virava
   urgência alta.
3. "investir em imóveis para renda" tinha "imóveis para" extraído como
   região, e o roteiro seguia com lixo no slot.
"""

import pytest

from app.agents.qualifier import LeadQualification
from app.agents.scheduler_agent import EscolhaVisita
from app.core import llm_demo


def _prompt_qualificacao(mensagem: str, *, slots: dict | None = None, pergunta: str | None = None) -> str:
    """Reproduz o formato de `agents/qualifier.py::extrair_slots`."""
    cabecalho = f'Pergunta que acabou de ser feita ao cliente: "{pergunta}"\n\n' if pergunta else ""
    return f'{cabecalho}Dados ja coletados: {slots or {}}\n\nUltima mensagem do cliente: "{mensagem}"'


def _extrair(mensagem: str, **kwargs) -> LeadQualification:
    dados, _ = llm_demo.chat_structured(
        agente="qualifier", system="", user=_prompt_qualificacao(mensagem, **kwargs),
        schema=LeadQualification,
    )
    return dados


# ---------------------------------------------------------------- contaminação pelo prompt

def test_pergunta_pendente_nao_vira_intencao():
    """O bug nº 1 do docstring: a palavra "investir" estava na PERGUNTA."""
    r = _extrair(
        "Estou procurando apartamento na zona sul",
        pergunta="Pergunte se o cliente quer comprar, alugar ou investir em um imovel.",
    )
    assert r.intencao == "compra"
    assert r.regiao == "zona sul"


def test_slots_ja_coletados_nao_viram_extracao_nova():
    """`Dados ja coletados` também viaja no prompt."""
    r = _extrair("Ainda não sei", slots={"intencao": "aluguel", "regiao": "Campinas"})
    assert r.regiao is None


# ---------------------------------------------------------------- cenários do enunciado

def test_cenario_1_do_enunciado():
    r = _extrair("Estou procurando apartamento na zona sul")
    assert r.intencao == "compra"
    assert r.regiao == "zona sul"


def test_cenario_2_do_enunciado():
    r = _extrair("Quero investir em imóveis para renda")
    assert r.intencao == "investimento"
    # Bug nº 3: "imóveis para" não é região.
    assert r.regiao is None


# ---------------------------------------------------------------- urgência

@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("Tenho pressa, quero mudar logo", "alta"),
        ("É urgente", "alta"),
        ("Sem pressa, posso esperar", "baixa"),       # bug nº 2
        ("Não tenho pressa nenhuma", "baixa"),
        ("Nos próximos meses", "media"),
    ],
)
def test_urgencia(mensagem, esperado):
    assert _extrair(mensagem).urgencia == esperado


# ---------------------------------------------------------------- valores

@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("Até 600 mil", 600_000),
        ("R$ 450.000", 450_000),
        ("uns 350k", 350_000),
        ("1,2 milhão", 1_200_000),
    ],
)
def test_preco_em_varios_formatos(mensagem, esperado):
    assert _extrair(mensagem).faixa_preco_max == esperado


def test_numero_pequeno_nao_vira_preco():
    """"2 quartos" não é R$ 2."""
    r = _extrair("Quero 2 quartos")
    assert r.faixa_preco_max is None
    assert r.quartos == 2


def test_valor_vira_ticket_quando_a_intencao_ja_e_investimento():
    """A fala "Uns 400 mil" não diz de qual fluxo faz parte -- quem diz são
    os slots já coletados. Sem isso o roteiro ficava preso pedindo o ticket
    que o cliente acabara de informar."""
    r = _extrair("Uns 400 mil", slots={"intencao": "investimento"})
    assert r.ticket == 400_000
    assert r.faixa_preco_max is None


# ---------------------------------------------------------------- resposta curta ambígua

def test_numero_solto_responde_a_pergunta_pendente():
    r = _extrair("2", pergunta="Pergunte quantos quartos o cliente precisa.")
    assert r.quartos == 2


def test_numero_solto_como_preco_assume_milhares():
    r = _extrair("600", pergunta="Pergunte a faixa de preco que o cliente tem em mente.")
    assert r.faixa_preco_max == 600_000


# ---------------------------------------------------------------- quartos e região

@pytest.mark.parametrize(("mensagem", "esperado"), [("2 quartos", 2), ("três quartos", 3), ("1 dormitório", 1)])
def test_quartos(mensagem, esperado):
    assert _extrair(mensagem).quartos == esperado


@pytest.mark.parametrize("mensagem", ["Tenho 600 mil em mente", "No máximo isso", "Quero algo de valor justo"])
def test_palavra_apos_preposicao_nao_vira_regiao(mensagem):
    assert _extrair(mensagem).regiao is None


def test_cidade_apos_preposicao_vira_regiao():
    assert _extrair("Quero algo em Campinas").regiao == "campinas"


# ---------------------------------------------------------------- escolha de visita

def _escolha(mensagem: str) -> EscolhaVisita:
    dados, _ = llm_demo.chat_structured(
        agente="scheduler", system="", user=f'Mensagem do cliente: "{mensagem}"', schema=EscolhaVisita,
    )
    return dados


@pytest.mark.parametrize(("mensagem", "esperado"), [("Quero o 1", 1), ("o segundo", 2), ("gostei do terceiro", 3)])
def test_numero_escolhido(mensagem, esperado):
    assert _escolha(mensagem).numero_escolhido == esperado


def test_nome_com_maiuscula_e_acento():
    """Roda no texto ORIGINAL: sem IGNORECASE, "Meu nome é" não casava e o
    agendamento ficava preso pedindo o nome pra sempre."""
    assert _escolha("Meu nome é Wellson").nome == "Wellson"


def test_telefone():
    assert "98888" in _escolha("meu telefone é 11 98888-7777").telefone


def test_horario():
    assert "sabado" in _escolha("Sábado de manhã").horario_texto.lower()


# ---------------------------------------------------------------- texto humanizado

def test_frase_vem_da_acao_decidida_pelo_router():
    from app.core.prompts import INSTRUCOES_POR_ACAO

    texto, _ = llm_demo.chat_text(
        agente="responder", system="",
        user=f"O que fazer agora: {INSTRUCOES_POR_ACAO['perguntar_quartos']}",
    )
    assert "quarto" in texto.lower()


def test_instrucao_desconhecida_cai_no_generico():
    texto, _ = llm_demo.chat_text(agente="responder", system="", user="instrução que não existe")
    assert texto


# ---------------------------------------------------------------- embeddings e trace

def test_embedding_tem_a_dimensao_do_pgvector():
    assert len(llm_demo.embed_text("apartamento 2 quartos")) == llm_demo.DIMENSOES_EMBEDDING


def test_embedding_e_deterministico():
    """O mesmo imóvel precisa gerar o mesmo vetor no seed e na busca."""
    assert llm_demo.embed_text("cobertura") == llm_demo.embed_text("cobertura")


def test_embeddings_diferentes_para_textos_diferentes():
    assert llm_demo.embed_text("cobertura") != llm_demo.embed_text("kitnet")


def test_trace_nao_inventa_metrica_de_inferencia():
    """O painel mostra latência e tokens medidos. Modo demo não mediu
    inferência nenhuma, então não pode devolver número -- `None` vira "—"
    na tela, e o modelo aparece como `demo-rules`."""
    _, trace = llm_demo.chat_text(agente="responder", system="", user="qualquer coisa")
    assert trace.modelo == llm_demo.MODELO_DEMO
    assert trace.prompt_tokens is None
    assert trace.tokens_por_s is None
    assert trace.erro is None
