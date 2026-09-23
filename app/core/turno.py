"""Orquestrador de turno (secao 5.2 do PLANO.md).

Fluxo de conversa 100% em codigo (agents/router.py); texto controlado pelo
LLM so quando faz sentido. Compartilhado por todos os canais -- Telegram,
web chat, CLI -- que so entregam texto e recebem texto de volta (secao 5.8:
"canal e adapter"). Nenhum canal importa LLM, banco ou agentes diretamente;
todos passam por `processar_turno`.

Guardrail central da Etapa 5 (secao 5.6): o FATO (card de imovel, data da
visita, corretor, motivo do encaminhamento) e montado em CODIGO
(`_formatar_cards` e os blocos fixos dentro de `_apresentar_imoveis`,
`_confirmar_visita`), nunca escrito pelo LLM -- preco, endereco e id de
imovel sao os dados mais caros de alucinar, e tirar o LLM do caminho deles
e garantia estrutural, nao regra de prompt que pode ser ignorada (secao 5.3).

Mas guardrail nao e desculpa pra virar bot de template -- isso E o
"tique que denuncia bot" que a secao 5.3 pede pra evitar, so que
reintroduzido pelo proprio guardrail. Por isso `_gerar_frase` (abaixo)
gera a FRASE DE ABERTURA de cada mensagem com fato fixo -- o LLM nunca
toca no numero, so escreve a saudacao/reacao em volta, que varia a cada
chamada. Guardrail no dado, humanizacao no texto -- as duas coisas juntas,
nao uma trocada pela outra.
"""

import logging
import re
import unicodedata
from datetime import date, datetime, time, timedelta

from app.agents.qualifier import extrair_slots, merge_slots
from app.agents.router import (
    TRATATIVA_POR_OBJECAO,
    detectar_objecao,
    eh_aceite_curto,
    perguntou_quem_e,
)
from app.agents.scheduler_agent import (
    escolher_corretor,
    extrair_reagendamento,
    proximo_horario_util,
)
from app.agents.search import buscar_imoveis
from app.core.llm import LLMError, chat_text
from app.core.memory import contexto_conversa
from app.core.prompts import INSTRUCAO_PADRAO, INSTRUCOES_POR_ACAO, PERSONA, REGRAS
from app.core.scoring import calcular_score
from app.core.security import mascarar_telefone
from app.core.traces import registrar_trace
from app.db import repo

logger = logging.getLogger(__name__)

RESPOSTA_FALLBACK = "Desculpa, tive um probleminha aqui agora. Pode repetir o que voce disse?"


def _persona_com_data(turno: int) -> str:
    """Todo prompt do agente de resposta passa por aqui -- achado no
    Telegram real: perguntado "que dia é hoje?", o LLM respondeu "dia 26
    de setembro" quando a data real era 19. O modelo nao tem relogio nem
    calendario proprio; sem essa linha, "hoje" e so mais um dado que ele
    inventa, igual aconteceu com "quinta-feira e dia 19" antes de
    `_resolver_data` existir. Centralizado numa funcao (nao 3 f-strings
    soltas) pra garantir que todo lugar que monta o system prompt do
    agente de resposta -- `_gerar_resposta`, `_gerar_frase`,
    `_turno_pos_desfecho` -- sempre sabe a data real.

    `turno` decide a linha de apresentacao: achado em uso real -- a Bia
    ficava dizendo "Aqui e a Bia da Horizonte" em praticamente toda
    mensagem, nao so na primeira. A regra de REGRAS pede pra so se
    apresentar no primeiro turno ou quando perguntado, mas depender so do
    LLM "lembrar" a propria regra ao longo da conversa e o mesmo tipo de
    aposta que ja falhou outras vezes neste projeto -- por isso o fato
    (e ou nao e o primeiro turno) vem pronto aqui, nao inferido."""
    hoje = _formatar_data_falada(date.today())
    apresentacao = (
        'Esta e a PRIMEIRA mensagem desta conversa -- pode se apresentar ("Aqui é a Bia '
        'da Horizonte") antes de seguir.'
        if turno <= 1
        else "Esta NÃO é a primeira mensagem -- não se reapresente, a menos que o cliente "
        "pergunte quem você é ou com quem está falando."
    )
    return f"{PERSONA}\n\nHoje é {hoje}. {apresentacao}\n\n{REGRAS}"


def processar_turno(*, chat_id: str, canal: str, mensagem: str, origem: str = "texto") -> str:
    """Um turno completo. A orquestracao (qual no roda em qual ordem, pra
    cada status do lead) e feita pelo grafo de estados em core/graph.py --
    import tardio aqui de proposito, pra evitar import circular (graph.py
    importa as funcoes de logica deste modulo; este modulo so precisa do
    grafo no momento da chamada, nao no carregamento)."""
    # Import tardio de proposito -- ver docstring desta funcao.
    from app.core.graph import processar_turno_grafo  # noqa: PLC0415

    lead = repo.get_or_create_lead(chat_id=chat_id, canal=canal)
    lead_id = lead["id"]

    # Cliente voltou depois do fechamento por inatividade (core/followup.py
    # ::verificar_inatividade). Nada e apagado (pedido do usuario) -- o que
    # muda e so pra qual ramo do grafo ele volta:
    #
    #   - com visita marcada que ainda nao passou: volta pro ramo
    #     pos-desfecho, que ja sabe citar imovel/corretor/data da visita
    #     (`_resumo_desfecho`). Sem isso o compromisso ja marcado virava
    #     "novo atendimento" e o agente recomecava a qualificacao de quem
    #     ja tem visita no dia seguinte.
    #   - sem visita ativa: retoma a qualificacao com os slots e o contato
    #     que ja existiam (repo.retomar_atendimento).
    #
    # `lead` precisa ser recarregado depois -- o resto do turno usa o
    # status dele pra rotear (core/graph.py::_rota_por_status).
    if lead["status"] == "encerrado":
        if repo.get_visita_ativa(lead_id):
            repo.atualizar_status(lead_id, "visita_agendada")
        else:
            repo.retomar_atendimento(lead_id)
        lead = repo.get_lead(lead_id)

    turno = repo.contar_turnos(lead_id) + 1

    repo.salvar_mensagem(lead_id, papel="user", conteudo=mensagem, origem=origem)

    resposta = processar_turno_grafo(lead, mensagem, turno=turno)

    repo.salvar_mensagem(lead_id, papel="assistant", conteudo=resposta, origem="texto")
    return resposta


