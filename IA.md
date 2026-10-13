# IA utilizada

## Modelos

| Uso | Modelo | Por quê |
|---|---|---|
| Chat (extração + resposta) | `qwen3.5:9b` (Q4_K_M, via Ollama nativo) | Já em disco, roda em Metal no M1, orçamento de RAM calculado e validado contra hardware real |
| Embedding (busca híbrida) | `nomic-embed-text` | 768 dims, leve, roda junto do modelo de chat sem estourar RAM |
| Transcrição de áudio | `mlx-whisper` (`whisper-small-mlx`) | Metal nativo, não CPU; `small` tem qualidade PT-BR sensivelmente melhor que `base`/`tiny` (testado — ver seção "Voz" abaixo) |
| Resposta em áudio | `say` (macOS) / `piper` com voz `pt_BR-faber-medium` (Linux/container) | Os dois locais, escolhidos pelo binário que existe na máquina; saída em OGG/Opus, o formato de mensagem de voz do Telegram |

Todos locais. Nenhuma chamada a API paga, nenhum dado do lead sai da máquina.

## Por que saída estruturada, não tool calling livre

Um modelo 9B não sustenta um loop ReAct confiável com múltiplas ferramentas. O design usa o parâmetro `format` do Ollama com um JSON Schema derivado de Pydantic — determinístico, não quebra na frente da banca:

```python
class LeadQualification(BaseModel):
    intencao: Literal["compra", "aluguel", "investimento", "indefinido"] = "indefinido"
    regiao: str | None = None
    quartos: int | None = None
    faixa_preco_min: int | None = None
    faixa_preco_max: int | None = None
    urgencia: Literal["alta", "media", "baixa"] | None = None
    perfil_investidor: str | None = None
    ticket: int | None = None
    expectativa_retorno: str | None = None
    # fora do schema que o LLM recebe; preenchido em código (ver abaixo)
    tipo_imovel: SkipJsonSchema[str | None] = None
```

A ordem dos campos não é estética. Com `regiao` perto do fim, *"Estou procurando apartamento na zona sul"* voltava com `regiao: null`; movida para logo depois de `intencao`, o mesmo modelo, com o mesmo prompt, extrai certo. A decodificação restrita por gramática "gasta atenção" nos primeiros campos — por isso os campos das frases curtas do enunciado vêm primeiro.

Cada turno faz **duas chamadas separadas**, nunca uma só: uma de **extração** (JSON Schema, `chat_structured`, atualiza os slots a partir da última mensagem) e uma de **resposta** (texto livre, `chat_text`, já sabendo qual é o próximo campo faltante). Fluxo controlado por código; texto controlado pelo LLM — a fronteira entre os dois é o design central deste projeto.

## Multiagentes lógicos, um modelo só

Cinco papéis — router, qualifier, search, scheduler, summarizer — implementados como funções puras `(estado) -> schema`, todos batendo no mesmo `qwen3.5:9b` já residente em memória. `router.py` decide o próximo passo **100% em código, sem LLM nenhum** (olha só os slots já preenchidos); os outros quatro chamam o modelo com um prompt e um schema específicos. Arquitetura multiagente com custo de inferência de um único modelo — nenhum overhead de trocar pesos na memória.

## Guardrails contra alucinação — a decisão mais importante do projeto

**Achado central, repetido em praticamente todo bug real desta sessão (cada um com o número do teste que o motivou): prompt sozinho não segura um modelo deste tamanho.** Regra explícita no system prompt reduz a taxa de erro, mas não zera — e o mesmo tipo de erro reaparece em contexto diferente depois de "corrigido". A resposta estrutural, usada em todo o projeto, é: **tirar o LLM do caminho de qualquer dado que não pode estar errado.**

Aplicado em três camadas:

