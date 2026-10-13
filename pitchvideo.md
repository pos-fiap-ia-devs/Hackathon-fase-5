# Pitch em vídeo — guia de gravação

Guia para gravar o vídeo do pitch sem erro na frente da câmera. Ele diz **o que preparar, o que digitar, o que falar e o que fazer se algo sair diferente**.

Todas as mensagens deste guia foram rodadas contra o modelo real e o catálogo atual, com o resultado registrado aqui. Mensagem fora delas pode funcionar, mas não foi testada.

> **Duração alvo: 6 a 7 minutos.** O enunciado não fixa limite; se a FIAP informar um, corte primeiro a cena 6 e depois a 7.

---

## 1. Por que estas cidades

O catálogo é real (266 imóveis de uma imobiliária parceira), e ele é forte em poucos lugares. Escolher a cidade errada no vídeo é o erro mais provável.

| Lugar | O que tem | Use para |
|---|---|---|
| **Barueri** | 88 imóveis, apartamentos em Jardim Tupanci, Alphaville, Tamboré | cenário 1, depois que o cliente aceita outra região |
| **Santana de Parnaíba** | 73 imóveis, muitas casas em Aldeia da Serra | plano B (aluguel de casa) |
| **Zona sul de SP** | 3 imóveis, **nenhum apartamento** | só a frase de abertura do cenário 1, de propósito |

**Não cite:** Campinas, Moema, Santo André, Sorocaba, Ribeirão Preto. Essas cidades eram da base antiga e a Bia vai responder que não atende.

---

## 2. Antes de gravar (checklist)

Faça tudo isto na ordem, com 15 minutos de folga.

**Ambiente**
1. Feche tudo que pesa na máquina: navegador com muitas abas, Docker de outros projetos, **e não rode testes**. O modelo divide a GPU com o que estiver aberto, e a resposta fica mais lenta.
2. Desligue notificações do sistema (Não Perturbe) e do celular.
3. No `.env`, confirme as linhas abaixo. **Se alguma não existir, acrescente.** O `run.sh` só cria o `.env` a partir do `.env.example` quando ele ainda não existe, então um `.env` antigo não recebe as variáveis novas — e sem `TTS_ENABLED` a Bia simplesmente não fala, sem erro nenhum:
   ```
   DEMO_MODE=true          # follow-up chega em ~2 min, não em 30 min
   TTS_ENABLED=true        # para a Bia responder em áudio
   STT_ENABLED=true        # para entender o áudio que você mandar
   TELEGRAM_BOT_TOKEN=...  # o do bot que vai aparecer no vídeo
   ```
3a. **Um bot só.** Se o projeto estiver rodando em outra máquina com o mesmo `TELEGRAM_BOT_TOKEN`, feche lá. Com dois rodando, o Telegram entrega cada mensagem para um deles ao acaso — e o outro pode não ter a transcrição instalada. No `run.log`, o sinal é `Conflict: terminated by other getUpdates request`.
3b. **Transcrição instalada** (só uma vez por máquina). Sem isto a Bia responde *"Não consegui entender o áudio"*, sem outro aviso:
   ```bash
   source .venv/bin/activate
   uv pip install -e ".[stt]"          # pacote mlx-whisper (só Apple Silicon)
   bash scripts/baixar_modelo_stt.sh   # modelo de 459 MB, no caminho do STT_MODEL_PATH
   ```
4. Suba tudo. Se o sistema já estava rodando, **pare antes**: o `.env` só é lido na subida.
   ```bash
   ./stop.sh && ./run.sh
   ```
5. Confira que está de pé:
   - http://localhost:8000/health → `{"status":"ok"}`
   - http://localhost:8000/dashboard → faça login
   - http://localhost:5500 → o site da imobiliária abre com fotos

**Banco limpo**

6. Limpe os leads de ensaio, para o painel começar vazio:
   ```bash
   ./limpar_leads.sh
   ```
   Ele mostra o que vai apagar e pede confirmação. Imóveis e usuários não são tocados.

**Aquecer o modelo**

7. Mande **uma** mensagem qualquer para o bot ("oi") e espere a resposta. A primeira resposta depois de subir leva ~20 s, porque o modelo carrega na memória; as seguintes levam 7 a 15 s.
8. Rode o `./limpar_leads.sh` de novo, para essa mensagem não aparecer no painel.

**Ensaio**

9. Faça o cenário 1 inteiro uma vez antes de gravar, com outra conta do Telegram ou pelo chat web. Se ele chegar à visita marcada, pode gravar.
10. Mande **um áudio** de teste e confirme que a Bia entendeu e respondeu em áudio. Se não responder em áudio, confira o `TTS_ENABLED` e regrave sem a cena 3; ela é a única que depende disso.

