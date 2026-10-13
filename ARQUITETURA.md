# Arquitetura

## Visão geral

Um processo FastAPI único hospeda tudo: os quatro canais de entrada (Telegram, chat web, widget embutível, CLI), o dashboard do corretor, o endpoint de CRM e o scheduler de follow-up. Todos os canais convergem no mesmo orquestrador — trocar de canal é escrever um adapter fino, não reescrever o agente.

```mermaid
flowchart TD
    TG[Telegram<br/>long polling] --> APP
    WEB[Web chat do lead<br/>HTMX] --> APP
    CLI[CLI fallback] --> APP
    WID[Widget em site<br/>de terceiro] --> APP
    BROWSER[Dashboard do corretor] --> APP

    subgraph APP[Processo único FastAPI]
        TURNO[Orquestrador de turno<br/>LangGraph StateGraph]
        ROUTER[Roteador de intenção]
        QUAL[Qualificador<br/>slots via JSON Schema]
        SEARCH[Busca híbrida]
        SCHED[Agendador / especialista]
        SUM[Resumo p/ corretor]
        STT[Transcrição de áudio<br/>mlx-whisper, sob demanda]
        JOB[APScheduler<br/>follow-up]
    end

    TURNO --> ROUTER & QUAL & SEARCH & SCHED
    TG -.-> STT
    STT -.texto.-> TURNO
    ROUTER --> OLL[Ollama nativo<br/>qwen3.5:9b]
    QUAL --> OLL
    SUM --> OLL
    SEARCH --> PG[(Postgres 16 + pgvector)]
    QUAL --> PG
    SCHED --> PG
    JOB --> PG
```

**Frase central:** os cinco agentes são lógicos, não processos. Cada um é um prompt + um schema Pydantic, todos batendo no mesmo modelo Ollama já residente em memória — arquitetura multiagente com custo de inferência de um único modelo.

## Estrutura de pastas

```
app/
  agents/      router.py           decide o próximo passo do fluxo, 100% em código (sem LLM)
               qualifier.py        extrai slots (JSON Schema) da última mensagem
               search.py           busca híbrida SQL + pgvector
               scheduler_agent.py  extrai escolha de imóvel/horário, monta card e confirmação
               summarizer.py       gera o resumo pro corretor
  core/        graph.py            StateGraph do LangGraph — orquestração
               turno.py            lógica de negócio de cada nó (extração, resposta, resolução de data)
               llm.py              client do Ollama (chat_text, chat_structured), guardrails de saída
               prompts.py          persona e regras fixas
               memory.py           resumo rolante
               followup.py         agendamento e execução de follow-up
               scoring.py          score determinístico do lead
               security.py         mascaramento de PII
               auth.py             login argon2id, sessão server-side, convites
               injecao.py          detecção e classificação de prompt injection
               traces.py           dataclass de observabilidade
               custo.py            custo real x contrafactual de API paga
               stt.py              transcrição de áudio local
               tts.py              resposta em áudio (say / piper)
               llm_demo.py         provedor de regras do modo demonstração
               config.py           settings via .env (pydantic-settings)
  channels/    telegram.py         adapter Telegram (long polling + voice)
               cli.py              adapter CLI
  web/         api.py              FastAPI: dashboard, chat web, widget, CRM, auth, lifespan do bot Telegram
               templates/          Jinja2 + HTMX, zero build step
               static/widget.js    widget embutível (Shadow DOM + iframe)
  db/          schema.sql  repo.py  seed.py
scripts/       preparar_banco.py  importar_orion.py  carregar_catalogo.py  buscar.py  baixar_modelo_stt.sh
site/          build.py            site estático da imobiliária com a Bia embutida
```

Cada agente é uma função pura `(estado) -> schema`, testável sozinha, sem Telegram e sem banco — é a componentização que a rubrica de arquitetura cobra.

## Orquestração: LangGraph