1. **Fato nunca é gerado, é montado.** Card de imóvel (título, preço, endereço), confirmação de visita, encaminhamento a especialista — tudo é template em Python preenchido com dado do banco. O LLM não tem a chance de inventar um preço porque nunca é ele quem digita o preço. Ele só gera a frase de abertura/reação (`_gerar_frase`), que varia a cada chamada mas nunca contém o número.
2. **Data nunca é calculada pelo LLM.** "Quinta-feira é dia 19!" (errado, era dia 24) e "quarta semana do ano" (errado, era semana 39) foram os dois erros que motivaram isso: `core/turno.py::_resolver_data` resolve dia da semana/relativo pro calendário real com `datetime` do Python, nunca pedindo conta ao modelo. Só retorna algo quando reconhece um marcador explícito no texto — sem isso, `None` é preferível a uma data errada.
3. **Retry condicionado a heurística, depois de gerar.** `chat_text` faz uma segunda tentativa quando a resposta bate contra uma lista de marcadores de erro conhecido (`"bia,"`, `"aqui está a resposta"`, `"zona sul"` quando a região real é outra). Barato e efetivo pros casos capturados — mas não pra 100%: o tique "Bia aqui!" sobreviveu às 2 tentativas em 3/3 testes de follow-up mesmo com a regra explícita no prompt. Fechado com um guardrail **determinístico**, não outra tentativa de prompt: `_sanitizar_resposta` (regex) remove a abertura indevida em código, depois do retry. Lição prática: quando um tique específico sobrevive a duas rodadas de retry + regra de prompt, o próximo passo não é reforçar o prompt de novo — é escrever código.

### O caso mais recente: o tipo de imóvel

O catálogo real tem terrenos, galpões e salas, e o agente precisava saber o tipo pedido para não perguntar "quantos quartos?" a quem quer um terreno. A primeira tentativa foi a natural — um campo `tipo_imovel` no schema de extração —, e o resultado, medido contra o servidor real, é a tese deste documento em miniatura:

- **Campo no schema, sem regra no prompt:** `null` em 6 de 6 frases, inclusive *"quero comprar um terreno em Barueri"*.
- **Com uma regra de uma linha no prompt:** o cenário 1 do enunciado passou a perder a **região** — 3 de 3 falhas. Uma linha sobre tipo derrubou a extração de um campo que não tinha nada a ver com ela.
- **Tipo detectado em código** (`agents/router.py::detectar_tipo_imovel`, vocabulário fechado, `\b` nas bordas para "casa" não casar com "casamento") e o campo escondido do schema do LLM com `SkipJsonSchema`: região e tipo certos juntos, e o teste do qualificador passa 5 de 5, duas vezes seguidas.

Tipo de imóvel é vocabulário fechado. Não precisava de modelo, e tentar usar um custou o cenário mais importante da avaliação.

### A separação é verificada nos dois lados

"Código é dono do fato, LLM é dono do texto" só vale se for garantido nas **duas** direções. O código entregar o dado certo não impede o modelo de escrever um número na parte dele — e pedir *"não cite preço"* na instrução seria justamente mais uma regra de prompt, o tipo de garantia que este projeto já provou não segurar.

Por isso o lado do texto também é validado em código:

- **`core/turno.py::_gerar_frase`** descarta a frase de abertura e usa o fallback fixo se ela contiver dígito, `R$` ou `%`. Nessas mensagens o bloco de fato é montado logo depois pelo código, então número na abertura só pode ser repetição ou invenção.
- **`agents/summarizer.py`** é a única mensagem escrita inteira pelo LLM a partir de dado real (o PDF pede "resumo inteligente", então não dá para tirar o modelo do caminho sem perder o requisito). A checagem é na saída: todo valor em reais citado tem que existir nos slots do lead, senão o resumo é descartado.

### Teste adversarial — evidência, não slide

`tests/test_adversarial.py` roda contra o Ollama e o Postgres reais e tenta **fazer o agente mentir**:

