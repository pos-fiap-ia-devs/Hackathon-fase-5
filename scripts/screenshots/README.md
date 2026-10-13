# Capturas de tela

Refaz as imagens de [`docs/imagens/`](../../docs/imagens/README.md) com o mesmo enquadramento:
painel em 1440 px de largura (escala 1,5×), chat em 430 px (escala 2×, formato de celular) e o
widget numa janela de 1100 px.

Ferramenta de desenvolvimento, **só no host**. Usa o Google Chrome já instalado (`CHROME_PATH` para
apontar outro) via `playwright`, que fica no extra `shots` — fora das dependências da aplicação e
fora da imagem Docker. Não precisa do `playwright install`: não há download de navegador.

```bash
uv pip install -e ".[shots]"
```

Com a aplicação no ar (`./run.sh`, ou `uvicorn app.web.api:app --port 8000`), todas as imagens:

```bash
python -m scripts.screenshots.shoot
```

Só algumas:

```bash
python -m scripts.screenshots.shoot dashboard.png widget.png
```

## O estado que as imagens mostram

O script semeia o que precisa, em [`conversas.py`](conversas.py): um usuário de painel só de
desenvolvimento e um **funil** de leads em etapas diferentes — compra com visita agendada (quente),
investimento encaminhado, aluguel em qualificação, um lead frio recém-chegado e uma tentativa de
prompt injection. Mostrar um lead sozinho não demonstraria priorização nenhuma.

Também **poda detritos**: cada visita a `/chat` cria um lead `web:<uuid>`, então rodar a suíte algumas
vezes enche o painel de linhas "encerrado, score 0". Numa captura isso vira uma afirmação falsa —
parece um produto cheio de lead morto. O filtro só pega lead com score 0 e no máximo uma mensagem;
conversa de verdade nunca casa com ele.

`--refazer` apaga as conversas de captura e as recria do zero.

## Qual modelo

Padrão: **Ollama real**. O provedor de regras devolve `tokens=None`, e `observabilidade.png` sairia
com `US$ 0.0000 · 0 tokens · p95 —` — justamente os números que aquela imagem existe para mostrar.

`--demo` usa o provedor de regras ([`app/core/llm_demo.py`](../../app/core/llm_demo.py)): conversa
determinística, refazível em máquina sem GPU, com o painel de observabilidade sem número. Serve para
as telas de conversa.

## Depois de refazer

**Olhe as imagens antes do commit.** Uma conversa que mudou de tamanho pode cortar a parte que
interessa — ajuste `altura` ou `rolar_ate` em [`shots.json`](shots.json), que é onde mora a
configuração de cada captura (a URL, o que esperar carregar, até onde rolar).

Se a mudança altera algo da coluna "Refazer quando muda" do
[manifesto](../../docs/imagens/README.md), refaça a imagem **no mesmo commit**.
