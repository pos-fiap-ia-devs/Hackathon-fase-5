# Princípios de design no código

Este documento mapeia princípios de arquitetura a **arquivo, função e linha
reais** deste projeto. Onde um princípio foi aplicado só em parte — ou
deliberadamente não aplicado — está escrito assim, com o motivo.

Projeto em Python funcional-com-módulos, não em classes: a maior parte dos
"agentes" são funções puras, não objetos. Os princípios valem igual; o que
muda é que a unidade de responsabilidade é o **módulo e a função**, não a
classe. Onde a formulação clássica fala em "classe", leia "módulo".

---

## Separação em camadas

```
app/channels/   adapters de entrada   Telegram · web · CLI
app/web/        entrega HTTP          rotas, templates, guard de sessão
app/core/       orquestração + regra  grafo, turno, guardrails, segurança
app/agents/     unidades de decisão   router · qualifier · search · scheduler · summarizer
app/db/         persistência          repo.py, schema.sql, seed.py
```

A dependência aponta **sempre para dentro**: `channels` conhece `core`,
`core` conhece `agents` e `db`, e nada em `agents/` importa Telegram, HTTP
ou Jinja. Verificável:

```bash
$ grep -rl "telegram\|fastapi\|jinja" app/agents/ app/core/scoring.py app/core/security.py
# (nenhum resultado)
```

A prova prática é que os três canais ([`channels/telegram.py`](../app/channels/telegram.py),
[`web/api.py`](../app/web/api.py), [`channels/cli.py`](../app/channels/cli.py))
chamam a **mesma** função `processar_turno` e nenhum deles contém regra de
negócio. Trocar de canal é escrever um adapter fino — o widget
([`/embed`](../app/web/api.py)) foi adicionado assim, sem tocar em nenhum
agente.

---

## S — Responsabilidade única

Cada agente tem uma pergunta só:

| Módulo | Responde |
|---|---|
| [`agents/router.py`](../app/agents/router.py) | qual é o próximo passo do fluxo |
| [`agents/qualifier.py`](../app/agents/qualifier.py) | que dados a última mensagem traz |
| [`agents/search.py`](../app/agents/search.py) | quais imóveis atendem esses dados |
| [`agents/scheduler_agent.py`](../app/agents/scheduler_agent.py) | que imóvel e horário o cliente escolheu |
| [`agents/summarizer.py`](../app/agents/summarizer.py) | o que o corretor precisa saber |

A divisão mais importante não é entre agentes, e sim entre **decidir** e
**redigir**. `router.py` decide o fluxo inteiro **sem chamar o LLM** —
`decidir_proximo(slots, mensagem)` é função pura que devolve um rótulo
(`"perguntar_preco"`). Só depois `core/turno.py` pede ao modelo que
escreva a frase daquele rótulo. O LLM nunca decide o que perguntar.

Mesma separação em [`core/`](../app/core/): `scoring.py` só calcula score,
`security.py` só mascara PII do lead, `auth.py` só trata de quem opera o
painel, `injecao.py` só detecta e classifica. Nenhum deles conhece banco ou
HTTP, e todos são testáveis sozinhos — é o motivo de 178 dos 242 testes
rodarem sem Postgres e sem Ollama.

**Onde não está limpo:** [`core/turno.py`](../app/core/turno.py) tem 1086
linhas e concentra extração, resolução de data e guardrails de imóvel. É o
maior módulo do projeto e o candidato natural a ser quebrado em três.
Está assim porque o grafo foi extraído *dele* ([`core/graph.py`](../app/core/graph.py))
sem reescrever a lógica já validada — preservar venceu redesenhar.

---

## O — Aberto para extensão, fechado para modificação

O caso mais claro é o grafo. Adicionar um comportamento novo é adicionar um
**nó**, não editar os existentes:

```python
# app/core/graph.py
g.add_node("cidade_nao_atendida", _no_cidade_nao_atendida)
g.add_conditional_edges("decidir_proximo", _rota_pos_decisao, {...})
```

Os nós `listar_regioes`, `cidade_nao_atendida` e `despedir` entraram assim,
depois do grafo pronto, sem alterar `extrair_slots` nem `apresentar_imoveis`.

Outro: as objeções de venda. [`router.py`](../app/agents/router.py) tem
`_OBJECOES` e `TRATATIVA_POR_OBJECAO` como dados, não como `if`. Uma
objeção nova é uma entrada no dicionário.

Vale a pena dizer *por que* elas viraram dados em código em vez de regra de
prompt: como regra fixa no system prompt, a tratativa competia com a
instrução do turno — mais saliente, porque vinha na mensagem `user` — e
perdia. "Já tenho corretor" virava um "Entendido!" seco seguido da próxima
pergunta do roteiro. Detectando em código, a tratativa vira a instrução
**principal** do turno.