---

## 3. Arrumação da tela

- **Esquerda:** Telegram (desktop ou espelhado do celular).
- **Direita:** painel em http://localhost:8000/dashboard, rolado até o funil Kanban.
- Deixe o site (porta 5500) e a aba de observabilidade abertos em outras abas, para trocar rápido.

Use sempre o **mesmo** chat do Telegram para cada cenário. Comece cada cenário num chat novo (outra conta ou outro número) para os leads não se misturarem no painel.

---

## 4. Roteiro, cena a cena

**Regra de ouro:** mande uma mensagem e **espere a resposta chegar** antes de mandar a próxima. Cada resposta leva de 7 a 22 s. Na edição, corte essas esperas.

### Cena 1 — Abertura (0:00–0:30)

**Tela:** painel vazio, com os indicadores no topo.

**Fale:**
> "Imobiliárias perdem lead por cinco motivos: demoram para responder, não fazem acompanhamento, atendem tudo à mão, não sabem qual lead está quente e sobrecarregam o corretor. A Bia resolve os cinco. Ela é uma SDR com IA que roda inteira nesta máquina — nenhum dado do cliente vai para uma API paga — e atende sobre o estoque real de uma imobiliária parceira."

### Cena 2 — Cenário 1: compra (0:30–2:30)

**Tela:** Telegram à esquerda, painel à direita.

Digite **exatamente** isto, uma mensagem por vez:

| # | Você digita | A Bia responde (testado) |
|---|---|---|
| 1 | `Estou procurando apartamento na zona sul.` | anota e pergunta a faixa de preço |
| 2 | `até 1,5 milhão` | pergunta quantos quartos |
| 3 | `3 quartos` | pergunta se tem pressa |
| 4 | `preciso pra esse mês` | **diz que não encontrou nada** com esses critérios e pergunta se quer ajustar preço ou região |
| 5 | `pode ser em Barueri também` | mostra o apartamento **AP0190-HORZ**, Jardim Tupanci, R$ 1.200.000, com foto |
| 6 | `gostei do 1, dá pra visitar sábado de manhã?` | pede nome e telefone |
| 7 | `Carlos Lima, 11 98888-7777` | confirma a visita com o corretor, para o sábado, com a data certa |

**Enquanto espera a resposta 1, fale:**
> "Essa é a frase do enunciado. A Bia identifica a intenção e começa a qualificar: faixa de preço, quartos, urgência — uma pergunta por vez."

**Na resposta 4, fale (é o momento mais importante do vídeo):**
> "Aqui está o que diferencia a Bia. Não existe apartamento de três quartos na zona sul no estoque. Um chatbot comum inventaria um. A Bia diz que não tem. Preço, endereço e código nunca são escritos pelo modelo — são montados em código a partir do banco."

**Na resposta 5:** aponte para o painel. O lead aparece com os dados preenchidos.

**Na resposta 7, fale:**
> "Visita marcada. A data foi resolvida no calendário real, não calculada pelo modelo. E olhem o funil: o lead já está em 'Visita marcada', e o corretor recebe o resumo pronto."

Abra o card do lead no painel e mostre o resumo e o score com os motivos.

### Cena 3 — Voz (2:30–3:10)

Num **chat novo**, mande um **áudio** falando:
> "Oi, quero alugar uma casa em Santana de Parnaíba."

**Fale:**
> "A Bia entende áudio, transcrito localmente, e responde em áudio para quem mandou áudio. Nenhum som sai desta máquina."

No painel, mostre a mensagem com o ícone de áudio.

> Esta cena depende do passo 10 do checklist. Se o áudio falhar no ensaio, pule a cena.

### Cena 4 — Cenário 3: follow-up (3:10, deixar rodando)

Ainda no chat da cena 3, **pare de responder**. Não mande mais nada nesse chat.

**Fale:**
> "Agora eu paro de responder, como um cliente real faria. Vou deixar isso rodando e voltar daqui a pouco."

Com `DEMO_MODE=true`, a primeira mensagem de follow-up chega em **cerca de 2 minutos**.

> **Não deixe esse chat parado mais de 10 minutos.** Com 10 minutos sem resposta, a Bia encerra o atendimento por inatividade, e a cena perde o sentido.

### Cena 5 — Cenário 2: investimento (3:20–4:40)

**Num chat novo**, digite:

