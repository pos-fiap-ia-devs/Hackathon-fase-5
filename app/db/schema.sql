-- Agente SDR Imobiliario - schema
-- Portugues sem acento nos identificadores para evitar surpresa de encoding no psql.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------- imoveis

CREATE TABLE imoveis (
    id              SERIAL PRIMARY KEY,
    titulo          TEXT        NOT NULL,
    tipo            TEXT        NOT NULL,   -- apartamento, casa, studio, cobertura, sala
    finalidade      TEXT        NOT NULL,   -- venda | aluguel
    bairro          TEXT        NOT NULL,
    zona            TEXT        NOT NULL,   -- como o lead fala da regiao: na capital e
                                                -- zona sul/norte/leste/oeste/centro; fora dela
                                                -- e 'grande sao paulo' / 'interior' (app/db/seed.py)
    cidade          TEXT        NOT NULL DEFAULT 'Sao Paulo',
    preco           NUMERIC(12,2) NOT NULL, -- venda: valor total | aluguel: mensal
    quartos         INT         NOT NULL,
    banheiros       INT         NOT NULL,
    vagas           INT         NOT NULL,
    area            INT         NOT NULL,
    condominio      NUMERIC(10,2),
    -- preenchido so nos imoveis marcados para investimento; alimenta o cenario 2
    rentabilidade_estimada NUMERIC(4,2),
    descricao       TEXT        NOT NULL,
    -- 3 fotos por imovel (pedido do usuario), URLs escolhidas no seed de
    -- acordo com o TIPO do imovel (app/db/seed.py::_fotos_do_imovel).
    -- Array de texto e nao tabela propria: foto aqui e dado de exibicao,
    -- sem atributo nenhum alem da ordem -- tabela filha so adicionaria
    -- join em toda busca sem nada em troca.
    fotos           TEXT[]      NOT NULL DEFAULT '{}',
    embedding       vector(768),
    busca           tsvector GENERATED ALWAYS AS (
                        to_tsvector('portuguese',
                            coalesce(titulo,'') || ' ' ||
                            coalesce(bairro,'') || ' ' ||
                            coalesce(zona,'')   || ' ' ||
                            coalesce(descricao,''))
                    ) STORED,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sem indice vetorial de proposito: com ~200 linhas o scan sequencial e mais rapido
-- que ivfflat, e ivfflat ainda exigiria `lists` compativel com a contagem de linhas.
-- HNSW entra a partir de ~10 mil imoveis, sem mudar a query.
CREATE INDEX imoveis_busca_idx    ON imoveis USING gin (busca);
CREATE INDEX imoveis_filtro_idx   ON imoveis (finalidade, zona, preco, quartos);

-- ---------------------------------------------------------------- leads

CREATE TABLE leads (
    id                  SERIAL PRIMARY KEY,
    -- prefixado por canal: "tg:123456", "web:<uuid>", "cli:local"
    chat_id             TEXT        NOT NULL UNIQUE,
    canal               TEXT        NOT NULL,   -- telegram | web | cli
    nome                TEXT,
    telefone_mascarado  TEXT,                   -- nunca o telefone cru (secao 7)
    status              TEXT        NOT NULL DEFAULT 'novo',
                                                -- novo | qualificando | qualificado
                                                -- | aguardando_escolha | visita_agendada
                                                -- | encaminhado | perdido | encerrado
                                                -- (encerrado = fechado por inatividade;
                                                -- cliente respondeu de novo = retoma de onde
                                                -- parou, sem apagar slots/contato --
                                                -- repo.retomar_atendimento; se havia visita
                                                -- marcada e nao passou, volta pra
                                                -- 'visita_agendada')
    score               INT         NOT NULL DEFAULT 0,
    -- ids de imoveis.id mostrados na ultima busca (Etapa 5) -- permite o
    -- lead responder "quero o 2" sem repetir o titulo do imovel
    imoveis_apresentados INT[],
    -- estado parcial da escolha de visita entre turnos (status=aguardando_escolha).
    -- Sem isso, "quero o 1" num turno e "sabado de manha" no seguinte se perdem
    -- entre si -- mesmo bug de memoria de conversa que slots ja resolve pra
    -- qualificacao, so que pro sub-fluxo de agendamento.
    visita_imovel_escolhido INT,
    visita_horario_texto    TEXT,
    visita_nome_texto       TEXT,
    visita_telefone_texto   TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultima_interacao_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX leads_score_idx   ON leads (score DESC, ultima_interacao_em DESC);
CREATE INDEX leads_pendente_idx ON leads (ultima_interacao_em) WHERE status IN ('novo','qualificando');

-- ---------------------------------------------------------------- slots

CREATE TABLE slots (
    lead_id             INT PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    intencao            TEXT,   -- compra | aluguel | investimento | indefinido
    faixa_preco_min     BIGINT,
    faixa_preco_max     BIGINT,
    quartos             INT,
    regiao              TEXT,
    urgencia            TEXT,   -- alta | media | baixa
    perfil_investidor   TEXT,
    ticket              BIGINT,
    expectativa_retorno TEXT,
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- conversa

CREATE TABLE mensagens (
    id         SERIAL PRIMARY KEY,
    lead_id    INT  NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    papel      TEXT NOT NULL,                  -- user | assistant
    conteudo   TEXT NOT NULL,
    origem     TEXT NOT NULL DEFAULT 'texto',  -- texto | audio | followup
    -- galeria enviada JUNTO com esta mensagem: [{imovel_id, legenda, fotos[]}].
    -- Fica gravado na mensagem, nao so montado em memoria no turno, senao
    -- recarregar o chat web (ou abrir o lead no dashboard) mostraria a
    -- apresentacao de imoveis sem as fotos que o cliente recebeu.
    anexos     JSONB NOT NULL DEFAULT '[]',
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX mensagens_lead_idx ON mensagens (lead_id, criado_em);

CREATE TABLE memoria (
    lead_id          INT PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    resumo           TEXT NOT NULL DEFAULT '',
    turnos_resumidos INT  NOT NULL DEFAULT 0,
    atualizado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- desfechos
-- Cenario 1 termina em visita. Cenario 2 termina em encaminhamento. Sao diferentes.

CREATE TABLE visitas (
    id         SERIAL PRIMARY KEY,
    lead_id    INT  NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    imovel_id  INT           REFERENCES imoveis(id),
    corretor   TEXT NOT NULL,
    data_hora  TIMESTAMPTZ NOT NULL,
    -- o que o cliente disse em texto livre (ex: "sabado de tarde") -- sem
    -- calendario real integrado no POC, data_hora e um horario util
    -- deterministico e horario_solicitado preserva a preferencia dita
    horario_solicitado TEXT,
    status     TEXT NOT NULL DEFAULT 'agendada',
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE encaminhamentos (
    id           SERIAL PRIMARY KEY,
    lead_id      INT  NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    especialista TEXT NOT NULL,
    motivo       TEXT NOT NULL,
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE resumos (
    lead_id       INT PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    texto         TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- observabilidade

CREATE TABLE agent_traces (
    id                SERIAL PRIMARY KEY,
    lead_id           INT REFERENCES leads(id) ON DELETE CASCADE,
    turno             INT,
    agente            TEXT NOT NULL,   -- router | qualifier | search | scheduler | summarizer | followup
    modelo            TEXT NOT NULL,
    prompt_tokens     INT,
    completion_tokens INT,
    latencia_ms       INT  NOT NULL,
    tokens_por_s      NUMERIC(8,2),
    erro              TEXT,
    criado_em         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX traces_lead_idx  ON agent_traces (lead_id, criado_em DESC);
CREATE INDEX traces_agente_idx ON agent_traces (agente, criado_em DESC);

-- ---------------------------------------------------------------- follow-up
-- Ja e uma tabela de fila: vira fila distribuida com SELECT ... FOR UPDATE SKIP LOCKED,
-- sem trocar de tecnologia. E o argumento 4 de escalabilidade.

CREATE TABLE follow_up_jobs (
    id           SERIAL PRIMARY KEY,
    lead_id      INT  NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tentativa    INT  NOT NULL DEFAULT 1,
    executar_em  TIMESTAMPTZ NOT NULL,
    executado_em TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'pendente',  -- pendente | enviado | cancelado
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX followup_due_idx ON follow_up_jobs (executar_em) WHERE status = 'pendente';

-- ---------------------------------------------------------------- colunas novas em banco ja criado
-- `run.sh` aplica este arquivo inteiro a cada subida e engole os erros
-- (`|| true`): num banco que ja existe, todo CREATE TABLE acima falha por
-- duplicidade e e ignorado -- o que significa que coluna nova adicionada la
-- em cima NUNCA chegaria a um banco existente. Estes ALTERs idempotentes
-- sao o caminho pelo qual ela chega, sem precisar dropar o volume.
ALTER TABLE imoveis   ADD COLUMN IF NOT EXISTS fotos  TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS anexos JSONB  NOT NULL DEFAULT '[]';
-- Tipo da tentativa de prompt injection (core/injecao.py), NULL = limpa.
-- TEXT e nao BOOLEAN porque o painel mostra a quebra por tipo -- saber
-- QUE houve tentativa vale menos que saber o que ela tentou fazer.
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS suspeita_injecao TEXT;
-- Codigo publico do imovel (MOE-0001). Identificador ESTAVEL pro cliente
-- citar: o numero do card (1, 2, 3) e posicional e muda a cada busca --
-- "quero o 2" amanha, depois de outra busca, aponta pra outro imovel. O
-- codigo nao muda, aparece na foto, no card e na confirmacao da visita.
ALTER TABLE imoveis ADD COLUMN IF NOT EXISTS codigo TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS imoveis_codigo_idx ON imoveis (codigo);

-- Quantas vezes seguidas o roteador decidiu a MESMA coisa. `decidir_proximo`
-- e funcao pura dos slots: se o cliente nao responde o que foi perguntado,
-- os slots nao mudam, a acao nao muda, e o agente repete a mesma pergunta
-- pra sempre. Medido com uma sonda: 6 de 6 cenarios de cliente pouco
-- cooperativo travavam. Contar aqui e o que permite ESCALAR em vez de
-- insistir (core/graph.py::_no_decidir_proximo).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ultima_acao TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS acao_repeticoes INT NOT NULL DEFAULT 0;
-- Campos que o cliente NAO quis (ou nao soube) informar depois de 3
-- tentativas. Sem isso, escalar so trocava um laco por outro: a pergunta
-- parava de ser feita, mas o campo continuava faltando, entao a MESMA
-- escalada disparava em todo turno seguinte. Dispensar e o que faz o
-- roteiro seguir em frente de verdade (agents/router.py::decidir_proximo).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS campos_dispensados TEXT[] NOT NULL DEFAULT '{}';

-- ---------------------------------------------------------------- login do dashboard
-- `IF NOT EXISTS` (e nao o CREATE TABLE puro la de cima) porque estas tabelas
-- nasceram depois: num banco que ja existe, elas PRECISAM ser criadas, e o
-- `|| true` do run.sh engoliria o erro de um CREATE TABLE sem a clausula.
--
-- O dashboard expoe telefone e conversa inteira de todo lead. Antes disso
-- aqui, qualquer um em :8000/dashboard lia tudo.
CREATE TABLE IF NOT EXISTS usuarios (
    id         SERIAL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    nome       TEXT NOT NULL,
    -- Hash argon2id codificado no formato PHC: carrega algoritmo, parametros
    -- e sal dentro da propria string. Por isso nao ha coluna de sal.
    senha_hash TEXT NOT NULL,
    papel      TEXT NOT NULL DEFAULT 'corretor',  -- admin | corretor
    ativo      BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sessao server-side em vez de JWT: desativar um usuario precisa cortar o
-- acesso NA HORA, e token assinado so expira sozinho. Aqui basta um DELETE.
CREATE TABLE IF NOT EXISTS sessoes (
    -- SHA-256 do token, nunca o token cru: quem ler o banco nao consegue se
    -- passar por ninguem. Hash rapido e o certo aqui (token e aleatorio de
    -- 256 bits, nao senha) -- argon2 so entra onde ha entropia baixa.
    token_hash TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira_em  TIMESTAMPTZ NOT NULL,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sessoes_expira_idx ON sessoes (expira_em);

-- Convite por link, sem SMTP: o admin gera, copia e entrega o link por fora.
-- Uso unico (`usado_em`) e com prazo (`expira_em`).
CREATE TABLE IF NOT EXISTS convites (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    papel      TEXT NOT NULL DEFAULT 'corretor',
    criado_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    expira_em  TIMESTAMPTZ NOT NULL,
    usado_em   TIMESTAMPTZ,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