# ---------------------------------------------------------------- qualificacao (cenarios 1 e 2, antes do desfecho)
#
# A orquestracao deste ramo (extrair -> decidir -> apresentar/encaminhar/
# finalizar) mora em core/graph.py como nos de um StateGraph. As funcoes
# abaixo sao as pecas reutilizaveis que os nos chamam -- toda a logica de
# negocio/guardrail, nenhuma decisao de fluxo.


def _finalizar_qualificacao(mensagem: str, slots: dict, proxima_acao: str, *, lead_id: int, turno: int) -> str:
    """Passo final do ramo de qualificacao quando nenhum campo falta
    resolver ainda (nem buscar imoveis, nem encaminhar) -- so pergunta o
    proximo campo. Extraido pra funcao propria pra virar um no do grafo
    (core/graph.py), separado da extracao/roteamento que vem antes."""
    resposta = _gerar_resposta(mensagem, slots, proxima_acao, lead_id=lead_id, turno=turno)
    score = calcular_score(slots)
    indefinida = not slots.get("intencao") or slots["intencao"] == "indefinido"
    repo.atualizar_status_score(lead_id, score=score, status="novo" if indefinida else "qualificando")
    return resposta


def _extrair_e_mesclar(
    mensagem: str, slots_atuais: dict, *, lead_id: int, turno: int, pergunta_pendente: str | None = None
) -> dict:
    try:
        qualification, trace = extrair_slots(mensagem, slots_atuais, pergunta_pendente=pergunta_pendente)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
        return merge_slots(slots_atuais, qualification)
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        logger.warning("extracao de slots falhou, mantendo slots atuais: %s", e)
        return slots_atuais


def _contexto_cliente_conhecido(lead_id: int) -> str:
    """Fatos que a Horizonte JA tem sobre este cliente de antes deste turno:
    nome, telefone registrado e visita marcada que ainda nao passou.

    Achado em uso real (pedido do usuario): fechando e reabrindo a
    conversa, o agente perguntava o nome outra vez e nao sabia da visita
    ja agendada -- os dados estavam no banco desde sempre, so nunca
    chegavam ao prompt. Slots iam no contexto, `leads.nome` e a tabela
    `visitas` nao. Mesmo principio do resto do projeto (secao 5.6): o
    guardrail de nao inventar so vale se o fato real estiver no contexto.
    """
    lead = repo.get_lead(lead_id)
    if not lead:
        return ""

    fatos = []
    if lead.get("nome"):
        fatos.append(f"nome: {lead['nome']}")
    if lead.get("telefone_mascarado"):
        fatos.append(f"telefone ja registrado no cadastro: {lead['telefone_mascarado']}")

    visita = repo.get_visita_ativa(lead_id)
    if visita:
        imovel = repo.get_imovel(visita["imovel_id"]) if visita["imovel_id"] else None
        onde = f"{imovel['titulo']} ({imovel['bairro']})" if imovel else "imovel ja escolhido"
        fatos.append(
            f"visita JA MARCADA e ainda por acontecer: {onde} com {visita['corretor']}, "
            f"{_formatar_data_extenso(visita['data_hora'].date())}"
            + (f' (preferencia dita: "{visita["horario_solicitado"]}")' if visita["horario_solicitado"] else "")
        )

    if not fatos:
        return ""

    return (
        "Dados que você JÁ tem deste cliente de conversas anteriores (use "
        "naturalmente, trate-o pelo nome, e NUNCA pergunte de novo o que estiver "
        "aqui): " + "; ".join(fatos) + "."
    )


def _gerar_resposta(mensagem: str, slots: dict, proxima_acao: str, *, lead_id: int, turno: int) -> str:
    instrucao = INSTRUCOES_POR_ACAO.get(proxima_acao, INSTRUCAO_PADRAO)

    # Objecao detectada em codigo vira a instrucao PRINCIPAL do turno, com a
    # pergunta do roteiro rebaixada pro fim (ver `agents/router.py::
    # detectar_objecao`). Antes isso era regra fixa no system prompt e
    # perdia a disputa de atencao pra instrucao do turno -- "ja tenho
    # corretor" virava um "Entendido!" seco seguido da proxima pergunta.
    objecao = detectar_objecao(mensagem)
    if objecao:
        instrucao = (
            f"{TRATATIVA_POR_OBJECAO[objecao]} Trate isso PRIMEIRO, em 1 frase. "
            f"Só depois, na mesma mensagem, retome naturalmente: {instrucao}"
        )

    system = _persona_com_data(turno)
    # memoria (resumo rolante, secao 5.5/Etapa 6) -- so entra quando ha algo
    # alem dos slots estruturados; conversa curta (turno 1-2) nao tem resumo
    # ainda, entao contexto_conversa devolve vazio e nao infla o prompt.
    memoria = contexto_conversa(lead_id)
    conhecido = _contexto_cliente_conhecido(lead_id)
    user = (
        (f"{conhecido}\n\n" if conhecido else "")
        + (f"{memoria}\n\n" if memoria else "")
        + f"Dados ja coletados do cliente: {slots}\n"
        f'Ultima mensagem do cliente: "{mensagem}"\n\n'
        f"O que fazer agora: {instrucao}"
    )
    # Rotulo proprio no trace quando houve objecao -- fica visivel na aba de
    # observabilidade do dashboard, entao "o agente trata objecao" deixa de
    # ser afirmacao de slide e vira evidencia auditavel.
    agente = f"responder_objecao:{objecao}" if objecao else "responder"
    # Dizer o proprio nome so e correto na abertura ou quando perguntado --
    # fora isso o guardrail de autorreferencia reescreve (core/llm.py).
    permite_nome = turno <= 1 or perguntou_quem_e(mensagem)
    try:
        texto, trace = chat_text(
            agente=agente, system=system, user=user, slots=slots, permite_nome=permite_nome
        )
        registrar_trace(trace, lead_id=lead_id, turno=turno)
        return texto
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        logger.warning("geracao de resposta falhou, usando fallback: %s", e)
        return RESPOSTA_FALLBACK


