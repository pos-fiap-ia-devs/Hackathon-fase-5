# Validação dos requisitos

Cada item do enunciado do Tech Challenge — Fase 5, com **onde está no
código** e **qual teste comprova**. Conferido contra `pytest` em 13/10/2026:
**371 testes passando**, nenhum falhando.

Os testes rodam contra o **Ollama e o Postgres reais**, sem mock — decisão
explicada em [SOLID.md](SOLID.md) e [ARQUITETURA.md](../ARQUITETURA.md). Os
que dependem de serviço **pulam** com a razão e o comando na mensagem
quando a infra não está de pé, em vez de falhar ([`tests/conftest.py`](../tests/conftest.py)):

```
sem o Postgres no ar  →  273 passed, 98 skipped em 9 s
tudo no ar            →  371 passed em ~15 min
```

Legenda: ✅ feito · ⚠️ parcial, com a limitação escrita · ❌ não feito.

---

## Objetivo do desafio

| # | Requisito | | Onde está | Teste que comprova |
|---|---|---|---|---|
| 1 | Atender leads automaticamente | ✅ | 4 canais no mesmo orquestrador: Telegram (long polling), chat web, widget em site de terceiro, CLI. Todos chamam `core/turno.py::processar_turno` | `test_widget.py` (26) · `test_galeria.py` (6) |
| 2 | Conversa humanizada | ✅ | Persona fixa em [`core/prompts.py`](../app/core/prompts.py); 1 pergunta por mensagem; reconhece o que o cliente disse antes de perguntar o próximo campo; 5 tipos de objeção de venda tratados ([`router.py`](../app/agents/router.py)); despedida e aceite curto reconhecidos | `test_router.py` (17) · `test_autorreferencia.py` |
| 3 | Qualificar clientes | ✅ | [`qualifier.py`](../app/agents/qualifier.py) extrai slots via JSON Schema; score determinístico e **explicável** em [`scoring.py`](../app/core/scoring.py) — nunca pedido ao LLM. O painel abre a conta: cada parcela com pontos, máximo e motivo, mais o que falta perguntar e a prioridade quente/morno/frio. Regra de negócio sobreposta: sem orçamento informado, a prioridade não passa de morno | `test_scoring.py` (5) · `test_scoring_explicavel.py` (29) |
| 4 | Identificar intenção (compra, aluguel, investimento) | ✅ | Campo `intencao` no schema, com regra por padrão de frase. A 1ª versão do prompt classificava o Cenário 1 do enunciado como aluguel; a atual acerta os 3 | `test_qualifier.py::test_cenario_1_compra` · `test_cenario_2_investimento` |
| 5 | Coletar informações relevantes | ✅ | 9 slots: intenção, região, quartos, faixa de preço (mín/máx), urgência, perfil de investidor, ticket, expectativa de retorno. Resposta curta ambígua ("2") é desambiguada pela pergunta pendente | `test_pergunta_vs_preferencia.py` · `test_qualifier.py` |
| 6 | Follow-up automático | ✅ | [`core/followup.py`](../app/core/followup.py) + APScheduler; 3 tentativas com intervalos crescentes; `DEMO_MODE` encurta 30 min/2 h/24 h → 90 s/180 s/300 s para a demo acontecer ao vivo. Fechamento por inatividade roda em paralelo | `test_inatividade.py` (5) |
| 7 | Agendar reuniões ou visitas | ✅ | Ramo B do grafo: escolha do imóvel → horário → nome → telefone → confirmação com corretor real. Entende "sábado de manhã", "dia 8 às 13h". Reagendamento pós-confirmação | `test_reagendamento.py` (5) · `test_resolucao_data.py` (11) |
| 8 | Integrar com base simulada de imóveis | ✅ | 266 anúncios reais de uma imobiliária parceira (294 registros venda/aluguel, 13 cidades, 731 fotos), em Postgres 16 + pgvector. Busca híbrida: filtro SQL (preço, quartos, bairro) + `0.6 × similaridade vetorial + 0.4 × ts_rank` | `test_cidade_nao_atendida.py` · `test_zona_alucinada.py` |
| 9 | Gerar resumos para corretores | ✅ | [`summarizer.py`](../app/agents/summarizer.py), em thread de fundo — medido: custava 4–9 s por turno com o cliente esperando uma resposta que já estava pronta | visível em `/dashboard/lead/{id}` |

---

## Cenários esperados

| Cenário do enunciado | | Como o sistema responde |
|---|---|---|
| **1 — Compra**: *"Estou procurando apartamento na zona sul"* | ✅ | Ordem literal do enunciado em [`router.py::decidir_proximo`](../app/agents/router.py): preço → quartos → região → urgência → apresentar imóveis → agendar. Fluxo completo reproduzido no README |
| **2 — Investimento**: *"Quero investir em imóveis para renda"* | ✅ | Ramo próprio: perfil → ticket → expectativa de retorno → `status=encaminhado` para especialista. Sem atalho de portfólio, porque o desfecho do enunciado é o encaminhamento |
| **3 — Follow-up**: cliente some no meio | ✅ | `follow_up_jobs` guarda a fila; a mensagem de retomada carrega o contexto (nome, interesse, onde parou). Quem pede para parar não recebe mais |