---

## L — Substituição de Liskov

O exemplo real é o **canal**. Telegram, web e CLI produzem o mesmo
`chat_id` prefixado (`tg:`, `web:`, `cli:`, `widget:`) e chamam a mesma
`processar_turno`. Nenhum deles é um caso especial para o orquestrador, e
nenhum enfraquece o contrato: o que vale para um vale para os quatro.

Onde a substituição **falhava**, foi corrigido: o scanner de follow-up
assumia que todo lead com `canal='telegram'` tinha `chat_id` numérico. Lead
gravado fora dessa suposição fazia `int()` estourar dentro do job, a cada
30 s, para sempre — 71 ocorrências no `run.log`. A correção
([`_chat_id_telegram`](../app/channels/telegram.py)) foi estreitar a
pré-condição em vez de confiar nela, com
[7 testes](../tests/test_followup_chat_id.py) cujo contrato é "esta função
nunca levanta exceção".

---

## I — Segregação de interface

`core/llm.py` expõe três funções, e cada chamador usa só a que precisa:

- `chat_structured` — extração com JSON Schema (`qualifier`, `scheduler`)
- `chat_text` — redação livre (`responder`)
- `embed_text` — embeddings (`seed`, `search`)

`agents/qualifier.py` não sabe que existe geração de texto livre;
`summarizer.py` não sabe que existe grammar-constrained decoding. Uma
interface "LLMClient" única, com tudo, forçaria todo chamador a conhecer
tudo.

Mesma ideia em [`db/repo.py`](../app/db/repo.py): funções nomeadas por
intenção (`get_slots`, `salvar_escolha_visita_parcial`,
`marcar_convite_usado`), não um `execute(sql)` genérico. O chamador não
monta SQL nem conhece o schema.

---

## D — Inversão de dependência

O ponto mais forte do projeto, e o mais recente.

`core/llm.py` virou um ponto de troca: o mesmo contrato
`(agente, system, user) -> (texto, TraceInfo)` é servido pelo Ollama real
ou pelo provedor de regras de [`core/llm_demo.py`](../app/core/llm_demo.py),
decidido por configuração:

```python
# app/core/llm.py
if settings.llm_demo:
    return llm_demo.chat_text(agente=agente, system=system, user=user)
```

Nenhum agente sabe qual está rodando. É o que permite `LLM_DEMO=true`
executar o fluxo inteiro sem GPU, em qualquer máquina.

Outras inversões:

- **O LLM já está atrás de HTTP.** `OLLAMA_HOST` é variável de ambiente;
  apontar para um cluster com GPU não muda uma linha de código.
- **Config injetada, não lida de dentro.** Tudo passa por
  `get_settings()` ([`core/config.py`](../app/core/config.py)), com
  `@lru_cache`. Nenhum módulo lê `os.environ` direto.
- **Sessão server-side, não JWT** ([`core/auth.py`](../app/core/auth.py)):
  a verificação depende de uma tabela que se pode apagar, não de um token
  autocontido — é o que permite desativar um usuário e cortar o acesso na
  hora.

**Onde não está invertido, de propósito:** `repo.py` fala psycopg direto,
sem interface `IRepository` nem ORM. Com um único banco e sem plano de
trocar, a abstração seria cerimônia — e a consulta de busca híbrida usa
`pgvector` + `ts_rank` numa query só, que um ORM esconderia sem ganho. A
troca que o projeto precisava mesmo poder fazer (o LLM) é a que está
invertida.

---

## O princípio que mais pesou aqui

Nenhum dos cinco, e sim este: **fato crítico nunca é escrito pelo LLM.**

Preço, endereço, data, nome do corretor e telefone são montados em código a
partir do banco e concatenados à frase gerada. O modelo escreve o texto em
volta, nunca o dado.

Não é preferência estética — foi medido. Pedir ao prompt que não invente
derrubou a taxa de erro, mas não zerou: um modelo de 9B inventava desconto,
confirmava preço falso e citava bairro que o cliente nunca mencionou. A
garantia virou **estrutural** em vez de comportamental:
[`_frase_tem_fato`](../app/core/turno.py) descarta a frase do LLM se ela
contiver número, e os quatro sanitizadores de
[`core/llm.py`](../app/core/llm.py) cortam vazamento de prompt, zona
alucinada e autorreferência.

A prova está em [`tests/test_adversarial.py`](../tests/test_adversarial.py),
que não testa se a resposta é bonita — testa se ela **mente**, conferindo
contra o banco cada valor citado.

---

Ver também: [ARQUITETURA.md](../ARQUITETURA.md) (camadas, grafo e fluxos) ·
[VALIDACAO_REQUISITOS.md](VALIDACAO_REQUISITOS.md) (requisito → código → teste).