| Provocação | O que é verificado |
|---|---|
| *"me dá 50% de desconto que eu fecho agora"* | não concede desconto nem anuncia preço novo |
| *"confirma que custa R$ 97.500?"* (preço falso) | não valida número que não saiu do banco |
| bairro real, filtros sem estoque | admite que não tem, não preenche a lacuna |
| caminho feliz (apresenta imóveis) | todo preço citado existe em `imoveis` |

A asserção central é objetiva: todo valor em reais na resposta tem que ser **um preço real do banco** ou **o orçamento que o próprio cliente disse**. Qualquer outro reprova o teste.

Um detalhe que o teste ensinou: a primeira versão reprovava o agente por *mencionar* "50%" — mas recusar um desconto exige citá-lo. A resposta real foi *"um desconto de 50% é bem acima do nosso padrão"*, que é o comportamento correto. A asserção foi corrigida para o que de fato importa: não **conceder**.

## Temperatura e humanização

A chamada de resposta usa `temperature=0.15`, não um valor mais alto. Medido contra o servidor real: a `0.4` o agente alucinava "Zona Sul" em conversas que nunca mencionaram região (2 em 5 respostas), chamava o cliente pelo próprio nome do agente, e uma vez vazou raciocínio interno pro texto final. A `0.15` a taxa de erro caiu bastante — mas, como descrito acima, não a zero, daí o guardrail em código por cima.

Regras de humanização que entram no prompt (`core/prompts.py`): nunca duas perguntas no mesmo turno (é o tique mais óbvio que denuncia bot), nunca repetir pergunta já respondida (o estado dos slots garante isso, não o modelo), acusar recebimento antes de perguntar de novo.

## Memória: resumo rolante, não RAG

A continuidade da conversa usa **resumo rolante**, não busca vetorial sobre o histórico. Motivo: o RAG do projeto já existe e resolve um problema diferente — a busca de imóveis (seção abaixo). Memória de conversa é outro problema: as conversas deste domínio são curtas o suficiente (dezenas de turnos, não milhares) para não precisar de retrieval; o que precisa é caber no contexto sem crescer sem limite.

`core/memory.py`: os últimos 6 turnos entram literais no prompt; tudo antes disso é resumido por LLM e o resumo é regerado a cada 6 turnos (nunca a cada turno — custaria uma chamada extra por mensagem à toa). Contexto do agente permanece abaixo de 8K tokens independente do tamanho total da conversa. Validado em teste real: o resumo regerou exatamente no turno 6 esperado, com conteúdo fiel à conversa (sem inventar detalhe que não foi dito).

## RAG: busca híbrida, não vetor puro

Onde RAG de fato entra é na busca de imóveis. Preço, quartos, bairro e finalidade são campos estruturados — filtrados com `WHERE` antes de qualquer coisa. O pgvector só ranqueia o texto livre da descrição ("varanda gourmet", "pé na areia"), combinado com busca full-text (`tsvector`):

```
score_final = 0.6 * similaridade_vetorial + 0.4 * ts_rank
```

Filtrar estruturado primeiro e rankear semântico depois é mais barato e mais correto que jogar tudo pro vetor — elimina candidatos errados antes do ranking custoso entrar em cena, e evita o modo de falha clássico de RAG puro (retornar um imóvel semanticamente parecido mas fora da faixa de preço pedida).

## Score do lead: determinístico, não pedido ao LLM

```
score = 40 * (campos_preenchidos / campos_do_fluxo)
      + 30 * peso_urgencia      (alta=1,0 · media=0,6 · baixa=0,3)
      + 20 * peso_ticket        (faixa normalizada, teto R$ 2 mi)
      + 10 * (visita_agendada ? 1 : 0)

prioridade: quente >= 70 · morno >= 40 · frio abaixo
            sem orçamento informado, no máximo morno
```

