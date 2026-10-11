"""Provedor de demonstração: o projeto inteiro sem Ollama, sem GPU, sem modelo.

POR QUE EXISTE
--------------
O projeto roda com um modelo de 9B local, por Metal. Isso é a decisão
central da arquitetura e não muda. Mas tem um custo que não é técnico: quem
for AVALIAR precisa de um Mac com Apple Silicon, 9 GB de modelo em disco,
Docker e Postgres. Em Windows ou Linux sem GPU, não vê nada.

Com `LLM_DEMO=true`, `python -m app.channels.cli` e o chat web funcionam de
ponta a ponta -- qualificação, busca de imóveis, agendamento, follow-up,
resumo -- em qualquer máquina, sem instalar nada além das dependências
Python. As respostas são de regra, não de modelo: muito piores, e é esse o
ponto -- é demonstração de FLUXO, não de qualidade de redação.

O QUE ISTO NÃO É
----------------
Não é mock de teste. A suíte de integração continua batendo no Ollama real
(`tests/test_qualifier.py`, `tests/test_adversarial.py`), e isso é
deliberado: os bugs que mais custaram neste projeto foram de FORMATO -- o
`anyOf` que devolvia null em silêncio, a chave `"intenção"` com acento, o
`think: false` faltando. Nenhum deles apareceria contra um mock. Mockar o
LLM nos testes esconderia exatamente a classe de erro que mais deu
trabalho; ver `ARQUITETURA.md`.

Por isso o modelo é reportado como `demo-rules` em `agent_traces`: a aba de
observabilidade mostra latência e tokens de verdade quando o Ollama roda, e
não pode passar número inventado por medição.
"""

import hashlib
import re
import struct
import time
import unicodedata

from pydantic import BaseModel

from app.core.prompts import INSTRUCOES_POR_ACAO
from app.core.traces import TraceInfo

MODELO_DEMO = "demo-rules"

# Dimensão do nomic-embed-text. O vetor abaixo não tem semântica -- a busca
# híbrida continua funcionando porque o filtro estruturado (preço, quartos,
# bairro) é SQL puro e o ranqueamento textual é `ts_rank`; só a parcela
# vetorial (0.6 do score) vira ruído estável. Ver ARQUITETURA.md.
DIMENSOES_EMBEDDING = 768


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _trace(agente: str, inicio: float) -> TraceInfo:
    """Trace honesto: sem contagem de token, porque não houve inferência.
    `None` em `prompt_tokens`/`tokens_por_s` faz o painel mostrar "—" em vez
    de um número que não mediu nada."""
    return TraceInfo(
        agente=agente, modelo=MODELO_DEMO,
        prompt_tokens=None, completion_tokens=None,
        latencia_ms=int((time.monotonic() - inicio) * 1000),
        tokens_por_s=None, erro=None,
    )


# ---------------------------------------------------------------- embeddings

def embed_text(texto: str) -> list[float]:
    """Vetor determinístico derivado do SHA-256 do texto.

    Determinístico importa: o mesmo imóvel precisa gerar o mesmo vetor no
    seed e na busca, senão a distância vira aleatória a cada execução.
    Semântica não importa aqui -- texto parecido NÃO gera vetor parecido, e
    é uma limitação assumida do modo demo."""
    bruto = b""
    semente = texto.encode()
    while len(bruto) < DIMENSOES_EMBEDDING * 4:
        semente = hashlib.sha256(semente).digest()
        bruto += semente
    inteiros = struct.unpack(f"{DIMENSOES_EMBEDDING}i", bruto[: DIMENSOES_EMBEDDING * 4])
    # Normalizado pra ficar na mesma escala do nomic-embed-text; sem isso a
    # distância do pgvector sairia em outra ordem de grandeza.
    maior = max(abs(v) for v in inteiros) or 1
    return [v / maior for v in inteiros]


# ---------------------------------------------------------------- texto humanizado

