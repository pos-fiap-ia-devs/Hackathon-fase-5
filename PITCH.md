# Pitch técnico

## O problema, em cinco dores

Uma imobiliária perde lead hoje por cinco motivos, direto do enunciado do desafio: tempo de resposta elevado, falta de acompanhamento, atendimento manual, dificuldade em priorizar lead quente, sobrecarga operacional do corretor. Cada um tem um contraponto direto neste projeto — não uma menção, uma implementação:

| Dor | Resposta | Prova |
|---|---|---|
| Tempo de resposta elevado | Resposta automática em segundos, 24/7 | latência real em `agent_traces`; indicador "1ª resposta" no painel |
| Falta de acompanhamento | Follow-up automático, 3 tentativas, sem humano | `follow_up_jobs` executados; indicador "recuperados" |
| Atendimento manual | Qualificação inteira sem corretor | turnos até o lead qualificado |
| Dificuldade em priorizar lead quente | Score determinístico e explicável, funil Kanban | cada ponto do score com o motivo escrito ao lado |
| Sobrecarga do corretor | Corretor só entra com o resumo pronto e a visita marcada | card de resumo + `visitas` |

## O que foi construído

Um agente SDR imobiliário, a **Bia**, que atende por Telegram (texto e áudio, e responde em áudio), por chat web, por um **widget embutível** em qualquer site e por CLI — adapters diferentes, um orquestrador só. Ela qualifica o lead (compra, aluguel ou investimento), busca imóveis, agenda visita ou encaminha a um especialista, reengaja automaticamente quem parou de responder, gera resumo para o corretor e expõe tudo num painel com funil Kanban, indicadores e observabilidade em tempo real.

**Os imóveis são reais.** O catálogo — 266 imóveis em 13 cidades, 731 fotos, com descrição, preço, condomínio e IPTU — foi cedido por uma imobiliária parceira, com autorização, e importado do próprio site dela por dado estruturado (`schema.org`), não por raspagem de tela. O enunciado pede uma base *simulada*; usar uma real é ir além, e é o que deixa a demonstração honesta: todo imóvel que a Bia mostra existe, com a foto do próprio anúncio.

100% local: o modelo é um `qwen3.5:9b` rodando via Ollama na própria máquina, o banco é Postgres com pgvector — nenhum dado do lead sai da máquina, nenhuma chamada a API paga.

## Arquitetura, em uma frase

Cinco agentes lógicos — não processos — cada um um prompt e um schema Pydantic, todos batendo no mesmo modelo já residente em memória, orquestrados por um `StateGraph` do LangGraph. O canal é um detalhe de adapter: trocar Telegram por WhatsApp é escrever um adapter novo, não reescrever o agente. Um único processo FastAPI hospeda tudo — bot, painel, chat web, widget, CRM, scheduler de follow-up.

## O que diferencia esta solução

- **Guardrail estrutural, não só de prompt.** O achado mais repetido da implementação: regra de prompt reduz alucinação, não elimina. Fato crítico (preço, data, corretor, metragem) nunca é escrito pelo LLM — é montado em código a partir do banco. O modelo gera a frase, nunca o número.
- **Nenhum número inventado, nem os "estimados".** O catálogo não publica rentabilidade, então ela é calculada só onde há dado: aluguel e venda do *mesmo* imóvel, ambos anunciados. Banheiro que a imobiliária não cadastrou é estimado para a busca, mas nunca aparece para o cliente como fato.
- **Busca híbrida, não RAG puro.** Filtro estruturado (`WHERE` em preço/quartos/tipo/bairro) primeiro, ranking semântico (pgvector + full-text) só no texto descritivo. Mais barato e mais correto que jogar tudo pro vetor.
- **Memória sem inchar o contexto.** Resumo rolante — a conversa pode durar horas sem o prompt crescer sem limite.
- **Observabilidade própria.** Toda chamada ao LLM grava latência, tokens e tokens/s reais, e o painel mostra quanto a mesma conversa teria custado numa API paga — o custo real é zero, e isso é medido, não dito.
- **Segurança de verdade.** Login com argon2id e sessão no servidor, convite por link, e tentativa de prompt injection detectada e marcada no painel.
- **7 dos 9 diferenciais opcionais do PDF entregues**: RAG híbrido, memória conversacional, multiagentes, Voice AI (transcrição local via `mlx-whisper` e resposta em áudio via `say`/`piper`), integração com CRM (entrada e exportação de leads), observabilidade e segurança. Ficaram de fora WhatsApp (o canal é Telegram) e deploy em cloud (roda local com Docker).
- **LGPD por arquitetura, não por política.** O dado nunca sai da máquina porque o modelo e o banco são locais — não é uma promessa, é uma consequência de onde os componentes rodam.

## Roteiro de demonstração (6 minutos)

| Tempo | Cena |
|---|---|
| 0:00 | As cinco dores, painel na tela: indicadores e funil Kanban |
| 0:20 | Cenário 1 (compra), pelo Telegram, com a frase do enunciado: *"Estou procurando apartamento na zona sul"* — a Bia pergunta faixa de preço, quartos e urgência, e os slots aparecem no painel |
| 1:10 | Uma das respostas em áudio — a transcrição aparece no painel com o ícone de origem, e a Bia responde em áudio |
| 1:40 | A Bia **admite** que não tem apartamento com esses critérios na zona sul, em vez de inventar um; o cliente aceita Barueri e ela apresenta um apartamento real, com foto do anúncio e código |
| 2:20 | O cliente escolhe e passa nome e telefone — visita marcada com corretor e data resolvida no calendário; o cartão anda no Kanban |
| 2:40 | Cenário 3 (follow-up): parar de responder, deixar correndo em segundo plano |
| 3:00 | Cenário 2 (investimento), outro chat: *"Quero investir em imóveis para renda"* — perfil → ticket → retorno → encaminhado a especialista, com o resumo pronto |
| 4:30 | Follow-up do cenário 1 chega sozinho, citando o que o lead já tinha dito |
| 5:00 | Observabilidade: latência real, tokens/s, custo equivalente em API paga, e uma tentativa de prompt injection marcada |
| 5:30 | Site da imobiliária com a Bia embutida, card de resumo pro corretor, argumento de LGPD |

O follow-up chegando sozinho enquanto a apresentação segue em outro assunto é o momento que prova automação de verdade — não é o chat que impressiona, é o sistema agindo sem ninguém pedir. E a cena das 1:40 é a que prova o guardrail: diante de uma pergunta sem resposta no estoque, a Bia diz que não tem.

## Por que isso poderia existir no mercado

O enunciado fecha com uma frase que funciona como critério de desempate: *"construam algo que poderia realmente existir no mercado."* Quatro argumentos sustentam isso aqui:

1. **Já roda sobre o estoque de uma imobiliária real** — o catálogo, as fotos e os preços vêm do sistema dela, e reimportar é um comando (`./atualizar_catalogo.sh`).
2. **Custo marginal por lead é zero** — modelo local, sem custo de API por mensagem.
3. **LGPD by design** — o dado nunca sai da máquina; não é feature adicional, é onde os componentes rodam.
4. **O gargalo está medido, não estimado** — `agent_traces` tem número real de latência e throughput, base concreta para qualquer decisão de escalar depois.

Documentação técnica completa: [README.md](README.md) (como rodar) · [ARQUITETURA.md](ARQUITETURA.md) (design e trade-offs) · [IA.md](IA.md) (modelo, prompts, guardrails, achados reais de teste) · [docs/VALIDACAO_REQUISITOS.md](docs/VALIDACAO_REQUISITOS.md) (cada requisito do enunciado e onde ele está).