---

## Requisitos funcionais

| Requisito | | Evidência |
|---|---|---|
| Atendimento conversacional | ✅ | 4 canais, um orquestrador |
| Conversa natural | ✅ | `temperature=0.15` — a 0.4 o modelo alucinava "Zona Sul" em conversa que nunca citou região, em 2 de 5 respostas |
| Fluxo humanizado | ✅ | Objeções, despedida e aceite curto detectados em código |
| Continuidade da conversa | ✅ | Resumo rolante ([`core/memory.py`](../app/core/memory.py)) + status do lead; cliente que volta após o fechamento retoma sem perder slots nem a visita marcada |
| Qualificação de leads | ✅ | 9 slots + score 0–100 |
| Agendamento de reuniões | ✅ | `visitas` e `encaminhamentos` |
| Resumo inteligente | ✅ | Card no dashboard |
| Dashboard de acompanhamento | ✅ | `/dashboard` com leads por score, detalhe, observabilidade |

---

## Diferenciais

| Diferencial | | Evidência |
|---|---|---|
| Uso de RAG | ✅ | Busca híbrida pgvector + full-text. O filtro determinístico elimina candidato errado **antes** do ranking semântico — mais barato e mais correto que RAG puro sobre texto não estruturado |
| Memória conversacional | ✅ | Resumo rolante + estado parcial do agendamento entre turnos |
| Multiagentes | ✅ | 5 agentes lógicos num `StateGraph` do LangGraph, 19 nós. [Diagrama gerado do grafo compilado](../ARQUITETURA.md), não desenhado à mão |
| Observabilidade | ✅ | `agent_traces`: 1 linha por chamada de LLM, com tokens e latência **medidos** (`eval_count`/`eval_duration` da resposta do Ollama), não estimados. Painel com p50/p95/pior, tokens/s e quebra por agente |
| Segurança | ✅ | Login argon2id com convite de uso único; sessão server-side; PII mascarada antes de persistir; rate limit; token do bot suprimido do log; detecção e classificação de prompt injection em 4 tipos | 
| Integração com CRM | ✅ | `POST /crm/leads` e `GET /crm/leads.csv` |
| Voice AI | ✅ | **As duas metades, locais.** Entrada: áudio do Telegram transcrito com `mlx-whisper` via Metal. Saída: a Bia responde falando ([`core/tts.py`](../app/core/tts.py)) — `say` no macOS, `piper` no container, sem API paga. Responde em áudio para quem mandou áudio |
| Deploy em cloud | ✅ | Versão publicada em servidor remoto, acessível pela internet: bot no Telegram, painel em `:8000` e site em `:5500` (endereços no [README](../README.md#testar-agora-versão-no-ar)). Dockerfile e compose prontos para replicar |
| Integração com WhatsApp | ❌ | Aprovação de número de negócio não cabe no prazo. O Telegram cobre o mesmo padrão de canal |

Testes dos diferenciais: `test_security.py` · `test_auth.py` (13) ·
`test_auth_rotas.py` (25) · `test_injecao.py` (31) · `test_widget.py` (26) ·
`test_custo.py` (10) · `test_stt.py` (5) · `test_vazamento_prompt.py`

---

## Entregáveis

| Entregável | | Onde |
|---|---|---|
| Repositório do projeto | ✅ | este repo |
| README | ✅ | [README.md](../README.md) |
| Arquitetura da solução | ✅ | [ARQUITETURA.md](../ARQUITETURA.md) · [SOLID.md](SOLID.md) |
| Demonstração funcional | ✅ | `LLM_DEMO=true python -m app.channels.cli` roda o fluxo inteiro em qualquer máquina, sem GPU |
| Pitch técnico | ✅ | [PITCH.md](../PITCH.md) · roteiro do vídeo em [pitchvideo.md](../pitchvideo.md) |
| Explicação da IA utilizada | ✅ | [IA.md](../IA.md) |
| Documento técnico consolidado (PDF) | ✅ | [DOCUMENTO_TECNICO.pdf](DOCUMENTO_TECNICO.pdf) |


---

## O que este projeto faz que o enunciado não pede

- **Inferência 100% local, custo zero.** Nenhum dado do lead sai da máquina.
  Não é add-on de compliance — é efeito estrutural da arquitetura. O painel
  mostra `US$ 0,00` ao lado do contrafactual "se fosse nuvem", calculado
  sobre os tokens realmente medidos.
- **Fato crítico nunca escrito pelo LLM.** Preço, endereço, data e corretor
  são montados em código a partir do banco. Testado adversarialmente:
  `test_adversarial.py` (4) não verifica se a resposta é bonita, verifica se
  ela **mente**.
- **Modo demonstração.** [`core/llm_demo.py`](../app/core/llm_demo.py)
  troca o Ollama por um provedor de regras, para que o fluxo possa ser
  avaliado sem Apple Silicon.
- **Widget embutível** em site de imobiliária, com Shadow DOM e
  `frame-ancestors` no CSP.
- **Bugs reais documentados**, com o número do teste que os pegou — os
  achados de formato (`anyOf` devolvendo `null` em silêncio, chave
  `"intenção"` com acento, `think: false` faltando) estão em [IA.md](../IA.md).