# Frase pronta por ação do router. A chave é a MESMA instrução que
# `core/prompts.py` manda pro LLM, então um `INSTRUCOES_POR_ACAO` novo sem
# frase aqui cai no genérico em vez de quebrar.
_FRASE_POR_ACAO = {
    "perguntar_intencao": "Oi! Aqui é a Bia da Horizonte. Você está procurando pra comprar, alugar ou investir?",
    "perguntar_preco": "Perfeito, anotei. Qual faixa de preço você tem em mente?",
    "perguntar_quartos": "Ótimo. De quantos quartos você precisa?",
    "perguntar_regiao": "Anotado. Em qual cidade, região ou bairro de São Paulo você tem interesse?",
    "perguntar_urgencia": "Entendi. Você tem pressa pra fechar, ou pode esperar um pouco?",
    "buscar_imoveis": "Show, já tenho o que preciso. Vou buscar as melhores opções pra você agora.",
    "perguntar_perfil_investidor": (
        "Legal! Me conta: você busca renda mensal com aluguel, valorização a longo prazo, "
        "ou é a primeira vez investindo?"
    ),
    "perguntar_ticket": "Entendi. Quanto você pretende investir?",
    "perguntar_expectativa_retorno": "E que retorno você espera? Pode ser um percentual ao ano ou uma renda mensal.",
    "encaminhar_especialista": (
        "Perfeito. Vou te encaminhar pro nosso especialista em investimentos imobiliários, "
        "ele entra em contato com você."
    ),
}

# Reverso: da instrução que viaja no prompt de volta pra ação. Montado uma
# vez, no import.
_ACAO_POR_INSTRUCAO = {instrucao: acao for acao, instrucao in INSTRUCOES_POR_ACAO.items()}

_FRASE_GENERICA = "Certo, entendi. Me conta um pouco mais pra eu te ajudar melhor."


# O resumo pro corretor é o único texto do projeto que não é conversa: é
# ficha montada a partir de dado estruturado. Isso um provedor de regras
# faz BEM -- e faz melhor que cair na frase genérica, que numa captura de
# tela do painel pareceria defeito do produto.
_SLOTS_DO_RESUMO = re.compile(r"dados coletados do lead:\s*(\{.*?\})", re.IGNORECASE | re.DOTALL)
_STATUS_DO_RESUMO = re.compile(r"^status:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_DESFECHO_DO_RESUMO = re.compile(r"^desfecho:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

_VERBO_POR_INTENCAO = {"compra": "comprar", "aluguel": "alugar", "investimento": "investir"}


def _campo(bruto: str, nome: str) -> str | None:
    achado = re.search(rf"'{nome}':\s*(?:'([^']*)'|(\d+))", bruto)
    if not achado:
        return None
    return achado.group(1) or achado.group(2)


def _reais(valor: str | None) -> str | None:
    if not valor or not valor.isdigit():
        return None
    return f"R$ {int(valor):,}".replace(",", ".")


def _resumo_pro_corretor(prompt: str) -> str | None:
    """Ficha a partir dos slots que o próprio prompt carrega. `None` quando
    o prompt não é de resumo -- aí o chamador segue pro caminho normal."""
    achado = _SLOTS_DO_RESUMO.search(prompt)
    if not achado:
        return None
    slots = achado.group(1)

    intencao = _campo(slots, "intencao")
    verbo = _VERBO_POR_INTENCAO.get(intencao or "", "negociar")

    partes = []
    if intencao == "investimento":
        detalhes = [
            _campo(slots, "perfil_investidor"),
            (f"ticket de {t}" if (t := _reais(_campo(slots, "ticket"))) else None),
            (f"espera {r}" if (r := _campo(slots, "expectativa_retorno")) else None),
        ]
    else:
        detalhes = [
            (f"{q} quartos" if (q := _campo(slots, "quartos")) else None),
            _campo(slots, "regiao"),
            (f"até {p}" if (p := _reais(_campo(slots, "faixa_preco_max"))) else None),
        ]
    presentes = [d for d in detalhes if d]
    partes.append(f"Cliente quer {verbo}" + (f": {', '.join(presentes)}." if presentes else "."))

    if urgencia := _campo(slots, "urgencia"):
        partes.append(f"Urgência {urgencia}.")

    if desfecho := _DESFECHO_DO_RESUMO.search(prompt):
        partes.append(desfecho.group(1).strip().rstrip(".") + ".")
    elif (status := _STATUS_DO_RESUMO.search(prompt)) and status.group(1) == "aguardando_escolha":
        partes.append("Escolhendo o imóvel pra visita.")

    return " ".join(partes)


def chat_text(*, agente: str, system: str, user: str, **_ignorado) -> tuple[str, TraceInfo]:
    """Devolve a frase pronta da ação que o router decidiu.

    A ação não vem por parâmetro -- `core/turno.py` a transforma em texto
    ("O que fazer agora: ...") antes de chamar. Em vez de mudar a
    assinatura só por causa do modo demo, o caminho de volta é procurar
    qual instrução conhecida aparece no prompt. Sem alteração nenhuma no
    fluxo real."""
    inicio = time.monotonic()
    if resumo := _resumo_pro_corretor(user):
        return resumo, _trace(agente, inicio)
    for instrucao, acao in _ACAO_POR_INSTRUCAO.items():
        if instrucao in user and acao in _FRASE_POR_ACAO:
            return _FRASE_POR_ACAO[acao], _trace(agente, inicio)
    return _FRASE_GENERICA, _trace(agente, inicio)


# ---------------------------------------------------------------- extração estruturada

_INTENCAO = (
    ("investimento", ("investir", "investimento", "investidor", "renda mensal", "rentabilidade", "para renda")),
    ("aluguel", ("alugar", "aluguel", "locacao", "de aluguel")),
    ("compra", ("comprar", "compra", "adquirir", "a venda", "financiar", "procurando apartamento", "procuro apartamento")),
)

# Ordem é regra, não estilo: "baixa" vem primeiro porque suas frases
# CONTÊM as de "alta" -- "sem pressa" casaria com o gatilho "pressa" e o
# cliente que disse que pode esperar viraria lead quente.
_URGENCIA = (
    ("baixa", ("sem pressa", "nao tenho pressa", "posso esperar", "mais pra frente",
               "ano que vem", "so olhando", "sem urgencia", "nao e urgente")),
    ("media", ("alguns meses", "medio prazo", "proximos meses", "ate o fim do ano", "nao tao rapido")),
    ("alta", ("urgente", "urgencia", "pressa", "o quanto antes", "pra ontem", "imediato",
              "mudar logo", "fechar logo", "assim que possivel", "esse mes")),
)

_ZONAS = ("zona sul", "zona norte", "zona leste", "zona oeste", "centro")

# "600 mil", "R$ 600.000", "1,2 milhao", "600k"
_VALOR = re.compile(
    r"(?:r\$\s*)?(\d{1,3}(?:[.\s]\d{3})+|\d+(?:[.,]\d+)?)\s*(milhoes|milhao|mi\b|mil\b|k\b)?",
    re.IGNORECASE,
)


def _valores(texto: str) -> list[int]:
    """Valores em reais citados no texto, já normalizados."""
    achados = []
    for numero, escala in _VALOR.findall(texto):
        limpo = numero.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            valor = float(limpo)
        except ValueError:
            continue
        escala = (escala or "").lower()
        if escala in ("milhoes", "milhao", "mi"):
            valor *= 1_000_000
        elif escala in ("mil", "k"):
            valor *= 1_000
        # Descarta número pequeno sem escala: "2 quartos" não é preço.
        if valor >= 10_000:
            achados.append(int(valor))
    return achados


_QUARTOS_POR_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5}
_QUARTOS = re.compile(r"(\d+|um|uma|dois|duas|tres|quatro|cinco)\s*(?:quarto|dormitorio|dorm\b|suite)")