# Marcadores de FATO numa frase que deveria ser so texto: digito, valor,
# percentual, metragem. O bloco de fato dessas mensagens e montado em
# codigo logo depois (`_formatar_cards`, lista de bairros, confirmacao de
# visita), entao numero na frase de abertura e sempre uma de duas coisas:
# repeticao do que ja vem escrito, ou invencao. Nenhuma das duas serve.
_FATO_NA_FRASE = re.compile(r"\d|R\$|%|\[[^\]]{3,}\]")


def _frase_tem_fato(texto: str) -> bool:
    """Digito/valor/percentual, ou placeholder de instrucao nao preenchido
    ("[descrever o imovel aqui]") -- os dois significam que o modelo
    invadiu o espaco do bloco de fato, que e montado em codigo logo
    depois. Nos dois casos vale mais o fallback fixo."""
    return bool(_FATO_NA_FRASE.search(texto))


def _gerar_frase(instrucao: str, *, lead_id: int, turno: int, fallback: str, slots: dict | None = None) -> str:
    """Frase curta gerada pelo LLM pra abrir/reagir a uma mensagem que tem
    um FATO fixo em outro lugar (cards, confirmacao de visita, encaminhamento)
    -- ver docstring do modulo. Fallback fixo garante que uma falha aqui
    nunca trava o fluxo (secao 10: excecao nunca chega no usuario) e nunca
    fica sem resposta nenhuma, so menos variada.

    A separacao fato/texto e verificada nos DOIS lados, nao so num: o
    codigo entrega o fato certo E o texto do LLM e descartado se contiver
    fato. Sem essa checagem, a instrucao "nao cite preco" seria so mais
    uma regra de prompt -- exatamente o tipo de garantia que este projeto
    ja provou nao segurar (secao 5.3 do PLANO.md)."""
    system = _persona_com_data(turno)
    try:
        texto, trace = chat_text(agente="responder", system=system, user=instrucao, temperature=0.4, slots=slots)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        return fallback

    if _frase_tem_fato(texto):
        logger.warning("frase de abertura veio com fato, usando fallback: %r", texto)
        return fallback
    return texto


# ---------------------------------------------------------------- cenario 1: busca + apresentacao (RAG, secao 5.4)

def _apresentar_imoveis(lead_id: int, slots: dict, mensagem: str, *, turno: int) -> str:
    finalidade = "aluguel" if slots.get("intencao") == "aluguel" else "venda"
    resultados = buscar_imoveis(
        finalidade=finalidade,
        zona=slots.get("regiao"),
        preco_max=slots.get("faixa_preco_max"),
        quartos_min=slots.get("quartos"),
        limite=3,
    )
    score = calcular_score(slots)

    if not resultados:
        # guardrail (secao 5.6): filtro vazio -> admite e oferece ajustar
        # criterio, nunca inventa imovel pra preencher a lacuna.
        repo.atualizar_status_score(lead_id, score=score, status="qualificando")

        # Cidade nao atendida nao chega ate aqui: `core/graph.py::
        # _no_decidir_proximo` intercepta assim que a regiao e informada,
        # pra nao deixar o cliente responder mais perguntas achando que a
        # cidade dele esta ok. Aqui e sempre "existe, mas nao nessa faixa".
        return (
            "Poxa, não encontrei nada com exatamente esses critérios agora. "
            "Quer tentar ajustar a faixa de preço ou a região?"
        )

    repo.salvar_imoveis_apresentados(lead_id, [im["id"] for im in resultados])
    repo.atualizar_status_score(lead_id, score=score, status="aguardando_escolha")

    cards = _formatar_cards(resultados)
    intro = _gerar_frase(
        f"O cliente pediu um imóvel pra {slots.get('intencao')} na região "
        f"{slots.get('regiao') or 'que ele mencionou'}, e você vai mostrar "
        f"{len(resultados)} opção(ões) agora. Última mensagem dele: \"{mensagem}\". "
        "Escreva SÓ 1 frase curta e natural reconhecendo o pedido dele antes de "
        "mostrar as opções -- se a última mensagem trouxer uma objeção ou hesitação, "
        "trate ela primeiro (regra de objeção do system prompt) antes de emendar pra "
        "apresentação; não liste nada, não mencione preço nem endereço específico, "
        "isso vem logo depois.",
        lead_id=lead_id, turno=turno, slots=slots,
        fallback="Encontrei essas opções pra você:",
    )
    return (
        f"{intro}\n\n{cards}\n\n"
        "Qual te interessou -- pode dizer o número ou o bairro? E qual dia costuma "
        "funcionar melhor pra uma visita?"
    )


def _cidades_atendidas(regiao_pedida: str, *, finalidade: str, lead_id: int, turno: int) -> str:
    """Cliente pediu uma cidade que a imobiliaria nao cobre -- responde com
    a lista REAL de cidades atendidas, em vez de mandar "ajuste o preco".

    Pedido do usuario. A lista sai do banco e e formatada em codigo: nome
    de cidade e preco sao exatamente o tipo de dado que o LLM nao pode
    inventar (mesmo guardrail do card de imovel, secao 5.6) -- e aqui o
    risco seria pior que o normal, porque prometer uma cidade que a
    imobiliaria nao atende e uma promessa impossivel de cumprir."""
    cidades = repo.cidades_disponiveis(finalidade)
    if not cidades:
        return (
            f"Ainda não trabalho com imóveis em {regiao_pedida}. "
            "Quer tentar outra região?"
        )

    sufixo = "/mês" if finalidade == "aluguel" else ""
    linhas = [
        f"📍 {c['cidade']} (a partir de "
        + f"R$ {c['preco_min']:,.0f}".replace(",", ".")
        + f"{sufixo})"
        for c in cidades[:8]
    ]
    blocos = "\n".join(linhas)
    if len(cidades) > 8:
        blocos += f"\n\n(e mais {len(cidades) - 8})"

    intro = _gerar_frase(
        f'O cliente procurou imóvel em "{regiao_pedida}", e a imobiliária ainda não '
        "atende essa cidade. Escreva SÓ 1 frase curta dizendo isso com naturalidade e "
        "emendando que atende outras cidades de São Paulo -- NÃO cite nome de cidade "
        "nem preço, a lista vem escrita logo depois por outro processo.",
        lead_id=lead_id, turno=turno,
        fallback=f"Ainda não atendo {regiao_pedida}, mas tenho opções nestas cidades:",
    )
    return f"{intro}\n\n{blocos}\n\nAlguma dessas funciona pra você?"


