"""Client do Ollama nativo -- embeddings e chat com saida estruturada.

Duas coisas nao obvias, descobertas testando contra o servidor real antes de
escrever este arquivo:

1. qwen3.5 e um modelo de raciocinio. Sem `think: false` no payload (fora de
   `options`), ele gasta o budget inteiro de tokens em um bloco de "thinking"
   e o campo `content` volta vazio -- em teste manual, 40s e 340+ tokens sem
   chegar a resposta. Com `think: false`: resposta direta, eval_duration
   caindo de dezenas de segundos para ~2s. TODA chamada de chat precisa
   desse flag, senão o alvo de <6s/turno (secao 5.2) e impossivel.
2. `format` aceita o JSON Schema inteiro (nao so `"json"`), mas so se os campos
   opcionais usarem `"type": [X, "null"]`. O `anyOf: [{type:X},{type:"null"}]`
   que o `model_json_schema()` do Pydantic v2 gera por padrao para `X | None`
   faz o grammar-constrained decoding do llama.cpp devolver `null` sempre,
   silenciosamente -- sem erro, sem exception, so o campo nunca preenchido.
   Reproduzido em teste manual antes de escrever `qualifier.py`: com `anyOf`,
   "zona sul" e "2 quartos" na mensagem viravam `null`; convertendo o mesmo
   schema para `type: [X, "null"]`, os mesmos campos vieram certos. Por isso
   `_simplificar_schema` abaixo reescreve o schema do Pydantic antes de
   mandar pro Ollama.
3. O `format` tambem nao garante o NOME da chave, so o `type`/`enum` do
   valor -- reproduzido com `intencao`: o mesmo payload, mesma temperatura
   0.0, devolveu a chave como `"intenção"` (com acento) em 2 de 3 tentativas.
   Como o campo tem palavra natural em portugues com acento, o
   grammar-constrained decoding "corrige" a grafia da chave por conta
   propria. `_normalizar_chaves` abaixo desfaz isso comparando a versao
   sem acento de cada chave devolvida contra os campos do schema.
"""

import re
import time
import unicodedata

import httpx
from json_repair import repair_json
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.traces import TraceInfo


class LLMError(Exception):
    """Falha apos o retry unico (secao 10 -- excecao nunca deve chegar ao usuario)."""

    def __init__(self, message: str, trace: TraceInfo):
        super().__init__(message)
        self.trace = trace