def _quartos(texto: str) -> int | None:
    achado = _QUARTOS.search(texto)
    if not achado:
        return None
    bruto = achado.group(1)
    return int(bruto) if bruto.isdigit() else _QUARTOS_POR_EXTENSO.get(bruto)


_REGIAO_APOS_PREPOSICAO = re.compile(r"\b(?:em|na|no|bairro de|cidade de|regiao de)\s+([a-z]{4,}(?:\s+[a-z]{4,})?)")
# Palavras que seguem "em/na/no" sem serem lugar -- sem isto, "em mente"
# e "no maximo" viravam região e o roteiro seguia com lixo no slot.
_NAO_E_REGIAO = {
    "mente", "maximo", "minimo", "torno", "media", "geral", "tudo", "conta",
    "casa", "casas", "apartamento", "apartamentos", "imovel", "imoveis",
    "valor", "valores", "preco", "precos", "quartos", "breve", "renda",
    "dinheiro", "maos", "aberto", "ideia", "vista", "frente", "aluguel",
    "investimento", "investimentos", "familia", "obras", "reforma",
}


def _regiao(texto: str) -> str | None:
    for zona in _ZONAS:
        if zona in texto:
            return zona
    achado = _REGIAO_APOS_PREPOSICAO.search(texto)
    if not achado:
        return None
    candidato = achado.group(1).strip()
    if candidato.split()[0] in _NAO_E_REGIAO:
        return None
    return candidato