def _listar_regioes(lead_id: int, slots: dict, *, turno: int) -> str:
    """Responde "quais bairros voces tem?" com a lista REAL do banco.

    Mesmo guardrail do card de imovel (secao 5.6): nome de bairro e preco
    sao montados em codigo a partir do SELECT, o LLM so escreve a frase de
    abertura em volta -- ele nunca ve a chance de inventar um bairro que
    nao existe no estoque."""
    finalidade = "aluguel" if slots.get("intencao") == "aluguel" else "venda"
    regioes = repo.regioes_disponiveis(
        finalidade=finalidade,
        preco_max=slots.get("faixa_preco_max"),
        quartos_min=slots.get("quartos"),
    )

    if not regioes:
        return (
            "Poxa, com esses critérios eu não tenho nenhum bairro com opção disponível agora. "
            "Quer tentar ajustar a faixa de preço ou a quantidade de quartos?"
        )

    # Agrupado por CIDADE (nao por zona): com 10 cidades no estoque, "zona
    # sul" so faz sentido dentro da capital -- fora dela o cliente pensa em
    # cidade. Limite de 6 cidades pra lista nao virar parede de texto no
    # Telegram; as com mais opcao vem primeiro (ORDER BY do SELECT).
    por_cidade: dict[str, list[str]] = {}
    for r in regioes:
        preco_fmt = f"R$ {r['preco_min']:,.0f}".replace(",", ".")
        sufixo = "/mês" if finalidade == "aluguel" else ""
        por_cidade.setdefault(r["cidade"], []).append(
            f"{r['bairro']} (a partir de {preco_fmt}{sufixo})"
        )
    mostradas = list(por_cidade.items())[:6]
    blocos = "\n".join(
        f"📍 {cidade}: " + ", ".join(bairros[:4]) for cidade, bairros in mostradas
    )
    if len(por_cidade) > len(mostradas):
        blocos += f"\n\n(e mais {len(por_cidade) - len(mostradas)} cidades)"

    filtro = []
    if slots.get("quartos"):
        filtro.append(f"{slots['quartos']} quarto(s)")
    if slots.get("faixa_preco_max"):
        valor = f"R$ {slots['faixa_preco_max']:,.0f}".replace(",", ".")
        filtro.append(f"até {valor}")
    resumo_filtro = " e ".join(filtro) if filtro else "o que você procura"

    # A contagem entra na instrucao pra calibrar o tom -- sem ela o modelo
    # escreveu "temos opcoes em varios bairros" pra uma lista de 2. E numero
    # real, calculado aqui, entao citar nao e alucinacao.
    qtd_cidades = len(por_cidade)
    qtd_bairros = len(regioes)
    intro = _gerar_frase(
        f"O cliente perguntou onde existem imóveis pra {slots.get('intencao')}. "
        f"São {qtd_bairros} bairro(s) em {qtd_cidades} cidade(s) com opção pra {resumo_filtro}. "
        "Escreva SÓ 1 frase curta apresentando a lista, sem exagerar a quantidade -- NÃO "
        "cite nome de cidade, bairro nem preço, isso vem escrito logo depois por outro processo.",
        lead_id=lead_id, turno=turno, slots=slots,
        fallback="Essas são as regiões com opção pro que você procura:",
    )
    return f"{intro}\n\n{blocos}\n\nQual dessas regiões faz mais sentido pra você?"


def _formatar_cards(imoveis: list[dict]) -> str:
    """Card montado em codigo -- ver guardrail no docstring do modulo.
    Numeros (1, 2, 3) sao o que `_turno_escolha_visita` usa pra mapear a
    resposta do cliente de volta ao imovel, sem precisar repetir titulo."""
    linhas = []
    for i, im in enumerate(imoveis, start=1):
        preco_fmt = f"R$ {im['preco']:,.0f}".replace(",", ".")
        sufixo = "/mês" if im["finalidade"] == "aluguel" else ""
        linha = (
            f"{i}. {im['titulo']} — {im['bairro']}\n"
            f"   {preco_fmt}{sufixo} · {im['quartos']} quarto(s) · "
            f"{im['banheiros']} banheiro(s) · {im['area']}m²"
        )
        if im.get("rentabilidade_estimada"):
            linha += f"\n   rentabilidade estimada: {im['rentabilidade_estimada']}% a.a."
        linhas.append(linha)
    return "\n\n".join(linhas)


# ---------------------------------------------------------------- cenario 2: encaminhamento a especialista

def _encaminhar_especialista(lead_id: int, slots: dict, mensagem: str, *, turno: int) -> str:
    partes = []
    if slots.get("perfil_investidor"):
        partes.append(f"perfil: {slots['perfil_investidor']}")
    if slots.get("ticket"):
        partes.append(f"ticket: R$ {slots['ticket']:,.0f}".replace(",", "."))
    if slots.get("expectativa_retorno"):
        partes.append(f"retorno esperado: {slots['expectativa_retorno']}")
    motivo = "; ".join(partes) if partes else "sem detalhes adicionais"

    repo.criar_encaminhamento(lead_id, especialista="Equipe de Investimentos Horizonte", motivo=motivo)
    score = calcular_score(slots)
    repo.atualizar_status_score(lead_id, score=score, status="encaminhado")

    return _gerar_frase(
        "O cliente acabou de ser qualificado pra investimento e você vai encaminhar ele "
        "pra um especialista em investimentos da equipe, que vai entrar em contato em breve. "
        f'Última mensagem dele: "{mensagem}". Escreva uma mensagem curta e calorosa avisando '
        "isso -- se a última mensagem trouxer uma objeção ou hesitação, trate ela primeiro "
        "(regra de objeção do system prompt) antes de emendar pro encaminhamento. Não "
        "mencione número, valor ou percentual específico -- isso fica com o especialista.",
        lead_id=lead_id, turno=turno, slots=slots,
        fallback=(
            "Perfeito! Vou te encaminhar agora para um especialista em investimentos "
            "imobiliários da nossa equipe — ele vai entrar em contato em breve com "
            "opções alinhadas ao que você me contou. 🤝"
        ),
    )


# ---------------------------------------------------------------- pos-busca: escolha do imovel + horario da visita

def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


