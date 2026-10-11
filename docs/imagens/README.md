# Capturas de tela

As imagens que o [README](../../README.md) e a [matriz de requisitos](../VALIDACAO_REQUISITOS.md)
mostram. São da aplicação rodando de verdade, contra o Postgres com o seed de 200 imóveis.

**Como refazer:** [`scripts/screenshots/`](../../scripts/screenshots/README.md) tira todas, ou só as
que você nomear, com o mesmo enquadramento.

**Com qual modelo.** Por padrão o script semeia as conversas com o **Ollama real**, e é de propósito:
em modo demonstração o provedor de regras devolve `tokens=None` e latência de microssegundos, então
`observabilidade.png` sairia com `US$ 0.0000 · 0 tokens · p95 —`. Os números medidos são o conteúdo
daquela imagem; publicá-la zerada documentaria um painel que não funciona.

`python -m scripts.screenshots.shoot --demo` usa o provedor de regras
([`app/core/llm_demo.py`](../../app/core/llm_demo.py)): a conversa fica determinística e as telas de
conversa podem ser refeitas em máquina sem GPU — ao custo de o painel de observabilidade ficar sem
número. Em qualquer dos dois, o imóvel, o preço, o corretor e a data vêm do Postgres.

**Quando refazer.** Uma imagem desatualizada é uma afirmação falsa sobre o produto. Se uma mudança
altera algo da coluna "Refazer quando muda", refaça a imagem **no mesmo commit** — senão o README
passa a mostrar uma tela que não existe mais.

| Arquivo | O que mostra | Refazer quando muda |
|---|---|---|
| `dashboard.png` | Lista de leads ordenada por score, com o selo **quente/morno/frio**, status, intenção, região e canal | As colunas da tabela, o selo de prioridade, as faixas de `SCORE_QUENTE`/`SCORE_MORNO` em [`core/scoring.py`](../../app/core/scoring.py), o nav do painel |
| `lead-detalhe.png` | Detalhe do lead com o **score aberto**: cada parcela com pontos, máximo, barra e motivo, mais "falta perguntar" e o resumo pro corretor | A fórmula ou as parcelas do score; os rótulos de `CAMPOS_POR_INTENCAO`; o bloco de resumo; os slots exibidos |
| `observabilidade.png` | Aba de traces: **custo real US$ 0,00** ao lado do contrafactual "se fosse nuvem", custo por conversa, latência p50/p95/pior, tokens/s e quebra por agente | Os cartões de indicador; as parcelas do custo em [`core/custo.py`](../../app/core/custo.py); os preços de referência do `.env`; as colunas da tabela por agente |
| `chat-web.png` | Chat do lead no navegador, fim da conversa de compra: qualificação, cartões de imóveis com foto e preço, e a confirmação da visita | O visual do chat ou da galeria; as frases do roteiro de compra; o formato do cartão de imóvel; o texto de confirmação de visita |
| `widget.png` | A Bia embutida num site de imobiliária de terceiro: bolha flutuante e o chat aberto no iframe, com a marca do site | O `widget.js` (bolha, painel, cores); a página `/embed`; o cabeçalho com `data-brand` |
| `injection.png` | Observabilidade com uma tentativa de **prompt injection** marcada: tipo classificado e o texto cru que o cliente enviou | Os tipos de `_PADROES` em [`core/injecao.py`](../../app/core/injecao.py); os rótulos; o bloco na aba de traces |

As fotos dos imóveis vêm do `loremflickr.com` (aleatórias, por `lock`) e não têm relação com o imóvel
real. Os leads que aparecem nas listas são do banco de desenvolvimento.
