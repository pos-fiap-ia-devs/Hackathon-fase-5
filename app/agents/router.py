"""Roteador de intencao/estado (secao 5.2 e 5.9 do PLANO.md).

Decisao 100% em codigo, sem chamada de LLM -- e o que a secao 5.2 quer dizer
com "fluxo de conversa controlado em codigo; texto controlado pelo LLM".
`decidir_proximo` so olha os slots ja extraidos e devolve um rotulo; quem
transforma esse rotulo em pergunta humanizada e o agente `responder`
(core/prompts.py + core/llm.chat_text), na Etapa 4.

Ordem das perguntas segue literalmente os dois cenarios do PDF:
  cenario 1 (compra/aluguel): preco -> quartos -> regiao -> urgencia -> reuniao
  cenario 2 (investimento): perfil -> ticket -> retorno -> especialista
"""

import re
import unicodedata

# Pedido explicito pra ver o portfolio. Achado em uso real: o cliente disse
# "Quais tem hoje para mim com essa configuracao de 2 quartos" e o agente
# respondeu "qual regiao voce prefere?" -- insistiu no roteiro em vez de
# atender o pedido direto. O PDF nao cobre esse caso (no exemplo dele o
# cliente ja da a regiao na primeira frase), mas "conversa natural" e
# "fluxo humanizado" sao requisitos funcionais explicitos: um SDR de
# verdade mostra o que tem e refina depois, nao trava o cliente no script.
_PEDIDOS_VER_OPCOES = (
    "quais tem",
    "que tem",
    "o que tem",
    "tem alguma",
    "tem algum",
    "tem disponivel",
    "disponiveis",
    "me mostra",
    "me mostre",
    "mostra ai",
    "pode mostrar",
    "quero ver",
    "queria ver",
    "gostaria de ver",
    "ver as opcoes",
    "ver opcoes",
    "quais opcoes",
    "me manda",
    "manda as opcoes",
    "ver imoveis",
    "ver os imoveis",
)


# Objecoes classicas de venda imobiliaria, agrupadas por tipo -- o tipo
# importa porque a tratativa muda (preco pede valor/beneficio; terceiro
# pede acolher a decisao conjunta; concorrencia pede diferencial sem
# desqualificar o outro corretor).
#
# Deteccao em CODIGO, nao no prompt: achado testando as 3 objecoes reais
# -- com a regra so no system prompt, ela competia com a instrucao do
# turno ("Pergunte quantos quartos", que vem na mensagem do usuario, mais
# saliente) e perdia. "Ja tenho corretor" virava um "Entendido!" seco
# seguido da pergunta do roteiro. Detectando aqui, a tratativa vira a
# instrucao PRINCIPAL do turno, nao uma regra passiva.
_OBJECOES: dict[str, tuple[str, ...]] = {
    "preco": (
        "ta caro", "esta caro", "muito caro", "caro demais", "achei caro",
        "fora do meu orcamento", "acima do que eu queria", "nao tenho esse valor",
        "mais barato", "condominio caro",
    ),
    "adiar": (
        "vou pensar", "preciso pensar", "pensar melhor", "depois eu vejo",
        "nao e o momento", "nao e uma boa hora", "mais pra frente",
        "ano que vem", "sem pressa agora", "deixa pra depois",
    ),
    "terceiro": (
        "falar com minha esposa", "falar com meu marido", "falar com minha mulher",
        "falar com meu esposo", "conversar com minha familia", "falar com meus pais",
        "decidir com", "ver com minha esposa", "ver com meu marido",
    ),
    "concorrencia": (
        "ja tenho corretor", "ja tenho um corretor", "outro corretor",
        "outra imobiliaria", "ja estou vendo com", "ja estou sendo atendido",
        "ja tenho alguem",
    ),
    "desconfianca": (
        "nao conheco voces", "nao conheco a imobiliaria", "e confiavel",
        "e golpe", "nao sei se confio",
    ),
}

# Como tratar cada tipo -- vira a instrucao do turno (core/turno.py).
# Nenhuma delas autoriza inventar dado: o guardrail de "so cite o que ja
# esta no contexto" continua valendo (secao 5.6 do PLANO.md).
TRATATIVA_POR_OBJECAO = {
    "preco": (
        "O cliente achou caro. Acolha sem rebater, deixe claro que da pra ajustar a "
        "busca pro orcamento dele, e NAO invente desconto nem compare com imovel que "
        "ninguem mencionou."
    ),
    "adiar": (
        "O cliente quer adiar a decisao. Acolha (e uma decisao importante mesmo), "
        "deixe a porta aberta sem pressionar, e ofereca continuar a conversa no ritmo dele."
    ),
    "terceiro": (
        "O cliente precisa decidir junto com outra pessoa. Acolha como algo natural e "
        "positivo, e ofereca deixar tudo organizado pros dois avaliarem juntos."
    ),
    "concorrencia": (
        "O cliente ja e atendido por outro corretor. NUNCA desqualifique o concorrente. "
        "Acolha, e diga que pode mostrar opcoes sem compromisso pra ele comparar."
    ),
    # Achado testando: com "nao invente premio/depoimento" o modelo ainda
    # inventou credencial ("somos uma imobiliaria local com anos de
    # mercado"). Proibir sozinho nao basta -- precisa dar uma saida
    # concreta, senao o modelo preenche o vazio. Aqui a saida e trocar
    # "prove que e confiavel" por "nao precisa confiar ainda, veja sem
    # compromisso", que nao depende de nenhum dado que a gente nao tem.
    "desconfianca": (
        "O cliente esta inseguro sobre a imobiliaria. Acolha a preocupacao e responda "
        "SEM falar de tempo de mercado, tamanho, premio ou numero de clientes -- voce "
        "nao tem esses dados e nao pode inventar. Em vez disso, tire a pressao: ele "
        "pode ver as opcoes sem compromisso e decidir depois."
    ),
}