def _resolver_por_texto(mensagem: str, imoveis: list[dict]) -> int | None:
    """Casa a mensagem do cliente com um dos imoveis mostrados por bairro
    ou tipo -- ex: "pedro ludovico" -> "Setor Pedro Ludovico" (achado
    testando no Telegram real: cliente respondeu pelo nome do bairro, nao
    pelo numero, e o fluxo nao reconheceu). Match em CODIGO contra o dado
    real, nao via LLM -- mesma filosofia de guardrail da secao 5.6: nao
    pedir pro modelo adivinhar o que ja podemos comparar por string."""
    msg_norm = _normalizar(mensagem)
    candidatos = set()
    for im in imoveis:
        bairro_norm = _normalizar(im["bairro"])
        # duas direcoes: mensagem cita o bairro inteiro ("moro perto do Parque
        # Amazônia") OU cita so uma parte dele ("pedro ludovico" para "Setor
        # Pedro Ludovico") -- sem isso, nomes de bairro com prefixo (Setor,
        # Jardim, Parque...) nunca batem quando o cliente omite o prefixo.
        if bairro_norm in msg_norm or msg_norm in bairro_norm:
            candidatos.add(im["id"])
    if len(candidatos) == 1:
        return next(iter(candidatos))
    return None


def _parece_nome_de_bairro(texto: str, imoveis: list[dict]) -> bool:
    """Guardrail de codigo, nao so de prompt (mesma licao da secao 5.3):
    testado e reproduzido -- so de existir o campo `nome` no schema, o
    modelo passou a ler "pedro ludovico" (resposta sobre QUAL IMOVEL) como
    se fosse o cliente se apresentando. Regra de prompt sozinha ja provou
    nao ser confiavel o bastante; aqui da pra checar contra o dado real."""
    texto_norm = _normalizar(texto)
    return any(
        _normalizar(im["bairro"]) in texto_norm or texto_norm in _normalizar(im["bairro"])
        for im in imoveis
    )


def _proxima_pergunta_agendamento(
    *, tem_imovel: bool, tem_horario: bool, tem_nome: bool, tem_telefone: bool
) -> str:
    tem_contato = tem_nome and tem_telefone
    if tem_imovel and tem_horario and not tem_contato:
        # Nome pode vir de atendimento anterior (`_resolver_escolha`) -- nesse
        # caso so falta o telefone, e pedir "seu nome e um telefone" seria
        # exatamente o esquecimento que o resto desta mudanca corrige.
        if tem_nome:
            return "Perfeito! Só me confirma um telefone pra contato?"
        return "Perfeito! Última coisa: pode me passar seu nome e um telefone pra contato?"
    if tem_imovel and not tem_horario:
        return "Legal! E qual dia ou período costuma funcionar melhor pra você visitar?"
    if tem_horario and not tem_imovel:
        return "Entendido sobre o horário! Qual das opções te interessou -- pode ser o número (1, 2 ou 3) ou o bairro."
    return (
        "Pode me dizer qual das opções te interessou -- o número (1, 2 ou 3) ou o "
        "bairro -- e um horário que funcione pra você?"
    )


# A orquestracao deste ramo (extrair escolha -> resolver -> confirmar/
# perguntar) mora em core/graph.py como nos de um StateGraph. As funcoes
# abaixo sao as pecas reutilizaveis.

def _resolver_escolha(lead: dict, mensagem: str, escolha) -> dict:
    """Parte 100% em codigo do sub-fluxo de agendamento: resolve imovel
    (numero ou bairro), horario, nome e telefone a partir do que ja foi
    dito antes (`lead.visita_*`) + o que a extracao devolveu agora.
    Separada de `_turno_escolha_visita` pra virar um no do grafo
    (core/graph.py) independente da chamada de LLM que a antecede."""
    ids_apresentados = lead.get("imoveis_apresentados") or []
    imoveis_apresentados = [im for i in ids_apresentados if (im := repo.get_imovel(i))]

    imovel_id = lead.get("visita_imovel_escolhido")
    if escolha.numero_escolhido and 1 <= escolha.numero_escolhido <= len(ids_apresentados):
        imovel_id = ids_apresentados[escolha.numero_escolhido - 1]
    else:
        imovel_id = _resolver_por_texto(mensagem, imoveis_apresentados) or imovel_id

    horario = escolha.horario_texto or lead.get("visita_horario_texto")

    # guardrail: "pedro ludovico" (resposta sobre qual imovel) nao pode virar
    # nome do cliente so porque o schema tem um campo `nome` agora (ver
    # docstring de _parece_nome_de_bairro).
    nome_extraido = escolha.nome
    if nome_extraido and _parece_nome_de_bairro(nome_extraido, imoveis_apresentados):
        nome_extraido = None
    # `leads.nome` no fim da cadeia: nome ja dado num atendimento anterior
    # continua valendo -- pedir de novo e o tipo de esquecimento que o
    # cliente percebe na hora (pedido do usuario). Telefone nao entra
    # nessa herança: o banco so guarda a versao mascarada (secao 7), que
    # nao serve pra equipe ligar, entao ele e sempre perguntado de novo.
    nome = nome_extraido or lead.get("visita_nome_texto") or lead.get("nome")
    telefone = escolha.telefone or lead.get("visita_telefone_texto")

    return {
        "imovel_id": imovel_id,
        "horario": horario,
        "nome": nome,
        "telefone": telefone,
        "completo": bool(imovel_id and horario and nome and telefone),
    }