| # | Você digita | A Bia responde (testado) |
|---|---|---|
| 1 | `Quero investir em imóveis para renda.` | pergunta se você já tem experiência como investidor |
| 2 | `quero renda mensal com aluguel` | pergunta o valor a investir |
| 3 | `uns 2 milhões` | pergunta a expectativa de retorno |
| 4 | `uns 7% ao ano` | encaminha para a equipe de investimentos |

**Fale:**
> "No investimento o roteiro muda: perfil, ticket, retorno esperado. E o desfecho não é visita, é um especialista — exatamente o que o enunciado pede. O encaminhamento vai com o resumo pronto: perfil, ticket e retorno."

No painel, mostre o lead na coluna "Com especialista".

### Cena 6 — O follow-up chega sozinho (por volta de 4:40)

Volte ao chat da cena 3. A mensagem de follow-up deve ter chegado.

**Fale:**
> "Enquanto eu falava de outra coisa, a Bia voltou sozinha ao cliente que parou de responder, lembrando o que ele já tinha dito. Ninguém pediu. Isso é o follow-up automático."

> Se ainda não chegou, siga para a cena 7 e volte a este chat no fim.

### Cena 7 — Observabilidade e segurança (4:50–5:40)

**Tela:** painel, aba de observabilidade.

**Fale:**
> "Cada chamada ao modelo é medida: latência, tokens por segundo, qual agente rodou. O painel também mostra quanto essa mesma conversa custaria numa API paga. Aqui, o custo real é zero."

Se tiver tempo, mostre o Kanban e **arraste** um lead entre colunas.

### Cena 8 — Site e fechamento (5:40–6:30)

**Tela:** http://localhost:5500.

**Fale:**
> "A Bia também fica embutida no site da imobiliária, com um widget. Os imóveis, as fotos e os preços são reais. O sistema roda sobre o estoque de uma imobiliária de verdade, com custo zero por lead e nenhum dado saindo da máquina. Isso é algo que poderia existir no mercado amanhã."

Clique em "Falar com a Bia" para mostrar o chat abrindo.

---

## 5. Plano B (se o cenário 1 sair diferente)

Se a resposta 4 do cenário 1 não aparecer como esperado, troque pelo aluguel de casa, testado de ponta a ponta:

| # | Você digita | A Bia responde (testado) |
|---|---|---|
| 1 | `Oi, quero alugar uma casa em Santana de Parnaíba` | pergunta a faixa de preço |
| 2 | `até 10 mil por mês` | pergunta quantos quartos |
| 3 | `3 quartos` | pergunta se tem pressa |
| 4 | `sem pressa, uns 2 meses` | mostra **3 casas reais**, de R$ 7.300 a R$ 9.500 por mês |
| 5 | `gostei do 2, pode ser quinta à tarde?` | pede nome e telefone |
| 6 | `Ana Souza, 11 97777-6666` | confirma a visita para quinta |

Neste caminho a Bia mostra imóveis logo, sem a cena de "não tenho". Perde o argumento do guardrail, mas não tem risco.

---

## 6. O que não fazer

| Não faça | Por quê |
|---|---|
| Mandar a próxima mensagem antes da resposta chegar | as duas mensagens se misturam no mesmo turno |
| Citar Campinas, Moema, Santo André, Sorocaba | não estão no catálogo; a Bia diz que não atende |
| Esperar ver lista de apartamentos na zona sul | não existe nenhum; a resposta certa é "não encontrei" |
| Deixar o chat do follow-up parado mais de 10 min | a Bia encerra o atendimento por inatividade |
| Rodar testes ou build durante a gravação | disputam a GPU e cada resposta passa de 30 s |
| Reaproveitar um chat de ensaio | o lead já tem dados, e a Bia pula perguntas |
| Arrastar um lead para "Visita marcada" sem visita | o painel recusa, de propósito (ver abaixo) |

O último item pode virar demonstração: arraste um lead em qualificação para "Visita marcada". O painel recusa, porque não existe visita registrada, e a Bia citaria um compromisso que não existe. É o mesmo princípio do "não tenho apartamento", aplicado ao painel.

---

## 7. Edição

- **Corte as esperas** entre mensagem e resposta. Deixe 1 segundo da Bia "digitando", para mostrar que é tempo real.
- Ponha uma **legenda** discreta nas mensagens digitadas; o texto do Telegram fica pequeno em vídeo.
- Na cena 2, resposta 4, deixe a frase "não encontrei nada" visível por 2 ou 3 segundos. É o argumento central do pitch.
- Se o follow-up demorar, junte a cena 6 no final, com um corte.
