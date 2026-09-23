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