def _confirmar_visita(
    lead_id: int, *, imovel_id: int, horario_texto: str, nome: str, telefone: str, turno: int
) -> str:
    imovel = repo.get_imovel(imovel_id)
    corretor = escolher_corretor(lead_id)

    # data real calculada em codigo quando o texto tem um dia identificavel
    # ("quinta que vem", "dia 22", "amanha"); sem isso, so um placeholder
    # interno (nunca mostrado ao cliente -- ver _resumo_desfecho).
    data_resolvida = _resolver_data(horario_texto)
    data_hora = (
        datetime.combine(data_resolvida, time(hour=_resolver_hora_do_periodo(horario_texto)))
        if data_resolvida
        else proximo_horario_util()
    )

    repo.criar_visita(
        lead_id, imovel_id=imovel_id, corretor=corretor, data_hora=data_hora, horario_solicitado=horario_texto
    )
    # PII nunca em texto puro no banco (secao 7) -- telefone mascarado antes de gravar.
    repo.atualizar_contato(lead_id, nome=nome, telefone_mascarado=mascarar_telefone(telefone))

    slots = repo.get_slots(lead_id)
    score = calcular_score(slots, visita_agendada=True)
    repo.atualizar_status_score(lead_id, score=score, status="visita_agendada")
    repo.salvar_escolha_visita_parcial(lead_id, imovel_id=None, horario_texto=None)  # limpa estado do sub-fluxo

    primeiro_nome = nome.split(maxsplit=1)[0] if nome else ""

    intro = _gerar_frase(
        f"A visita do cliente {primeiro_nome or ''} acabou de ser confirmada. Escreva SÓ uma "
        "frase curta e animada reagindo/confirmando -- os detalhes da visita (imóvel, corretor, "
        "data) vêm escritos por outro processo logo depois, então NÃO invente nem repita "
        "nenhum desses detalhes agora, só a reação.",
        lead_id=lead_id, turno=turno, slots=slots,
        fallback=f"Show, {primeiro_nome}!" if primeiro_nome else "Show!",
    )
    if data_resolvida:
        fatos = (
            f"Marquei sua visita ao {imovel['titulo']} ({imovel['bairro']}) com {corretor}, "
            f'para "{horario_texto}" -- {_formatar_data_extenso(data_resolvida)}. A equipe '
            "confirma o horário exato e te chama no telefone que você passou. 📅"
        )
    else:
        fatos = (
            f"Marquei sua visita ao {imovel['titulo']} ({imovel['bairro']}) com {corretor}. "
            f'Horário combinado: "{horario_texto}" — a equipe confirma o horário exato e te '
            "chama no telefone que você passou. 📅"
        )
    return f"{intro}\n\n{fatos}"


# ---------------------------------------------------------------- pos-desfecho (visita marcada ou ja encaminhado)

def _resumo_desfecho(lead: dict) -> str:
    """Dados reais do desfecho (visita ou encaminhamento), pra dar pro LLM
    responder perguntas tipo "qual dia ficou a reserva?" sem inventar --
    achado no Telegram real: o cliente perguntou os detalhes da visita ja
    marcada e o agente respondeu com um texto generico, porque nao tinha
    NENHUM dado do agendamento no contexto pra citar. O guardrail de nao
    alucinar so funciona se o dado real estiver disponivel -- senao a
    unica saida do modelo e inventar ou ser vago."""
    lead_id = lead["id"]

    if lead["status"] == "visita_agendada":
        visita = repo.get_ultima_visita(lead_id)
        if visita:
            imovel = repo.get_imovel(visita["imovel_id"])
            titulo = imovel["titulo"] if imovel else "imóvel não encontrado"
            bairro = imovel["bairro"] if imovel else ""
            horario_texto = visita.get("horario_solicitado") or "a confirmar"

            # data_hora so e citavel quando recalculavel a partir do texto
            # ATUAL (idempotente, mesma funcao usada pra gravar) -- assim
            # nunca cita um placeholder interno como se fosse a data real,
            # e nunca fica desatualizada apos uma correcao de horario.
            data_resolvida = _resolver_data(horario_texto)
            if data_resolvida:
                return (
                    f"Visita confirmada: {titulo} ({bairro}), corretor(a) {visita['corretor']}. "
                    f'Horário combinado: "{horario_texto}" -- {_formatar_data_extenso(data_resolvida)}.'
                )
            return (
                f"Visita confirmada: {titulo} ({bairro}), corretor(a) {visita['corretor']}. "
                f'Horário combinado: "{horario_texto}".'
            )

    if lead["status"] == "encaminhado":
        enc = repo.get_ultimo_encaminhamento(lead_id)
        if enc:
            return f"Encaminhado para: {enc['especialista']}. Detalhes informados: {enc['motivo']}."

    return "Nenhum detalhe adicional registrado."


_DIAS_SEMANA = {
    "segunda": 0, "terca": 1, "terça": 1, "quarta": 2, "quinta": 3,
    "sexta": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}
_NOMES_DIA_SEMANA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira", 3: "Quinta-feira",
    4: "Sexta-feira", 5: "Sábado", 6: "Domingo",
}
_NOMES_MES = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril", 5: "maio", 6: "junho",
    7: "julho", 8: "agosto", 9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}
_PERIODOS_HORA = {"manha": 9, "manhã": 9, "tarde": 14, "noite": 19}


def _somar_mes(d: date) -> date:
    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)


def _resolver_data(texto: str, referencia: date | None = None) -> date | None:
    """Resolve dia da semana / "amanha" / "dia N" pro PROXIMO calendario
    real -- nunca pedir pro LLM fazer essa conta.

    Achado no Telegram real: o agente escreveu "Quinta-feira é dia 19!"
    quando a proxima quinta-feira era dia 24 -- LLM fazendo aritmetica de
    calendario e exatamente o tipo de alucinacao que os guardrails deste
    projeto existem pra evitar (secao 5.3/5.6: preco, endereco e agora
    data sao dados caros demais pra deixar o LLM calcular). So retorna
    algo quando reconhece um marcador de dia explicito -- "de manha"
    sozinho, sem dia, devolve None, e melhor nao citar data nenhuma do
    que citar uma errada."""
    referencia = referencia or date.today()
    baixo = _normalizar(texto)

    if "amanha" in baixo:
        return referencia + timedelta(days=1)
    if "hoje" in baixo:
        return referencia

    m = re.search(r"\bdia\s*(\d{1,2})\b", baixo)
    if m:
        try:
            candidato = referencia.replace(day=int(m.group(1)))
            if candidato < referencia:
                candidato = _somar_mes(candidato)
            return candidato
        except ValueError:
            return None

    for nome, alvo_wd in _DIAS_SEMANA.items():
        if nome in baixo:
            dias_ate = (alvo_wd - referencia.weekday()) % 7 or 7  # nunca hoje -- "que vem" e sempre futuro
            return referencia + timedelta(days=dias_ate)

    return None