def embed_text(text: str) -> list[float]:
    """Vetor de 768 dims via nomic-embed-text. Usado no seed e na busca hibrida."""
    settings = get_settings()
    resp = httpx.post(
        f"{settings.ollama_host}/api/embeddings",
        json={"model": settings.ollama_embed_model, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _simplificar_schema(schema: dict) -> dict:
    """Converte `anyOf: [{type:X}, {type:"null"}]` em `type: [X, "null"]`
    (ver nota do modulo). So cobre schemas flat de 1 nivel -- e o que os
    agentes deste projeto usam (secao 5.1: sem nesting, sem $defs).

    Duas pegadinhas resolvidas aqui, achadas testando contra o servidor real:

    - Quando um campo tem `enum` E `type` permite null, o JSON Schema so
      considera valido um valor de fora do enum se `null` tambem estiver
      DENTRO do enum -- `type:["string","null"]` sozinho nao basta. Sem
      isso, o grammar do llama.cpp forca o modelo a sempre escolher um dos
      valores do enum, mesmo quando nada na mensagem justifica (visto
      inventando `urgencia:"baixa"` do nada). Por isso todo enum nullable
      ganha `None` como opcao explicita.
    - `description` verboso por campo, com 8 campos no schema, piora a
      extracao em vez de ajudar -- um campo obvio como `regiao` passou a
      voltar sempre null. Titulo e descricao ficam de fora do schema; a
      explicacao de cada campo mora no system prompt do agente, nao aqui.
    """
    props = {}
    for nome, spec_original in schema.get("properties", {}).items():
        spec = dict(spec_original)
        opcoes = spec.pop("anyOf", None)
        spec.pop("default", None)
        spec.pop("title", None)
        spec.pop("description", None)
        if opcoes:
            tipos = []
            enum_vals = None
            for opcao in opcoes:
                tipos.append(opcao.get("type", "null"))
                if "enum" in opcao:
                    enum_vals = list(opcao["enum"])
            spec["type"] = tipos
            if enum_vals is not None:
                if "null" in tipos:
                    enum_vals = [*enum_vals, None]
                spec["enum"] = enum_vals
        props[nome] = spec

    return {"type": "object", "properties": props, "required": list(props.keys())}


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _normalizar_chaves(bruto: dict, campos: set[str]) -> dict:
    """Remapeia chave devolvida com acento (`intenção`) para o campo real
    do schema (`intencao`), comparando a forma sem acento. Ver nota 3 do
    docstring do modulo."""
    mapa = {_sem_acento(campo): campo for campo in campos}
    return {mapa.get(_sem_acento(chave), chave): valor for chave, valor in bruto.items()}


def _trace_de_resposta(data: dict, *, agente: str, modelo: str, latencia_ms: int, erro: str | None = None) -> TraceInfo:
    prompt_tokens = data.get("prompt_eval_count")
    completion_tokens = data.get("eval_count")
    eval_duration_s = data.get("eval_duration", 0) / 1e9
    tokens_por_s = (
        round(completion_tokens / eval_duration_s, 2) if completion_tokens and eval_duration_s else None
    )
    return TraceInfo(
        agente=agente,
        modelo=modelo,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latencia_ms=latencia_ms,
        tokens_por_s=tokens_por_s,
        erro=erro,
    )


# Marcadores de resposta malformada, achados testando contra o servidor real
# (ver docstring de chat_text). Checagem barata, e nao syntatica -- um
# "retry se parece ruim" e mais robusto que confiar 100% no prompt pra um
# modelo deste tamanho.
_MARCADORES_RESPOSTA_RUIM = (
    "aqui está a resposta",
    "aqui esta a resposta",
    "resposta para enviar",
    "vou usar um termo",
    "como sdr virtual",
    "bia,",  # "Perfeito, Bia." / "Entendido, Bia!" -- chamando o CLIENTE pelo nome do agente
    "bia!",
    "bia.",
    "bia aqui",
    # Terceira pessoa: "Bia está aqui para ajustar a busca", "a Bia vai te
    # mandar". Achado testando as tratativas de objecao. Padroes estreitos
    # de proposito -- "Aqui é a Bia da Horizonte" (apresentacao legitima no
    # turno 1) nao pode cair aqui.
    "bia esta aqui",
    "bia está aqui",
    "a bia vai",
    "a bia pode",
    "semana do ano",  # LLM inventando conta de calendario -- achado real: "quarta semana do
                       # ano" pra uma data que era semana 39. Data em si vem pronta no
                       # contexto (core/turno.py::_resolver_data); qualquer conta ADICIONAL
                       # de calendario feita pelo modelo e chute.
    # Vazamento do proprio prompt na resposta -- achado real: a resposta veio
    # com "Cliente: 2 quartos\n\nDados ja coletados do cliente: {...}\n
    # Ultima mensagem do cliente: \"2 quartos\"\n\nO que fazer agora: ..."
    # colado no final, verbatim. O modelo "continuou" o formato Cliente:/
    # Você: que `core/memory.py::contexto_conversa` injeta no historico, em
    # vez de responder a partir dele. Marcadores da propria estrutura do
    # prompt (nunca aparecem numa resposta de verdade pro cliente).
    "dados ja coletados",
    "dados já coletados",
    "ultima mensagem do cliente",
    "última mensagem do cliente",
    "o que fazer agora",
    # Raciocinio interno vazando pro texto final -- mesma familia do "vou
    # usar um termo generico" acima (secao 5.3 do PLANO.md), achado real:
    # "Zona Sul não foi mencionada ainda, então vou focar no que falta
    # saber." antes da pergunta de verdade -- o modelo narrando a propria
    # decisao de fluxo em vez de so executa-la.
    "foi mencionada ainda",
    "vou focar no que falta",
)


_ABERTURA_BIA = re.compile(r"(?i)^\s*bia[,!.]?\s*(aqui)?[,!.:]?\s*")


def _sanitizar_resposta(texto: str) -> str:
    """Guardrail final, deterministico, depois do retry -- achado real: o
    tic "Bia aqui!" sobreviveu as 2 tentativas de `chat_text` em 3/3
    testes de follow-up, mesmo com a regra explicita "nunca escreva Bia na
    resposta" em REGRAS. Retry em prompt nao fecha 100% pra esse tique
    especifico; remover a abertura em codigo fecha. So mexe no INICIO do
    texto -- nunca risca conteudo do meio, so a saudacao indevida."""
    limpo = _ABERTURA_BIA.sub("", texto).strip()
    if limpo:
        return limpo[0].upper() + limpo[1:] if limpo[0].islower() else limpo
    return texto  # nunca devolve string vazia -- melhor manter o "Bia" do que nao responder nada


_MARCADORES_VAZAMENTO_PROMPT = (
    "dados ja coletados",
    "dados já coletados",
    "ultima mensagem do cliente",
    "última mensagem do cliente",
    "o que fazer agora",
)


def _sanitizar_vazamento_prompt(texto: str) -> str:
    """Guardrail final pro vazamento de prompt (ver `_MARCADORES_RESPOSTA_RUIM`
    acima) -- se mesmo assim as 2 tentativas do retry vierem com o prompt
    colado, corta a partir do primeiro marcador em vez de mandar pro
    cliente. Igual `_sanitizar_resposta`: so mexe a partir do ponto onde o
    vazamento comeca, preserva o que veio limpo antes (normalmente a
    resposta de verdade, com o lixo grudado depois)."""
    baixo = texto.lower()
    indices = [baixo.find(m) for m in _MARCADORES_VAZAMENTO_PROMPT]
    indices = [idx for idx in indices if idx != -1]
    if not indices:
        return texto
    corte = min(indices)
    limpo = texto[:corte].strip()
    # O vazamento costuma comecar 1 linha ANTES do marcador -- o formato
    # "Cliente: X" / "Você: Y" que `core/memory.py::contexto_conversa`
    # injeta no historico. Corta essa linha residual tambem, senao sobra
    # grudada no fim do texto que ficou limpo.
    limpo = re.sub(r"\n\n(Cliente|Você):.*$", "", limpo, flags=re.IGNORECASE | re.DOTALL).strip()
    return limpo or texto  # nunca devolve string vazia



# Zonas fixas do negocio (mesmas de app/db/seed.py::BAIRROS_POR_ZONA) -- tic
# recorrente do modelo, so problema se o cliente nao tiver informado essa
# regiao de verdade. Nao so "zona sul": ampliado pra todas as 5, achado
# real mostrou o mesmo tique noutra variante de frase, mesma causa.
_ZONAS_CONHECIDAS = ("zona sul", "zona norte", "zona leste", "zona oeste", "centro")

_QUEBRA_FRASE = re.compile(r"(?<=[.!?])\s+")


def _sanitizar_zona_alucinada(texto: str, *, slots: dict | None = None) -> str:
    """Guardrail final pro tic de zona alucinada -- sobreviveu as 2
    tentativas do retry em teste real ("Zona sul, ótimo.", "Zona Sul não é
    o foco agora, Bia!", "Zona sul não é necessário mencionar aqui, pois o
    cliente ainda não citou região." -- 3 variantes de frase diferentes,
    mesma causa). Nas 3, a mencao indevida sempre veio como frase inteira
    (abertura/reacao), nunca colada no meio de uma frase sobre outra
    coisa -- por isso remover a frase inteira (nao so a palavra) preserva
    a pergunta de verdade que vem depois, sem quebrar gramatica."""
    regiao_conhecida = str((slots or {}).get("regiao", "")).lower()
    baixo = texto.lower()
    if not any(zona in baixo and zona != regiao_conhecida for zona in _ZONAS_CONHECIDAS):
        return texto

    frases = _QUEBRA_FRASE.split(texto)
    restantes = [
        f for f in frases
        if not any(zona in f.lower() and zona != regiao_conhecida for zona in _ZONAS_CONHECIDAS)
    ]
    limpo = " ".join(restantes).strip()
    return limpo or texto  # nunca devolve string vazia


# Autorreferencia pelo nome no meio da frase -- o tique mais teimoso do
# projeto. `_ABERTURA_BIA` acima so pega a ABERTURA ("Bia aqui! ..."); estes
# sao os casos no meio, achados testando as tratativas de objecao:
# "Entendo perfeitamente, Bia aqui da Horizonte!" e "Bia está aqui quando
# voce precisar". Retry nao segura (2/2 falharam), regra de prompt tambem
# nao -- entao reescrita deterministica, igual ao resto dos guardrails.
# A pontuacao final NAO entra nos padroes de propósito: "Entendo, Bia aqui
# da Horizonte!" tem que virar "Entendo!", nao "Entendo" -- comer o "!"
# junto colaria a frase seguinte.
_REESCRITAS_AUTORREFERENCIA = (
    (re.compile(r"(?i)[,;]?\s*(?:a\s+)?bia\s+aqui\s+da\s+horizonte"), ""),
    (re.compile(r"(?i)[,;]?\s*(?:a\s+)?bia\s+aqui"), ""),
    (re.compile(r"(?i)\b(?:a\s+)?bia\s+est[aá]\s+aqui\b"), "estou aqui"),
)

# "Bia deixa tudo organizado" -> "deixo tudo organizado". Conjugar de
# verdade, nao so trocar por "eu": um `eu \1` generico produzia "eu vai",
# erro de concordancia que numa demo fica pior que o tique original.
_VERBOS_TERCEIRA_PESSOA = {
    "vai": "vou", "pode": "posso", "consegue": "consigo", "deixa": "deixo",
    "faz": "faço", "tem": "tenho", "fica": "fico", "manda": "mando",
    "envia": "envio", "busca": "busco", "separa": "separo", "mostra": "mostro",
}
_BIA_VERBO = re.compile(
    r"(?i)\b(?:a\s+)?bia\s+(" + "|".join(_VERBOS_TERCEIRA_PESSOA) + r")\b"
)

_ESPACO_ANTES_PONTUACAO = re.compile(r"\s+([!?.,])")
_ESPACOS_DUPLICADOS = re.compile(r"\s{2,}")
_SO_PONTUACAO = re.compile(r"^[\s!?.,;:]*$")
_PONTUACAO_INICIAL = re.compile(r"^[\s!?.,;:]+")


def _sanitizar_autorreferencia(texto: str, *, permite_nome: bool = False) -> str:
    """Remove o agente falando de si em 3a pessoa. `permite_nome=True` no
    turno 1 e quando o cliente pergunta com quem esta falando -- nesses
    dois casos dizer o nome e o comportamento correto, nao o tique."""
    if permite_nome:
        return texto

    limpo = texto
    for padrao, troca in _REESCRITAS_AUTORREFERENCIA:
        limpo = padrao.sub(troca, limpo)
    limpo = _BIA_VERBO.sub(lambda m: _VERBOS_TERCEIRA_PESSOA[m.group(1).lower()], limpo)

    limpo = _ESPACO_ANTES_PONTUACAO.sub(r"\1", limpo)
    limpo = _ESPACOS_DUPLICADOS.sub(" ", limpo).strip()
    # Quando a autorreferencia era a frase inteira ("Bia aqui!"), sobra so
    # pontuacao -- ai vale mais devolver o texto original do que um "!".
    if _SO_PONTUACAO.match(limpo):
        return texto
    limpo = _PONTUACAO_INICIAL.sub("", limpo).strip() or texto
    return limpo[0].upper() + limpo[1:] if limpo[0].islower() else limpo


# Placeholder de instrucao vazando pro cliente -- achado testando o seed de
# Sao Paulo: "Aqui esta uma opcao incrivel: [descrever brevemente o imovel
# ou enviar link]". O modelo tratou o proprio texto como template a
# preencher. Colchete com conteudo nunca e mensagem legitima de WhatsApp.
_PLACEHOLDER = re.compile(r"\[[^\]]{3,}\]")


def _parece_resposta_ruim(texto: str, *, slots: dict | None = None) -> bool:
    baixo = texto.lower()
    if any(marcador in baixo for marcador in _MARCADORES_RESPOSTA_RUIM):
        return True
    if _PLACEHOLDER.search(texto):
        return True

    regiao_conhecida = str((slots or {}).get("regiao", "")).lower()
    return any(zona in baixo and zona != regiao_conhecida for zona in _ZONAS_CONHECIDAS)


def chat_text(
    *, agente: str, system: str, user: str, temperature: float = 0.15,
    slots: dict | None = None, permite_nome: bool = False,
) -> tuple[str, TraceInfo]:
    """Resposta em texto livre, humanizada -- chamada 2 do turno (secao 5.2).

    Sem `format`/grammar: geracao livre e mais rapida que forcar JSON pra
    texto corrido (grammar so serve pra extracao estruturada, chat_structured
    acima).

    `temperature=0.15`, nao 0.4 -- medido: a 0.4 o modelo alucinava "Zona
    Sul" em conversas que nunca mencionaram regiao (2 em 5 respostas),
    chamava o cliente de "Bia" (nome do proprio agente) e uma vez vazou
    raciocinio interno pro texto final ("Como nao temos o nome do cliente
    ainda, vou usar um termo generico..."). Temperatura baixa reduz a
    variancia, mas nao zera o risco -- mesmo com REGRAS explicita em
    core/prompts.py proibindo isso por nome, o mesmo tipo de erro reapareceu
    num teste posterior ("Perfeito, Bia. Aqui está a resposta para enviar no
    WhatsApp: ..."). Prompt sozinho nao e suficiente pra um modelo deste
    tamanho -- daí o retry condicionado a `_parece_resposta_ruim` abaixo,
    mesma filosofia do retry por JSON malformado em chat_structured.
    """
    settings = get_settings()
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "think": False,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": settings.ollama_num_ctx},
    }

    ultimo_texto = ""
    ultimo_trace: TraceInfo | None = None

    for _tentativa in range(2):
        inicio = time.monotonic()
        try:
            resp = httpx.post(f"{settings.ollama_host}/api/chat", json=payload, timeout=settings.ollama_timeout_s)
            resp.raise_for_status()
            data = resp.json()
            latencia_ms = int((time.monotonic() - inicio) * 1000)
            texto = data["message"]["content"].strip()
            trace = _trace_de_resposta(data, agente=agente, modelo=settings.ollama_model, latencia_ms=latencia_ms)

            texto = _sanitizar_resposta(texto)
            texto = _sanitizar_vazamento_prompt(texto)
            texto = _sanitizar_zona_alucinada(texto, slots=slots)
            texto = _sanitizar_autorreferencia(texto, permite_nome=permite_nome)
            if not _parece_resposta_ruim(texto, slots=slots):
                return texto, trace
            ultimo_texto, ultimo_trace = texto, trace  # guarda caso a 2a tentativa tambem falhe
        # Retry unico; se as duas tentativas falharem, propaga como LLMError.
        except Exception as e:  # noqa: BLE001
            latencia_ms = int((time.monotonic() - inicio) * 1000)
            ultimo_trace = TraceInfo(
                agente=agente, modelo=settings.ollama_model, prompt_tokens=None, completion_tokens=None,
                latencia_ms=latencia_ms, tokens_por_s=None, erro=str(e),
            )
            continue

    if ultimo_texto:
        # as 2 tentativas vieram "ruins" pelo heuristico, mas sao texto valido --
        # melhor entregar um texto imperfeito do que estourar fallback generico.
        return ultimo_texto, ultimo_trace

    raise LLMError(f"falha na geracao de resposta ({agente})", ultimo_trace)


def chat_structured[T: BaseModel](
    *,
    agente: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
) -> tuple[T, TraceInfo]:
    """Uma chamada de chat com saida forcada por JSON Schema (secao 5.1).

    Nao persiste o trace -- devolve para o chamador decidir (mantem este
    modulo sem dependencia de banco, o que permite testar agentes de
    extracao isoladamente, sem Telegram e sem Postgres -- criterio de
    pronto da Etapa 3).

    Retry unico com parser tolerante (json_repair) se o JSON vier malformado.
    """
    settings = get_settings()
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": _simplificar_schema(schema.model_json_schema()),
        "think": False,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": settings.ollama_num_ctx},
    }

    ultimo_erro = ""
    inicio = time.monotonic()

    for _tentativa in range(2):
        inicio = time.monotonic()
        try:
            resp = httpx.post(
                f"{settings.ollama_host}/api/chat", json=payload, timeout=settings.ollama_timeout_s
            )
            resp.raise_for_status()
            data = resp.json()
            latencia_ms = int((time.monotonic() - inicio) * 1000)
            conteudo = data["message"]["content"]

            # Normaliza chave SEMPRE, nao so como fallback de erro: campo com
            # default (como `intencao`) nao levanta ValidationError quando a
            # chave vem errada -- so ignora o valor e cai no default, mascarando
            # o problema em silencio (ver nota 3 do docstring do modulo).
            # `repair_json` ja faz o `json.loads` por dentro antes de tentar
            # reparar, entao cobre tanto o JSON valido quanto o malformado.
            bruto = repair_json(conteudo, return_objects=True)
            bruto = _normalizar_chaves(bruto, set(schema.model_fields))
            parsed = schema.model_validate(bruto)

            trace = _trace_de_resposta(data, agente=agente, modelo=settings.ollama_model, latencia_ms=latencia_ms)
            return parsed, trace
        # Retry unico; se as duas tentativas falharem, propaga como LLMError.
        except Exception as e:  # noqa: BLE001
            ultimo_erro = str(e)
            continue

    latencia_ms = int((time.monotonic() - inicio) * 1000)
    trace = TraceInfo(
        agente=agente,
        modelo=settings.ollama_model,
        prompt_tokens=None,
        completion_tokens=None,
        latencia_ms=latencia_ms,
        tokens_por_s=None,
        erro=ultimo_erro,
    )
    raise LLMError(f"falha na extracao estruturada ({agente}): {ultimo_erro}", trace)