_PERFIL_INVESTIDOR = (
    ("renda com aluguel", ("renda", "alugar", "aluguel", "mensal")),
    ("valorizacao a longo prazo", ("valorizacao", "longo prazo", "valorizar")),
    ("iniciante", ("primeira vez", "comecando", "iniciante", "nunca investi")),
)

_RETORNO = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _extrair_qualificacao(texto: str, intencao_ja_conhecida: str | None = None) -> dict:
    """`intencao_ja_conhecida` vem dos slots que o prompt carrega.

    Sem ela, "Uns 400 mil" respondido no fluxo de INVESTIMENTO caía em
    `faixa_preco_max` em vez de `ticket` -- a fala sozinha não diz de qual
    fluxo ela faz parte, e o roteiro ficava preso pedindo o ticket que o
    cliente acabara de dar."""
    normalizado = _normalizar(texto)
    dados: dict = {}

    for intencao, gatilhos in _INTENCAO:
        if any(g in normalizado for g in gatilhos):
            dados["intencao"] = intencao
            break

    # Vale pros ramos abaixo (ticket vs preço, perfil de investidor), mas
    # NÃO é gravado: quem decide trocar a intenção é o merge do qualifier.
    intencao_ativa = dados.get("intencao") or intencao_ja_conhecida

    if (regiao := _regiao(normalizado)) is not None:
        dados["regiao"] = regiao
    if (quartos := _quartos(normalizado)) is not None:
        dados["quartos"] = quartos

    for urgencia, gatilhos in _URGENCIA:
        if any(g in normalizado for g in gatilhos):
            dados["urgencia"] = urgencia
            break

    valores = _valores(normalizado)
    if valores:
        # Investimento fala em ticket; compra/aluguel, em teto de preço.
        if intencao_ativa == "investimento":
            dados["ticket"] = max(valores)
        else:
            dados["faixa_preco_max"] = max(valores)
            if len(valores) > 1:
                dados["faixa_preco_min"] = min(valores)

    if intencao_ativa == "investimento":
        for perfil, gatilhos in _PERFIL_INVESTIDOR:
            if any(g in normalizado for g in gatilhos):
                dados["perfil_investidor"] = perfil
                break
        if (retorno := _RETORNO.search(normalizado)) is not None:
            dados["expectativa_retorno"] = f"{retorno.group(1)}% ao ano"

    return dados


_ORDINAIS = {"primeiro": 1, "primeira": 1, "segundo": 2, "segunda": 2, "terceiro": 3, "terceira": 3}
_NUMERO_SOLTO = re.compile(r"\b([123])\b")
# IGNORECASE e `[eé]` porque este regex roda no texto ORIGINAL, não no
# normalizado: o nome precisa sair com acento e maiúscula preservados. Sem
# os dois, "Meu nome é Wellson" não casava e o agendamento ficava preso
# pedindo o nome pra sempre.
_NOME = re.compile(
    r"(?:meu nome [eé]|me chamo|sou o|sou a|aqui [eé] o|aqui [eé] a)\s+([a-zA-ZÀ-ÿ]+(?:\s+[a-zA-ZÀ-ÿ]+)?)",
    re.IGNORECASE,
)
_TELEFONE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?[\s.-]*)?9?\d{4}[\s.-]?\d{4}")
_HORARIO = re.compile(
    r"((?:segunda|terca|quarta|quinta|sexta|sabado|domingo|amanha|hoje|depois de amanha|dia\s+\d{1,2})"
    r"(?:[^.,;!?]{0,30})?(?:manha|tarde|noite|\d{1,2}\s*(?:h|horas|:\d{2}))?)",
    re.IGNORECASE,
)


def _extrair_escolha_visita(texto: str) -> dict:
    normalizado = _normalizar(texto)
    dados: dict = {}

    for ordinal, numero in _ORDINAIS.items():
        if ordinal in normalizado:
            dados["numero_escolhido"] = numero
            break
    else:
        if (achado := _NUMERO_SOLTO.search(normalizado)) is not None:
            dados["numero_escolhido"] = int(achado.group(1))

    if (horario := _HORARIO.search(normalizado)) is not None:
        dados["horario_texto"] = horario.group(1).strip()
    # Nome e telefone saem do texto ORIGINAL: acento e maiúscula fazem parte
    # do nome, e o normalizado já os perdeu.
    if (nome := _NOME.search(texto)) is not None:
        dados["nome"] = nome.group(1).strip()
    if (telefone := _TELEFONE.search(texto)) is not None:
        dados["telefone"] = telefone.group(0).strip()

    return dados