def _resolver_hora_do_periodo(texto: str) -> int:
    """Mesma logica: hora explicita ("16h", "as 16") em codigo; sem hora
    explicita, usa um horario tipico do periodo mencionado (manha/tarde/
    noite); sem nenhum dos dois, um default neutro."""
    baixo = _normalizar(texto)
    m = re.search(r"\b(\d{1,2})\s*h\b", baixo) or re.search(r"\b(?:as|horario)\s*(\d{1,2})\b", baixo)
    if m:
        return int(m.group(1))
    for periodo, hora in _PERIODOS_HORA.items():
        if periodo in baixo:
            return hora
    return 10


def _formatar_data_extenso(d: date) -> str:
    """Formato pedido: "21/11/2026 (Quinta-feira)" -- usado nos blocos de
    FATO fixo (confirmação de visita, reagendamento)."""
    return f"{d.strftime('%d/%m/%Y')} ({_NOMES_DIA_SEMANA[d.weekday()]})"


def _formatar_data_falada(d: date) -> str:
    """Formato natural falado: "sábado, 19 de setembro de 2026" -- usado
    na linha "Hoje é ..." do system prompt (_persona_com_data).

    Achado no Telegram real: com a data em "19/09/2026 (Sábado)" no
    prompt, o modelo parafraseou certo o dia mas derrubou o ano ("Hoje é
    sábado, 19 de setembro! 🏠..."). Dando a frase já pronta em português
    natural com ano por extenso, reduz a chance dele reformular e perder
    informação ao parafrasear."""
    dia_semana = _NOMES_DIA_SEMANA[d.weekday()].lower()
    return f"{dia_semana}, {d.day} de {_NOMES_MES[d.month]} de {d.year}"


_PALAVRAS_DIA = (
    "segunda", "terca", "terça", "quarta", "quinta", "sexta", "sabado", "sábado",
    "domingo", "amanha", "amanhã", "hoje",
)


def _combinar_horario(atual: str | None, novo: str) -> str:
    """Decide se `novo` complementa `atual` (fragmento curto -- so a hora,
    ou so o dia) ou substitui tudo (o cliente ja deu dia + horario numa
    tacada, entao "tarde" antigo esta obsoleto). Em CODIGO, nao pedindo
    pro LLM julgar -- ver docstring de extrair_reagendamento pra o motivo.

    "completo" = a mensagem tem referencia de QUE DIA e de QUE HORARIO
    juntas -- so entao ela se basta e pode substituir o que tinha antes.
    "dia 22" sozinho (sem periodo/hora) so complementa "tarde"; "sexta de
    manha" (dia + periodo) substitui "tarde" inteiro."""
    if not atual:
        return novo

    baixo = novo.lower()
    # \b (word boundary) em vez de substring nua -- achado no Telegram real:
    # "manha" (periodo) e substring de "amanha" (dia), entao "amanha"
    # sozinho batia como tendo dia E periodo juntos, virando substituicao
    # total quando deveria so mesclar.
    tem_dia = bool(re.search(r"\b(" + "|".join(_PALAVRAS_DIA) + r")\b", baixo)) or bool(
        re.search(r"\bdia\s*\d{1,2}\b", baixo)
    )
    tem_hora = bool(re.search(r"\b\d{1,2}\s*h\b", baixo)) or bool(re.search(r"\b(as|às|horario|hor[áa]rio)\s*\d{1,2}\b", baixo))
    tem_periodo = bool(re.search(r"\b(manha|manhã|tarde|noite)\b", baixo))

    if tem_dia and (tem_hora or tem_periodo):
        return novo
    return f"{atual}, {novo}"


def _parece_pergunta_sobre_data(mensagem: str) -> bool:
    """Distingue "e amanhã, que dia é?" (pergunta) de "pode ser amanhã"
    (preferência de horário) ANTES de chamar o LLM.

    Achado no Telegram real: pedir pro extrator (via prompt, com exemplos
    explícitos) fazer essa distinção sozinho criava o efeito colateral
    inverso -- preferências legítimas como "pode ser amanhã" também
    passaram a voltar `null`. Mesma lição de sempre: quando uma
    heurística de código simples resolve, ela é mais confiável que pedir
    pro modelo julgar mais uma coisa. Ponto de interrogação + palavra
    interrogativa de data é sinal forte o bastante de pergunta, não de
    mudança de horário."""
    baixo = _normalizar(mensagem)
    return "?" in mensagem and any(p in baixo for p in ("que dia", "qual dia", "quando e", "quando fica"))


def _tentar_reagendar(lead: dict, mensagem: str, *, turno: int) -> str | None:
    """Reagendamento real (nao so conversa) -- achado no Telegram real: o
    cliente corrigiu o horario duas vezes ("dia 22", "horario 16") e o
    agente respondeu "anotado!" sem gravar nada. Na proxima pergunta sobre
    a reserva, o horario antigo voltava -- a confirmacao verbal era
    mentira. So mexe em algo se status ja for visita_agendada e a
    mensagem realmente parecer um novo horario (extrair_reagendamento
    devolve null pra mensagens que nao tem nada a ver)."""
    if lead["status"] != "visita_agendada":
        return None
    if _parece_pergunta_sobre_data(mensagem):
        return None

    lead_id = lead["id"]
    visita = repo.get_ultima_visita(lead_id)
    if not visita:
        return None

    try:
        pref, trace = extrair_reagendamento(mensagem)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        return None

    if not pref.horario_texto:
        return None

    novo_horario = _combinar_horario(visita.get("horario_solicitado"), pref.horario_texto)

    # mesma logica de _confirmar_visita: data real em codigo quando da pra
    # identificar (achado no Telegram real -- o LLM alucinou "quinta-feira
    # e dia 19" quando quinta era dia 24; ver docstring de _resolver_data).
    data_resolvida = _resolver_data(novo_horario)
    nova_data_hora = (
        datetime.combine(data_resolvida, time(hour=_resolver_hora_do_periodo(novo_horario)))
        if data_resolvida
        else None
    )
    repo.atualizar_horario_visita(visita["id"], horario_solicitado=novo_horario, data_hora=nova_data_hora)

    intro = _gerar_frase(
        "O cliente atualizou a preferência de horário da visita, e você acabou de gravar isso. "
        "Escreva SÓ uma frase curta e cordial reagindo/confirmando -- o horário exato vem "
        "escrito por outro processo logo depois, então NÃO mencione nenhum dia ou horário "
        "agora, só a reação.",
        lead_id=lead_id, turno=turno,
        fallback="Combinado!",
    )

    if data_resolvida:
        fato = f'Preferência registrada: "{novo_horario}" -- {_formatar_data_extenso(data_resolvida)}.'
    else:
        fato = f'Preferência registrada: "{novo_horario}".'

    return f"{intro} {fato} A equipe confirma o horário exato com você."