# "Quais bairros tem com 2 quartos" -- pergunta legitima e recorrente logo
# depois do agente perguntar a regiao. Sem isso o fluxo so sabia
# REPERGUNTAR a regiao, e o modelo respondia "tem preferencia por algum
# desses bairros?" sem listar nenhum (visto no Telegram real).
_PEDIDOS_LISTA_REGIOES = (
    "quais bairros",
    "que bairros",
    "quais sao os bairros",
    # Com 10 cidades no estoque, "quais cidades" virou a forma mais natural
    # de perguntar isso -- achado testando o seed de Sao Paulo.
    "quais cidades",
    "que cidades",
    "quais sao as cidades",
    "em quais cidades",
    "quais localidades",
    "quais regioes",
    "que regioes",
    "quais zonas",
    "que zonas",
    "quais lugares",
    "em quais bairros",
    "onde tem",
    "onde voces tem",
    "tem em quais",
    "quais opcoes de bairro",
    "me diz os bairros",
)


def pediu_lista_de_regioes(mensagem: str | None) -> bool:
    """Cliente quer saber ONDE existe imovel, nao ver os imoveis ainda."""
    if not mensagem:
        return False
    normalizada = _normalizar(mensagem)
    return any(pedido in normalizada for pedido in _PEDIDOS_LISTA_REGIOES)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


_PERGUNTAS_QUEM_E = (
    "com quem",
    "quem e voce",
    "quem esta falando",
    "quem fala",
    "qual seu nome",
    "qual e o seu nome",
    "seu nome",
    "voce e um robo",
    "e um bot",
    "e humano",
)


def perguntou_quem_e(mensagem: str | None) -> bool:
    """Cliente perguntou com quem esta falando -- unico caso, fora da
    primeira mensagem, em que dizer o proprio nome e legitimo. Usado pra
    liberar o guardrail de autorreferencia (core/llm.py)."""
    if not mensagem:
        return False
    normalizada = _normalizar(mensagem)
    return any(p in normalizada for p in _PERGUNTAS_QUEM_E)


# Despedida. Achado no Telegram real: com a visita ja marcada, o cliente
# mandou "Ok" e depois "Obrigado" pra encerrar -- e o agente repetiu a
# confirmacao da visita nas duas vezes, porque o ramo pos-desfecho so
# sabia reafirmar o fato. Nao havia nenhum ponto do fluxo que entendesse
# "acabou".
#
# Frases que ENCERRAM sozinhas, independente do tamanho da mensagem.
_DESPEDIDAS_EXPLICITAS = (
    "era so isso",
    "era isso mesmo",
    "so isso mesmo",
    "pode encerrar",
    "pode finalizar",
    "tchau",
    "ate logo",
    "ate mais",
    "ate a proxima",
    "ate breve",
    "bom final de semana",
    "boa semana",
    "tenha um otimo dia",
    "tenha uma otima",
    "otimo dia pra voce",
    "isso resolve",
    "resolvido",
    "entendi tudo",
    "nao preciso de mais nada",
    "so precisava disso",
    "por enquanto e so",
)

# Tokens de agradecimento/fechamento informal. Sozinhos NAO encerram: so
# valem numa mensagem curta. "Obrigado" (1 palavra) encerra; "obrigado,
# mas prefiro 3 quartos na verdade" continua a conversa -- e por isso que
# tem limite de tamanho em vez de match solto (`_MAX_PALAVRAS_DESPEDIDA`).
_TOKENS_DESPEDIDA = (
    "obrigado",
    "obrigada",
    "obg",
    "valeu",
    "vlw",
    "agradeco",
    "agradecido",
    "agradecida",
    "grato",
    "grata",
    "falou",
    "fechou",
    "flw",
    "abraco",
    "abracos",
    "boa noite",
    "bom dia",
    "boa tarde",
)

_MAX_PALAVRAS_DESPEDIDA = 6