`core/graph.py` expõe um `StateGraph` explícito. Cada nó chama uma função de `core/turno.py` — a lógica de negócio (extração de slots, resolução de datas, guardrails de imóvel) é reaproveitada sem alteração; o LangGraph decide **qual nó roda em qual ordem**, nunca **como** a chamada ao LLM é feita (isso continua em `core/llm.py`, com `httpx` puro — ver [IA.md](IA.md) sobre por que essa camada não usa LangChain).

Diagrama gerado direto do grafo compilado (`core/graph.py::exportar_mermaid()`):

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	extrair_slots(extrair_slots)
	decidir_proximo(decidir_proximo)
	apresentar_imoveis(apresentar_imoveis)
	encaminhar_especialista(encaminhar_especialista)
	listar_regioes(listar_regioes)
	cidade_nao_atendida(cidade_nao_atendida)
	impasse_faixa(impasse_faixa)
	impasse_sem_resultado(impasse_sem_resultado)
	impasse_humano(impasse_humano)
	finalizar_qualificacao(finalizar_qualificacao)
	extrair_escolha(extrair_escolha)
	resolver_escolha(resolver_escolha)
	confirmar_visita(confirmar_visita)
	proxima_pergunta_agendamento(proxima_pergunta_agendamento)
	checar_reagendamento(checar_reagendamento)
	responder_generico(responder_generico)
	despedir(despedir)
	novo_imovel(novo_imovel)
	finalizar(finalizar)
	__end__([<p>__end__</p>]):::last
	__start__ -.-> checar_reagendamento;
	__start__ -.-> despedir;
	__start__ -.-> extrair_escolha;
	__start__ -.-> extrair_slots;
	__start__ -.-> novo_imovel;
	apresentar_imoveis --> finalizar;
	checar_reagendamento -. &nbsp;fim&nbsp; .-> finalizar;
	checar_reagendamento -.-> responder_generico;
	cidade_nao_atendida --> finalizar;
	confirmar_visita --> finalizar;
	decidir_proximo -.-> apresentar_imoveis;
	decidir_proximo -.-> cidade_nao_atendida;
	decidir_proximo -.-> encaminhar_especialista;
	decidir_proximo -.-> finalizar_qualificacao;
	decidir_proximo -.-> impasse_faixa;
	decidir_proximo -.-> impasse_humano;
	decidir_proximo -.-> impasse_sem_resultado;
	decidir_proximo -.-> listar_regioes;
	despedir --> finalizar;
	encaminhar_especialista --> finalizar;
	extrair_escolha -. &nbsp;fim&nbsp; .-> finalizar;
	extrair_escolha -.-> resolver_escolha;
	extrair_slots --> decidir_proximo;
	finalizar_qualificacao --> finalizar;
	impasse_faixa --> finalizar;
	impasse_humano --> finalizar;
	impasse_sem_resultado --> finalizar;
	listar_regioes --> finalizar;
	novo_imovel --> finalizar;
	proxima_pergunta_agendamento --> finalizar;
	resolver_escolha -.-> confirmar_visita;
	resolver_escolha -.-> proxima_pergunta_agendamento;
	responder_generico --> finalizar;
	finalizar --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

**Entrada única, 3 ramos + 1 atalho**, roteados pela aresta de entrada condicional em `lead["status"]` — com uma exceção que vem antes do status: pedido explícito de encerramento (`router.py::pediu_para_encerrar`) cai direto em `despedir`, valha qual for o ramo. Sem isso, um "obrigado" com a visita já marcada fazia o ramo C repetir a confirmação inteira.

- **A — qualificação** (`novo`/`qualificando`): `extrair_slots` → `decidir_proximo` → um dos oito nós terminais. `listar_regioes` e `cidade_nao_atendida` existem porque "onde vocês têm imóvel?" e "quero em Bauru" se respondem com dado do banco, não com mais uma pergunta do roteiro. Os três `impasse_*` são as **saídas anti-laço**: quando a mesma decisão se repete 3 turnos seguidos, `decidir_proximo` deixa de ser consultado e a conversa muda de rota — mostra a faixa de preços real, afrouxa o filtro da busca dizendo o que afrouxou, ou passa o lead a um humano. Sem isso o agente repetia a mesma frase indefinidamente (ver [`tests/test_sem_loop.py`](tests/test_sem_loop.py)).
- **B — escolha de visita** (`aguardando_escolha`): `extrair_escolha` → `resolver_escolha` (guardrail bairro-vs-nome) → `confirmar_visita` ou `proxima_pergunta_agendamento`.
- **C — pós-desfecho** (`visita_agendada`/`encaminhado`): `checar_reagendamento` → se não reagendou, `responder_generico`.