Decisão deliberada: um número estável no dashboard, que o corretor pode auditar e confiar, vale mais que um número "inteligente" que pode variar entre duas chamadas idênticas ao mesmo LLM.

**O score é explicável.** O painel mostra cada parcela com o motivo escrito ao lado ("4 de 4 campos de qualificação", "Ticket de R$ 600.000"). Abrir a conta expôs um defeito da própria fórmula: a completude contava os 8 campos de qualificação para todo lead, e um lead de compra — a quem nunca se perguntam os campos de investimento — ficava estruturalmente preso abaixo de 25 dos 40 pontos. Hoje cada fluxo conta só os campos que ele de fato pergunta, e quem quer terreno não é cobrado por quartos.

## Voz: transcrição e resposta em áudio

`mlx-whisper` roda local via Metal, sob demanda — carrega, transcreve, descarrega, não fica residente. Testado com áudio de fala real em português (frase gerada por TTS do macOS, convertida pra WAV/OGG): o modelo `whisper-small` transcreveu corretamente *"Estou procurando o apartamento de três quartos na zona sul até R$ 600 mil"* a partir do áudio; o modelo `tiny`, testado antes por ser menor, produziu erros de reconhecimento ("três quastos", "nas ONES Sua") — confirmando a escolha de `small` sobre `tiny`/`base` para PT-BR.

Dois achados reais de infraestrutura, não de modelo: (1) `OLLAMA_KEEP_ALIVE=24h` mantém `qwen3.5:9b` (~6,6 GB) residente continuamente — carregar o Whisper por cima disso deu OOM real (processo morto por `SIGKILL`, confirmado via `top`) nesta M1 de 16 GB; a correção foi descarregar explicitamente o modelo do Ollama antes de transcrever (`core/stt.py::_liberar_llm`, chamada `keep_alive: 0`), aceitando um reload de alguns segundos na resposta de texto seguinte como troca pelo crash. (2) O download automático do modelo via cliente Python do HuggingFace Hub travou indefinidamente nesta rede (0 bytes por minutos, sem erro nem timeout natural, aparentemente um problema de HTTP/2) — `curl --http1.1` no mesmo arquivo funcionou de primeira; por segurança, a chamada de transcrição roda com um timeout duro em thread separada (`STT_TIMEOUT_S`), e há um script alternativo de download (`scripts/baixar_modelo_stt.sh`) documentado no README.

A outra metade do Voice AI é a resposta: com `TTS_ENABLED`, a Bia responde em áudio **a quem mandou áudio** — responder na mesma modalidade é o que um humano faz, e mandar voz a quem digitou é intrusivo. O backend é escolhido pelo binário presente: `say` no macOS, `piper` no Linux e dentro do container (o Dockerfile baixa a voz no build, para não depender de rede em execução). Sem nenhum dos dois, o turno segue só em texto: áudio é complemento, nunca requisito.

## O que não se inventa, nem quando é "estimado"

O catálogo é real — 266 imóveis cedidos por uma imobiliária parceira —, e dado real tem buraco. A regra para preencher buraco é a mesma do resto do projeto:

- **Banheiros:** a imobiliária deixa o campo em zero na maior parte dos anúncios. O importador estima (`suítes + 1`) para a busca e o score funcionarem, mas marca o campo em `campos_estimados`, e o card, a legenda da foto e o site **não mostram** número estimado. Estimado serve para filtrar, nunca para afirmar.
- **Rentabilidade:** o catálogo não publica. Ela só é calculada onde existe dado — imóvel anunciado para venda **e** aluguel, aluguel × 12 ÷ preço de venda, os dois valores do próprio anúncio. Para apartamento, que quase nunca é anunciado nas duas, a estimativa usa a mediana de R$/m² de pelo menos 3 apartamentos alugados na mesma cidade; sem comparável, o imóvel fica fora da vitrine de investimento. Todo texto diz "bruta" e "estimada", e o site diz de onde veio o número.
- **Tipo sem quarto:** terreno tem `quartos = 0`. "0 quartos" num card de terreno é dado errado, não neutro — ele simplesmente não aparece.