def _contexto_data_mencionada(mensagem: str) -> str:
    """Se o cliente mencionar um dia da semana/relativo NESTA mensagem (ex:
    "que dia é terça-feira?"), resolve a data real -- em código, nunca
    pedindo pro LLM calcular (seção 5.6/13).

    Achado no Telegram real: com só a data da visita já marcada no
    contexto, uma pergunta sobre OUTRO dia ("que dia é terça-feira?", a
    visita era quarta) não tinha nada pra responder, e o agente só repetiu
    a data da visita de volta -- seguiu certo a regra de "não invente",
    mas não respondeu a pergunta, porque o fato que faltava nunca tinha
    sido calculado."""
    data = _resolver_data(mensagem)
    if data:
        return f'\n\nO cliente mencionou um dia nesta mensagem: corresponde a {_formatar_data_extenso(data)}.'
    return ""


# ---------------------------------------------------------------- despedida (qualquer ramo)

# Nao vira "perdido": o cliente nao sumiu nem foi embora insatisfeito --
# ele encerrou educadamente. Se ja tinha desfecho (visita marcada ou
# encaminhamento), o status DE NEGOCIO e preservado, senao a conversao
# sumiria do dashboard por causa de um "obrigado".
_STATUS_DESFECHO_PRESERVADO = ("visita_agendada", "encaminhado")


def _despedir(lead: dict, *, turno: int) -> str:
    """Fecha a conversa quando o cliente se despede (`router.py::
    pediu_para_encerrar`).

    Achado no Telegram real: com a visita ja marcada, "Ok" e depois
    "Obrigado" faziam o agente repetir a confirmacao inteira da visita as
    duas vezes -- o ramo pos-desfecho so sabia reafirmar o fato, nunca
    entender que a conversa tinha acabado.

    A frase nao repete os dados da visita de proposito: quem se despede
    ja leu a confirmacao. `_gerar_frase` ainda garante que nenhum numero
    escape pro texto (`_frase_tem_fato`)."""
    lead_id = lead["id"]
    tem_desfecho = lead["status"] in _STATUS_DESFECHO_PRESERVADO

    if tem_desfecho:
        instrucao = (
            "O cliente está se despedindo e o atendimento dele JÁ FOI CONCLUÍDO "
            "(visita marcada ou encaminhamento feito). Escreva SÓ uma despedida curta "
            "e calorosa, deixando claro que ele pode chamar quando quiser. NÃO repita "
            "data, endereço, nome de corretor nem valor -- ele já recebeu tudo isso."
        )
        fallback = "Combinado! Qualquer coisa é só me chamar por aqui. Até breve! 👋"
    else:
        instrucao = (
            "O cliente está encerrando a conversa antes de fechar qualquer coisa. "
            "Escreva SÓ uma despedida curta e cordial, sem insistir e sem tentar "
            "vender mais nada, deixando a porta aberta pra quando ele quiser voltar."
        )
        fallback = "Tranquilo! Quando quiser retomar, é só me chamar. 👋"

    # Follow-up nao pode perseguir quem se despediu -- seria exatamente o
    # comportamento de vendedor chato que o resto do projeto evita.
    repo.cancelar_followups_pendentes(lead_id)
    if not tem_desfecho:
        repo.atualizar_status(lead_id, "encerrado")

    return _gerar_frase(instrucao, lead_id=lead_id, turno=turno, fallback=fallback)


# A orquestracao deste ramo (checar reagendamento -> resposta generica se
# nao reagendou) mora em core/graph.py como nos de um StateGraph.

def _responder_generico_pos_desfecho(lead: dict, mensagem: str, *, turno: int) -> str:
    """Resposta a qualquer coisa que nao virou reagendamento -- perguntas
    sobre a visita/encaminhamento, sobre outro dia mencionado, ou so
    conversa cordial (secao 5.9). Extraida pra funcao propria pra virar um
    no do grafo (core/graph.py), separada da checagem de reagendamento
    que vem antes."""
    lead_id = lead["id"]

    # "Ok" / "certo" / "beleza": o cliente so acusou recebimento. Repetir o
    # bloco inteiro da visita (imovel, corretor, data) nesse caso e o tique
    # de robo da secao 5.3 -- visto no Telegram real, um "Ok" devolvia a
    # confirmacao completa outra vez. Responde curto e para de falar.
    if eh_aceite_curto(mensagem):
        return _gerar_frase(
            "O cliente só confirmou que recebeu (disse algo como 'ok' ou 'certo'), sem "
            "perguntar nada. Escreva SÓ uma frase bem curta fechando com cordialidade, "
            "sem repetir nenhum dado do atendimento e sem fazer pergunta nova.",
            lead_id=lead_id, turno=turno,
            fallback="Qualquer dúvida, é só me chamar por aqui! 😊",
        )

    contexto = _resumo_desfecho(lead) + _contexto_data_mencionada(mensagem)
    system = _persona_com_data(turno)
    user = (
        f"Dados reais do atendimento deste cliente (ja concluido): {contexto}\n\n"
        f'Ultima mensagem do cliente: "{mensagem}"\n\n'
        "O que fazer agora: Se o cliente perguntar sobre a visita/reserva/encaminhamento "
        "ou sobre que data cai um dia que ele mencionou, responda usando SOMENTE os dados "
        "acima -- nunca invente data, endereço ou nome que não esteja ali. NUNCA faça "
        "nenhuma conta de calendário por conta própria (semana do ano, quantos dias "
        "faltam) -- cite só as datas exatas que já estão escritas acima, nada calculado "
        "por você. Se ele perguntar algo que não está nos dados, diga que a equipe vai "
        "retornar com detalhes. Fora isso, responda breve e cordial, sem reabrir a "
        "qualificação."
    )
    try:
        texto, trace = chat_text(agente="responder", system=system, user=user)
        registrar_trace(trace, lead_id=lead_id, turno=turno)
        return texto
    except LLMError as e:
        registrar_trace(e.trace, lead_id=lead_id, turno=turno)
        return RESPOSTA_FALLBACK