# Aceite curto: "Ok", "certo", "beleza". NAO e despedida -- o cliente so
# esta acusando recebimento. Achado no mesmo print: com a visita ja
# marcada, um "Ok" fazia o agente repetir a confirmacao INTEIRA (imovel,
# corretor, data). Repetir bloco de fato pra quem so disse "ok" e o tique
# de robo que a secao 5.3 pede pra evitar.
_ACEITES_CURTOS = (
    "ok", "okay", "oke", "certo", "beleza", "blz", "entendi", "entendido",
    "perfeito", "combinado", "isso", "isso mesmo", "ta bom", "tudo bem",
    "legal", "bacana", "otimo", "show",
)

_MAX_PALAVRAS_ACEITE = 3


def eh_aceite_curto(mensagem: str | None) -> bool:
    """Mensagem que so acusa recebimento, sem pergunta nem pedido."""
    if not mensagem:
        return False
    normalizada = _normalizar(mensagem)
    if "?" in normalizada:
        return False
    palavras = re.sub(r"[^\w\s]", " ", normalizada).split()
    if not palavras or len(palavras) > _MAX_PALAVRAS_ACEITE:
        return False
    return all(
        p in {t for token in _ACEITES_CURTOS for t in token.split()} for p in palavras
    )


def pediu_para_encerrar(mensagem: str | None) -> bool:
    """Cliente sinalizou que a conversa acabou.

    Duas regras, nao uma: frase explicita de encerramento vale sempre;
    agradecimento solto so vale em mensagem curta. Sem o limite de
    tamanho, um "obrigado, mas queria ver outra regiao" fecharia o
    atendimento no meio -- o oposto do que o cliente pediu."""
    if not mensagem:
        return False
    normalizada = _normalizar(mensagem)
    if any(frase in normalizada for frase in _DESPEDIDAS_EXPLICITAS):
        return True
    palavras = re.sub(r"[^\w\s]", " ", normalizada).split()
    if len(palavras) > _MAX_PALAVRAS_DESPEDIDA:
        return False
    # Token de uma palavra casa por palavra inteira (senao "obg" casaria
    # dentro de outra palavra); token composto casa por substring.
    return any(
        (token in palavras) if " " not in token else (token in normalizada)
        for token in _TOKENS_DESPEDIDA
    )


def detectar_objecao(mensagem: str | None) -> str | None:
    """Tipo da objecao, ou None. Match por frase em codigo -- mesma
    filosofia de `pediu_para_ver_opcoes`."""
    if not mensagem:
        return None
    normalizada = _normalizar(mensagem)
    for tipo, frases in _OBJECOES.items():
        if any(frase in normalizada for frase in frases):
            return tipo
    return None


def pediu_para_ver_opcoes(mensagem: str | None) -> bool:
    """Match em codigo, nao via LLM -- mesma filosofia de guardrail do
    resto do projeto (nao pedir pro modelo classificar o que da pra casar
    por string)."""
    if not mensagem:
        return False
    normalizada = _normalizar(mensagem)
    return any(pedido in normalizada for pedido in _PEDIDOS_VER_OPCOES)


def decidir_proximo(slots: dict, mensagem: str | None = None) -> str:
    intencao = slots.get("intencao") or "indefinido"

    if intencao == "indefinido":
        return "perguntar_intencao"

    if intencao in ("compra", "aluguel"):
        # Antes de tudo: se o cliente perguntou ONDE existe imovel, isso se
        # responde com dado real (repo.regioes_disponiveis), nao com mais
        # uma pergunta. Vale em qualquer ponto do roteiro -- a lista so
        # fica mais precisa conforme preco/quartos ja estejam preenchidos.
        if pediu_lista_de_regioes(mensagem):
            return "listar_regioes"
        # preco e quartos continuam obrigatorios antes de mostrar qualquer
        # coisa: sao os dois filtros que impedem a lista de sair generica
        # demais, e o PDF pede os dois explicitamente no Cenario 1.
        if slots.get("faixa_preco_max") is None:
            return "perguntar_preco"
        if slots.get("quartos") is None:
            return "perguntar_quartos"
        # Atalho: com preco e quartos na mao, um pedido explicito de ver o
        # portfolio vale mais que terminar o questionario. Regiao/urgencia
        # nao se perdem -- a mensagem de apresentacao ja fecha perguntando
        # qual imovel/bairro interessou e que dia funciona pra visita
        # (core/turno.py::_apresentar_imoveis).
        if pediu_para_ver_opcoes(mensagem):
            return "buscar_imoveis"
        if slots.get("regiao") is None:
            return "perguntar_regiao"
        if slots.get("urgencia") is None:
            return "perguntar_urgencia"
        return "buscar_imoveis"

    if intencao == "investimento":
        # Sem atalho aqui de proposito: o desfecho do Cenario 2 do PDF e
        # encaminhar pra especialista, nao mostrar portfolio.
        if slots.get("perfil_investidor") is None:
            return "perguntar_perfil_investidor"
        if slots.get("ticket") is None:
            return "perguntar_ticket"
        if slots.get("expectativa_retorno") is None:
            return "perguntar_expectativa_retorno"
        return "encaminhar_especialista"

    return "perguntar_intencao"