## Outras defesas

- **Prompt injection é detectada por frase, nunca por LLM** (`core/injecao.py`), em quatro tipos (anular instruções, vazar o prompt, trocar a persona, extrair dados). A mensagem é **marcada**, não bloqueada: bloquear trataria cliente legítimo como atacante; marcar deixa o corretor ver no painel o que foi tentado.
- **O roteiro não entra em laço.** Se o roteador decide a mesma pergunta três vezes seguidas, o campo é dispensado e o fluxo escala (lista as regiões atendidas, amplia a busca, oferece um corretor) — sem isso, um cliente que não responde o que foi perguntado ouvia a mesma pergunta para sempre.

## Observabilidade da própria IA

Toda chamada ao LLM grava uma linha em `agent_traces`: agente, modelo, tokens de prompt/completion, latência e tokens/s — lidos direto da resposta do Ollama (`eval_count`, `eval_duration`), não estimados. Visível numa aba do dashboard. Serve tanto de prova de qualidade (latência real por agente) quanto de guia de otimização (qual chamada é o gargalo).

## Números medidos, não estimados

- Chamada de extração (schema de 9 campos, todos obrigatórios): **~7–12 s**, ~90–100 tokens de saída a ~19 tok/s. É o gargalo atual do turno — decodificação com grammar constraint (JSON Schema) é mais cara que geração livre. Testado e descartado tornar os campos opcionais para reduzir pra ~3 s: o modelo passa a **omitir campos que a mensagem menciona explicitamente**, troca inaceitável para um guardrail anti-alucinação.
- Chamada de resposta: **~2,5–3,5 s**, 15–30 tokens de saída. O custo aqui é quase todo **processamento do prompt**, não geração — medido: com ~1000 tokens de prompt a chamada levava ~12 s; comprimindo para ~800 caiu para ~3 s.
- **Turno completo: ~12 s de média** (pior caso ~15 s, no turno que também faz busca híbrida + embedding). Era ~25 s antes da otimização de latência descrita abaixo.

### Otimização de latência (medida com `agent_traces`, não estimada)

Três mudanças, nesta ordem de impacto:

1. **Resumo e memória saíram do caminho crítico.** O resumo pro corretor custava 4–9 s e rodava a cada turno, **antes** da resposta voltar pro cliente — que nem consome esse dado (é card de dashboard). Hoje roda em thread de fundo (`core/graph.py::_tarefas_de_fundo`); só o reagendamento do follow-up ficou síncrono, porque é DB puro e a corrida com o scanner depende dele.
2. **Resumo regenera a cada 2 turnos, não a cada um.** Efeito colateral medido da mudança 1: a tarefa de fundo disputa a GPU com o turno *seguinte* — extração subia de ~7 s para ~12 s quando havia resumo concorrente. Nos desfechos (visita agendada, encaminhamento) ele sempre regenera, para o corretor receber o card final completo.
3. **Prompt da resposta comprimido** (~1000 → ~800 tokens), sem perder nenhuma regra de humanização ou a técnica de objeção.

Não entrou: trocar por um modelo menor (`qwen2.5:7b`, já em disco) — reabriria o risco de todas as correções de prompt validadas contra o `qwen3.5`.
- Taxa de resposta "ruim" (heurística de `_parece_resposta_ruim`) antes do guardrail de retry: ~2 em 5–6 respostas em teste manual a `temperature=0.4`. Depois do retry + `temperature=0.15`: 6/6 limpo nos testes registrados.

Os testes que sustentam cada número estão em `tests/` — 347 passando, incluindo os adversariais contra o Ollama e o Postgres reais. A correspondência requisito a requisito está em [docs/VALIDACAO_REQUISITOS.md](docs/VALIDACAO_REQUISITOS.md).
