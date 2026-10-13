# Instalação

Dois caminhos. Escolha pelo hardware da máquina:

| Máquina | Caminho | O que você vê |
|---|---|---|
| **Mac com Apple Silicon** (M1/M2/M3/M4) | [Completo](#caminho-completo-mac-apple-silicon) | Tudo: modelo local de verdade, áudio no Telegram |
| **Windows, Linux ou Mac Intel** | [Demonstração](#caminho-demonstração-qualquer-máquina) | Fluxo inteiro, sem GPU e sem baixar modelo |

O projeto roda com um modelo de 9 B local, por Metal. Em máquina sem Apple Silicon o Ollama até funciona, mas na velocidade da CPU — o modo demonstração existe exatamente para isso.

---

## Caminho demonstração (qualquer máquina)

Roda a qualificação, a busca no banco, o agendamento, o follow-up e o resumo. As respostas saem de um provedor de regras ([`app/core/llm_demo.py`](app/core/llm_demo.py)) em vez do modelo — bem piores de redação, e é esse o ponto: demonstra **fluxo**, não qualidade de texto. O imóvel, o preço, o corretor e a data continuam vindo do Postgres.

### 1. Pré-requisitos

- **Docker Desktop** rodando
- **Python 3.12** (o projeto exige `>=3.12,<3.13`)
- [**uv**](https://github.com/astral-sh/uv)

```bash
python3 --version      # precisa ser 3.12.x
docker info            # precisa responder
```

### 2. Instalar

```bash
git clone git@github.com:pos-fiap-ia-devs/Hackathon-fase-5.git
cd Hackathon-fase-5

uv venv --python 3.12
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e .

cp .env.example .env               # Windows: copy .env.example .env
```

### 3. Banco

```bash
docker compose up -d
python -m scripts.preparar_banco
```

O `preparar_banco` aplica o schema e semeia 200 imóveis. É idempotente: rodar de novo não apaga nada.

### 4. Usar

```bash
LLM_DEMO=true python -m app.channels.cli          # conversa no terminal
```

Ou com as telas:

```bash
LLM_DEMO=true uvicorn app.web.api:app --host 0.0.0.0 --port 8000
```

| Tela | Endereço |
|---|---|
| Chat do lead | http://localhost:8000/chat |
| Dashboard do corretor | http://localhost:8000/dashboard |

No **primeiro acesso ao dashboard** o app leva para `/registro`: essa primeira conta vira administrador e o cadastro aberto fecha em seguida. Os demais usuários entram por convite, em `/dashboard/usuarios`.

---

## Caminho completo (Mac Apple Silicon)

### 1. Pré-requisitos

- **Docker Desktop** rodando
- **Python 3.12** e [**uv**](https://github.com/astral-sh/uv)
- [**Ollama**](https://ollama.com) instalado **nativamente** — nunca em container: dentro do Docker não há Metal e a inferência cai para a velocidade da CPU

### 2. Subir tudo

```bash
git clone git@github.com:pos-fiap-ia-devs/Hackathon-fase-5.git
cd Hackathon-fase-5
./run.sh
```

O `run.sh` faz tudo em sequência: sobe o Ollama, baixa `qwen3.5:9b` e `nomic-embed-text` (~6 GB na primeira vez), sobe o Postgres, cria a venv, instala as dependências, cria o `.env` a partir do exemplo, aplica schema e seed, e sobe a aplicação em `http://localhost:8000`.

Parar tudo: `./stop.sh`

### 3. Telegram (opcional)

O `.env` **não vem no git** — o `run.sh` o cria a partir do `.env.example`, com o token vazio. Para ligar o bot:

1. Fale com o [@BotFather](https://t.me/BotFather) no Telegram, `/newbot`, copie o token
2. Cole em `TELEGRAM_BOT_TOKEN` no `.env`
3. `./stop.sh && ./run.sh`

> ⚠️ **Um bot, uma instância.** O Telegram só aceita um `getUpdates` por token. Rodar o projeto em duas máquinas com o mesmo token faz as duas brigarem (`Conflict: terminated by other getUpdates request`). Escolha uma.

### 4. Áudio no Telegram (opcional)

```bash
uv pip install -e ".[stt]"
```

E `STT_ENABLED=true` no `.env`. Só funciona em Apple Silicon — `mlx-whisper` depende do MLX, que não instala em Linux nem em Mac Intel.

---

## Voz da Bia fora do macOS

O cliente **sempre ouve** — quem gera o áudio é a máquina que roda a aplicação, e quem escuta é o Telegram, em qualquer aparelho. O que muda é a voz:

| App rodando em | Backend | Precisa instalar? |
|---|---|---|
| macOS | `say` (voz pt-BR do sistema) | não |
| Container (`./rebuild.sh --docker`) | `piper` | não, o Dockerfile faz |
| Linux ou Windows nativo | `piper` | sim |

No terceiro caso:

```bash
uv pip install -e ".[tts]"
```

E baixe um modelo pt-BR de [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR), apontando `TTS_MODELO_PIPER` para o arquivo `.onnx` no `.env`.

Sem nenhum dos dois backends, a Bia responde **só em texto** — nada quebra, e o log avisa uma vez. Áudio é complemento, nunca requisito.

## Testes

```bash
uv pip install -e ".[dev]"
pytest
```

Boa parte da suíte é de integração real: fala com o Postgres e com o Ollama, sem mock. Nada disso **falha** por falta de infraestrutura — os testes que dependem de serviço pulam com a razão e o comando na mensagem:

```
sem Docker e sem Ollama   →  226 passed, 89 skipped em ~50s
```

Com tudo no ar a suíte leva ~11 minutos: os testes anti-laço fazem 30 conversas reais
com o modelo. É o preço de testar o agente de verdade em vez de contra um mock.

---

## Portas usadas

| Porta | Serviço | Conflita com |
|---|---|---|
| `8000` | aplicação | qualquer outra API local sua |
| `5433` | Postgres (container) | **nada** — o padrão do Postgres é 5432, escolhemos 5433 de propósito |
| `11434` | Ollama (no host) | outro Ollama |

Se a `8000` estiver ocupada, mude `APP_PORT` no `.env`.

> Se você já tem outro Postgres em Docker na 5432, **não há conflito**: o nosso publica na 5433. O `5432` que aparece no `docker-compose.yml` é a porta *dentro* do container, que vive na rede isolada do projeto.

---

## Problemas comuns

### As fotos dos imóveis não aparecem

Quase sempre é banco semeado antes da mudança que trouxe as fotos para o repositório. Confira:

```bash
docker compose exec -T db psql -U sdr -d sdr -tAc 'SELECT fotos[1] FROM imoveis LIMIT 1'
```

- `/static/imoveis/...` → está certo, o problema é outro
- `https://loremflickr...` → seed antigo. O `preparar_banco` só semeia quando a tabela está **vazia**, então force:

```bash
python -m scripts.preparar_banco --forcar
```

> O `--forcar` executa `TRUNCATE imoveis ... CASCADE`, que **apaga as visitas agendadas** junto (há FK de `visitas` para `imoveis`). Sem ele, o carregador faz upsert por código e preserva visitas e ids — é o caminho seguro a cada subida.

As fotos (`app/web/static/imoveis/orion/`) e o catálogo (`app/db/catalogo_orion.json`) vêm versionados no repositório: subir o banco não depende de internet. `python -m scripts.importar_orion` só é preciso para atualizar o catálogo a partir da fonte.

### `docker info` não responde

Docker Desktop não está rodando. Abra o aplicativo e espere o ícone ficar estável.

### Porta 8000 ocupada

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Se for um container antigo deste projeto: `docker compose --profile app down`. O `--profile app` é necessário — sem ele o `down` derruba só o banco e deixa o app de pé.

### O dashboard pede login e não tenho conta

Com o banco sem nenhum usuário, `/dashboard` redireciona sozinho para `/registro` e a primeira conta vira administrador. Se já existe usuário e você perdeu a senha, apague-o e refaça o cadastro:

```bash
docker compose exec -T db psql -U sdr -d sdr -c 'DELETE FROM usuarios'
```

### Limpar os leads de teste

```bash
./limpar_leads.sh --teste     # só o lixo de teste e captura
./limpar_leads.sh             # tudo (pede confirmação)
```

Mostra o que vai sumir antes de perguntar. Nunca toca em `imoveis` nem em `usuarios`.

---

## O que o git não traz

| Arquivo | Por quê | Como obter |
|---|---|---|
| `.env` | tem o token do bot | `cp .env.example .env` (o `run.sh` faz sozinho) |
| `PITCH.md`, `IA.md`, `PLANO.md` | estão no `.gitignore` | peça ao autor |
| modelos do Ollama | ~6 GB | `ollama pull qwen3.5:9b` (o `run.sh` faz sozinho) |

As fotos dos imóveis **vêm** no git — são 40 arquivos, 2,7 MB.

---

Documentação: [README.md](README.md) · [ARQUITETURA.md](ARQUITETURA.md) · [docs/VALIDACAO_REQUISITOS.md](docs/VALIDACAO_REQUISITOS.md) · [docs/SOLID.md](docs/SOLID.md)