Todo caminho converge no nó comum `finalizar`, que roda três coisas depois de cada turno, independente do ramo: atualiza a memória rolante (`core/memory.py`), gera/atualiza o resumo pro corretor (`agents/summarizer.py`) se já houver intenção definida, e reagenda (ou cancela) o próximo follow-up conforme o status atual do lead (`core/followup.py`).

Sem checkpointer persistente do LangGraph: o Postgres já é a fonte de verdade de slots/mensagens/visitas, um checkpointer junto duplicaria estado.

## Fluxo de um turno

1. Canal (Telegram/web/CLI) recebe a mensagem, resolve/cria o lead por `chat_id` prefixado (`tg:`, `web:`, `cli:`).
2. Se for áudio: `core/stt.py` converte OGG/Opus → WAV, transcreve local (Whisper via MLX), o texto resultante segue o mesmo caminho de uma mensagem digitada — nenhum agente sabe que houve áudio.
3. `processar_turno` invoca o grafo compilado, que roteia pro ramo certo conforme o status do lead.
4. Dentro do ramo: uma chamada de **extração** (JSON Schema, `chat_structured`) atualiza o estado; uma chamada de **resposta** (texto livre, `chat_text`) gera a frase humanizada — nunca a mesma chamada faz as duas coisas.
5. Fatos críticos (preço, endereço, data, corretor) nunca são escritos pelo LLM — são montados em código a partir do banco e concatenados à frase gerada (ver [IA.md](IA.md), guardrails).
6. Nó `finalizar`: memória, resumo, follow-up.
7. Resposta volta pro canal; `mensagens` grava o turno inteiro (usuário e agente) com `origem` (`texto`/`audio`/`followup`).

## Modelo de dados

```sql
imoveis         294 registros (266 anúncios reais; venda e aluguel do mesmo imóvel são linhas separadas), 13 cidades; preço, quartos, cidade/zona/bairro,
                descrição, embedding(768), tsvector gerado
leads           chat_id único, canal, status, score, estado parcial do agendamento
slots           1:1 com lead — intenção, preço, quartos, região, urgência, perfil de investidor
mensagens       histórico completo, com origem (texto/audio/followup)
memoria         1:1 com lead — resumo rolante + contador de turnos já resumidos
visitas         desfecho do cenário compra/aluguel
encaminhamentos desfecho do cenário investimento
resumos         1:1 com lead — card pro corretor
agent_traces    1 linha por chamada de LLM — latência, tokens, tokens/s
follow_up_jobs  fila de reengajamento — pendente/enviado/cancelado
```

Índices: GIN em `imoveis.busca` (full-text), B-tree em `(finalidade, zona, preco, quartos)` para o filtro estruturado, e em `follow_up_jobs (executar_em) WHERE status='pendente'` pro scan do scheduler. **Sem índice vetorial** — com 200 linhas, scan sequencial é mais rápido que `ivfflat` (que ainda exigiria um `lists` compatível com a contagem de linhas); HNSW entra a partir de ~10 mil imóveis, sem mudar a query.

## Busca híbrida

Preço, quartos, bairro e finalidade são estruturados — filtrados com `WHERE` primeiro. O pgvector ranqueia só o descritivo livre ("varanda gourmet", "pé na areia"), combinado com busca full-text:

```
score_final = 0.6 * similaridade_vetorial + 0.4 * ts_rank
```

Mais barato e mais correto que RAG puro sobre texto não estruturado — o filtro determinístico elimina candidatos errados antes do ranking semântico entrar em cena.

## Observabilidade

