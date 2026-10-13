# Site da imobiliária

Site estático da **Horizonte Imóveis**, gerado a partir dos imóveis do banco (catálogo real de uma imobiliária parceira, 266 anúncios). Existe para mostrar a Bia atendendo **dentro do site de uma imobiliária** — que é onde ela viveria de verdade — em vez de só numa página de chat solta.

Preço, endereço, metragem e código vêm do Postgres, nunca de template: é o mesmo guardrail do card do agente. Dado de imóvel não se inventa, nem no chat nem na vitrine.

## Rodar

```bash
docker compose up -d                      # o banco precisa estar no ar
python site/build.py                      # gera site/dist/
python -m http.server 5500 --directory site/dist
```

Abra **http://localhost:5500**.

Para o chat funcionar, a aplicação também precisa estar rodando (`./run.sh` ou `uvicorn app.web.api:app --port 8000`) — é de lá que vem o `widget.js`. Sem ela o site abre normal, só a bolha não responde.

Publicando em outro lugar:

```bash
python site/build.py --app https://seu-app.exemplo.com
```

## O que é gerado

| Página | Conteúdo |
|---|---|
| `index.html` | Hero, busca, destaques de venda e locação, bairros, institucional |
| `comprar.html` · `alugar.html` | Listagem completa, com filtro por bairro no cliente |
| `imovel/CODIGO.html` | Uma página de detalhe por imóvel do catálogo |

A busca por código leva direto à página: digitar `CA0432-ORIO` abre `imovel/CA0432-ORIO.html`.

## A Bia no site

Uma linha, antes de `</body>` — a mesma que qualquer imobiliária usaria:

```html
<script src="http://localhost:8000/widget.js" data-brand="Horizonte Imóveis" defer></script>
```

Os botões do site chamam a API do widget com a mensagem já pronta:

| Botão | O que abre o chat dizendo |
|---|---|
| **Tenho interesse** (card) | *"Quero ver o CA0432-ORIO"* |
| **Agendar visita** (detalhe) | *"Quero agendar uma visita ao CA0432-ORIO"* |
| **Tirar dúvidas** | *"Tenho dúvidas sobre o CA0432-ORIO"* |

É por isso que o [código do imóvel](../README.md#código-do-imóvel) existe: o visitante clica, a Bia já sabe de qual imóvel se trata, e o código continua valendo se ele voltar amanhã.

## Design

Uma cor de marca (`#0e41d5`) e uma escala de cinzas. Nada de segunda cor decorativa — num site de imóvel quem puxa o olho tem que ser a foto e o preço, e cor demais compete com os dois.

Sombra em vez de borda nos cartões: borda em grade cria uma malha de linhas que cansa; sombra separa sem desenhar. Tipografia em escala estreita, `clamp()` no que é grande, zero media query para texto.

HTML puro, uma folha de estilo, nenhum build de JS. O único script da página é o `widget.js` do projeto.

## Fotos

São as do próprio anúncio, do catálogo real de uma imobiliária parceira, as mesmas que o agente envia no chat. A marca d'água da origem está desfocada e as imagens de terceiros foram removidas — ver `scripts/borrar_marca.py` e `scripts/achar_terceiros.py`.

`foto_do_card` desloca a escolha pela posição na grade, o que importa pouco agora: cada imóvel tem fotos próprias. Ele continua escolhendo entre as fotos do mesmo imóvel.