# Por nome da classe, não por import: `llm_demo` é importado por `llm.py`, e
# importar os agentes aqui fecharia um ciclo (agents -> llm -> llm_demo ->
# agents).
_EXTRATORES = {
    "LeadQualification": _extrair_qualificacao,
    "EscolhaVisita": _extrair_escolha_visita,
}

# O prompt de extração não é só a fala do cliente: carrega os slots já
# coletados e a pergunta que o agente acabou de fazer. Para um LLM isso é
# contexto; para um extrator de palavra-chave é contaminação.
#
# Custou um bug real: "Estou procurando apartamento na zona sul" saiu como
# `intencao=investimento`, porque a pergunta pendente no prompt era
# "Pergunte se o cliente quer comprar, alugar ou INVESTIR em um imóvel" --
# a regra casou com o texto do próprio agente.
_FALA_DO_LEAD = re.compile(r'(?:ultima )?mensagem do cliente:\s*"(.*)"\s*$', re.IGNORECASE | re.DOTALL)
_PERGUNTA_PENDENTE = re.compile(r'pergunta que acabou de ser feita ao cliente:\s*"(.*?)"', re.IGNORECASE)
_INTENCAO_COLETADA = re.compile(r"'intencao':\s*'(compra|aluguel|investimento)'", re.IGNORECASE)


def _intencao_ja_conhecida(prompt: str) -> str | None:
    """Lê `Dados ja coletados: {...}` do prompt. Regex e não
    `ast.literal_eval` de propósito: o dict vem de um f-string e um
    campo com aspas quebraria o parse inteiro por um dado só."""
    achado = _INTENCAO_COLETADA.search(prompt)
    return achado.group(1).lower() if achado else None


def _isolar_fala_do_lead(prompt: str) -> str:
    """Só o que o cliente escreveu. Sem o marcador, devolve o prompt inteiro
    -- degradar é melhor que extrair vazio."""
    achado = _FALA_DO_LEAD.search(prompt.strip())
    return achado.group(1) if achado else prompt


def _numero_solto_pela_pergunta(fala: str, prompt: str) -> dict:
    """Resposta curta só com número ("2", "600 mil") é ambígua sozinha: o
    que diz se é quartos ou preço é a pergunta que o agente acabou de
    fazer. Mesmo motivo pelo qual `qualifier.py` manda a pergunta pendente
    pro LLM -- aqui ela serve ao extrator de regra."""
    pergunta = _PERGUNTA_PENDENTE.search(prompt)
    if not pergunta or not re.fullmatch(r"[\d\s.,]+(?:mil|k|milhao|milhoes)?", _normalizar(fala)):
        return {}
    texto_pergunta = _normalizar(pergunta.group(1))
    if "quantos quartos" in texto_pergunta:
        numeros = re.findall(r"\d+", fala)
        return {"quartos": int(numeros[0])} if numeros else {}
    if "faixa de preco" in texto_pergunta or "pretende investir" in texto_pergunta:
        valores = _valores(_normalizar(fala))
        if not valores:
            # "600" sozinho respondendo preço significa 600 mil, não R$ 600.
            numeros = re.findall(r"\d+", fala)
            valores = [int(numeros[0]) * 1_000] if numeros else []
        if valores:
            campo = "ticket" if "pretende investir" in texto_pergunta else "faixa_preco_max"
            return {campo: max(valores)}
    return {}


def chat_structured[T: BaseModel](
    *, agente: str, system: str, user: str, schema: type[T], **_ignorado
) -> tuple[T, TraceInfo]:
    """Preenche o schema por regra. Schema desconhecido devolve a instância
    só com os defaults -- degrada pro fluxo "nada extraído neste turno", que
    o orquestrador já sabe tratar, em vez de estourar."""
    inicio = time.monotonic()
    extrator = _EXTRATORES.get(schema.__name__)
    if not extrator:
        return schema.model_validate({}), _trace(agente, inicio)

    fala = _isolar_fala_do_lead(user)
    if schema.__name__ == "LeadQualification":
        dados = extrator(fala, _intencao_ja_conhecida(user))
    else:
        dados = extrator(fala)
    # Só completa o que a fala sozinha não resolveu.
    for campo, valor in _numero_solto_pela_pergunta(fala, user).items():
        dados.setdefault(campo, valor)
    return schema.model_validate(dados), _trace(agente, inicio)