Toda chamada ao LLM grava uma linha em `agent_traces`: agente, modelo, tokens de prompt/completion, latência e tokens/s — números reais, lidos direto da resposta do Ollama (`eval_count`, `eval_duration`), não estimados. O dashboard tem uma aba dedicada (`/dashboard/traces`) que faz polling a cada 3 s. É a base do argumento de escalabilidade: "o gargalo é medido, não estimado".

## Escalabilidade

1. **Aplicação stateless** — todo estado mora no Postgres. N réplicas do FastAPI atrás de um balanceador não exigem mudança de código.
2. **O LLM já está atrás de HTTP** — trocar `OLLAMA_HOST` por um cluster com GPU é variável de ambiente.
3. **O gargalo é medido** — `agent_traces` tem latência e tokens/s reais por chamada.
4. **O único componente single-node é o scheduler de follow-up** (APScheduler in-process). `follow_up_jobs` já é tabela — `SELECT ... FOR UPDATE SKIP LOCKED` vira fila distribuída sem trocar de tecnologia.
5. **Busca vetorial** — scan sequencial é a escolha certa com algumas centenas de imóveis; HNSW entra a partir de ~10 mil, sem mudar a query.

## Segurança

- PII (telefone) mascarada antes de persistir (`core/security.py::mascarar_telefone` — mantém DDD + 4 últimos dígitos).
- Prompt injection: mensagem do lead nunca concatenada no system prompt, sempre em turno `user` — o LLM não confunde instrução do sistema com texto do cliente.
- Token do bot nunca em log de texto puro — o logger do `httpx` (que loga a URL inteira da requisição, e o Telegram embute o token na URL: `/bot<TOKEN>/metodo`) é explicitamente suprimido pra `WARNING`.
- Rate limit por `chat_id`.
- Dado do lead nunca sai da máquina — modelo local, banco local. Efeito estrutural da arquitetura, não um add-on de compliance.

## Decisões de design e trade-offs

| Decisão | Alternativa considerada | Por quê |
|---|---|---|
| Um processo FastAPI para tudo (Telegram + dashboard + chat + CRM + scheduler) | Processos separados por canal | Menos partes móveis pra subir/derrubar numa demo; o diagrama da seção anterior sempre previu isso |
| LangGraph `StateGraph` explícito | `match`/`if` aninhado à mão | Componentização visível, diagrama gerado do grafo real (não desenhado à mão), sem reescrever a lógica de negócio já validada |
| `httpx` puro pro Ollama, não LangChain `with_structured_output` | Usar a abstração do LangChain também na chamada ao LLM | O `with_structured_output` do LangChain gera o schema pela rota padrão do Pydantic (`anyOf`), o mesmo formato que causava campo `null` silencioso no llama.cpp (ver IA.md) — trocar essa camada reabriria um risco já fechado |
| Fatos críticos (preço, data, corretor) montados em código, nunca escritos pelo LLM | Confiar em prompt + validação pós-geração | Testado: regra de prompt sozinha não segura um modelo de 9B (taxa de erro caiu, não zerou) — tirar o LLM do caminho do dado é garantia estrutural, não comportamental |
| Sem índice vetorial | `ivfflat` desde o início | Com ~300 linhas o índice não ajuda e ainda exige `lists` calibrado; decisão documentada, não omissão |
| Follow-up via APScheduler in-process | Fila externa (Redis/Celery) desde o início | Menos um ponto de falha numa POC de 1 pessoa; a tabela `follow_up_jobs` já está desenhada pra virar fila distribuída depois, sem reescrever |
| STT com timeout duro em thread separada | Deixar `mlx_whisper.transcribe` rodar sem limite | Download do modelo via HF Hub travou indefinidamente em teste real (rede específica, ver IA.md) — sem timeout, um único áudio travaria o turno (e o processo do bot) pra sempre |

Os achados que motivaram cada decisão, com os números medidos, estão em [IA.md](IA.md). A correspondência requisito → código → teste está em [docs/VALIDACAO_REQUISITOS.md](docs/VALIDACAO_REQUISITOS.md).
