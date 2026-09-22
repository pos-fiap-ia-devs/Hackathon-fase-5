"""Router e 100% codigo, sem LLM -- testavel isolado e instantaneo, ao
contrario dos agentes que chamam o Ollama (test_qualifier.py)."""

from app.agents.router import (
    decidir_proximo,
    eh_aceite_curto,
    pediu_lista_de_regioes,
    pediu_para_encerrar,
    pediu_para_ver_opcoes,
)


def test_sem_intencao_pergunta_intencao():
    assert decidir_proximo({}) == "perguntar_intencao"
    assert decidir_proximo({"intencao": "indefinido"}) == "perguntar_intencao"


def test_compra_segue_ordem_do_pdf():
    """PDF: faixa de preco -> quartos -> regiao -> urgencia -> reuniao."""
    base = {"intencao": "compra"}
    assert decidir_proximo(base) == "perguntar_preco"
    assert decidir_proximo({**base, "faixa_preco_max": 500_000}) == "perguntar_quartos"
    assert decidir_proximo({**base, "faixa_preco_max": 500_000, "quartos": 2}) == "perguntar_regiao"
    assert decidir_proximo(
        {**base, "faixa_preco_max": 500_000, "quartos": 2, "regiao": "zona sul"}
    ) == "perguntar_urgencia"
    assert decidir_proximo(
        {**base, "faixa_preco_max": 500_000, "quartos": 2, "regiao": "zona sul", "urgencia": "alta"}
    ) == "buscar_imoveis"


def test_aluguel_segue_mesma_ordem_que_compra():
    assert decidir_proximo({"intencao": "aluguel"}) == "perguntar_preco"


def test_investimento_segue_ordem_do_pdf():
    """PDF: perfil investidor -> ticket -> expectativa de retorno -> especialista."""
    base = {"intencao": "investimento"}
    assert decidir_proximo(base) == "perguntar_perfil_investidor"
    assert decidir_proximo({**base, "perfil_investidor": "conservador"}) == "perguntar_ticket"
    assert decidir_proximo(
        {**base, "perfil_investidor": "conservador", "ticket": 300_000}
    ) == "perguntar_expectativa_retorno"
    assert decidir_proximo(
        {**base, "perfil_investidor": "conservador", "ticket": 300_000, "expectativa_retorno": "6% ao ano"}
    ) == "encaminhar_especialista"


def test_detecta_pedido_de_ver_opcoes():
    """Frase real que motivou o atalho + variacoes comuns."""
    assert pediu_para_ver_opcoes("Quais tem hoje para mim com está configuração de 2 quarto")
    assert pediu_para_ver_opcoes("me mostra o que tem")
    assert pediu_para_ver_opcoes("Quero ver as opções")
    assert pediu_para_ver_opcoes("tem alguma coisa nessa faixa?")
    assert not pediu_para_ver_opcoes("2 quartos")
    assert not pediu_para_ver_opcoes("sem pressa")
    assert not pediu_para_ver_opcoes(None)


def test_pedido_de_ver_opcoes_antecipa_busca():
    """Com preco e quartos ja coletados, pedido explicito do cliente vale
    mais que terminar o questionario -- regiao/urgencia ficam pra depois."""
    com_preco_e_quartos = {"intencao": "aluguel", "faixa_preco_max": 1500, "quartos": 2}
    assert decidir_proximo(com_preco_e_quartos) == "perguntar_regiao"
    assert decidir_proximo(com_preco_e_quartos, "quais tem pra mim?") == "buscar_imoveis"


def test_pedido_de_ver_opcoes_nao_pula_preco_nem_quartos():
    """Sem os dois filtros basicos a lista sairia generica demais -- e o
    PDF pede os dois explicitamente no Cenario 1."""
    assert decidir_proximo({"intencao": "compra"}, "me mostra o que tem") == "perguntar_preco"
    assert decidir_proximo(
        {"intencao": "compra", "faixa_preco_max": 500_000}, "me mostra o que tem"
    ) == "perguntar_quartos"


def test_investimento_nao_tem_atalho_de_portfolio():
    """Cenario 2 do PDF termina em especialista, nao em lista de imoveis."""
    base = {"intencao": "investimento", "perfil_investidor": "renda", "ticket": 300_000}
    assert decidir_proximo(base, "me mostra o que tem") == "perguntar_expectativa_retorno"


def test_desfechos_sao_diferentes_entre_compra_e_investimento():
    """Secao 5.9 -- os dois cenarios NAO terminam na mesma acao."""
    compra_completo = {
        "intencao": "compra", "faixa_preco_max": 1, "quartos": 1, "regiao": "x", "urgencia": "alta",
    }
    investimento_completo = {
        "intencao": "investimento", "perfil_investidor": "x", "ticket": 1, "expectativa_retorno": "x",
    }
    assert decidir_proximo(compra_completo) == "buscar_imoveis"
    assert decidir_proximo(investimento_completo) == "encaminhar_especialista"
    assert decidir_proximo(compra_completo) != decidir_proximo(investimento_completo)


def test_detecta_pedido_de_lista_de_regioes():
    """Frase real que motivou o no `listar_regioes`."""
    assert pediu_lista_de_regioes("Quais bairros tem com 2 quartos")
    assert pediu_lista_de_regioes("quais regiões vocês atendem?")
    assert pediu_lista_de_regioes("onde tem disponível?")
    assert not pediu_lista_de_regioes("Setor Bueno")
    assert not pediu_lista_de_regioes(None)


def test_pergunta_de_regiao_lista_em_vez_de_reperguntar():
    """Antes o fluxo so sabia REPERGUNTAR a regiao, e o modelo respondia
    "tem preferencia por algum desses bairros?" sem listar nenhum."""
    slots = {"intencao": "aluguel", "faixa_preco_max": 1400, "quartos": 2}
    assert decidir_proximo(slots) == "perguntar_regiao"
    assert decidir_proximo(slots, "quais bairros tem?") == "listar_regioes"


def test_investimento_nao_lista_regioes():
    base = {"intencao": "investimento", "perfil_investidor": "renda"}
    assert decidir_proximo(base, "quais bairros tem?") == "perguntar_ticket"


def test_detecta_pergunta_por_cidades():
    """Com 10 cidades no seed de SP, "quais cidades" virou a forma mais
    natural de perguntar onde existe imovel."""
    assert pediu_lista_de_regioes("Quais cidades voces tem?")
    assert pediu_lista_de_regioes("em quais cidades voces atuam")
    assert not pediu_lista_de_regioes("Campinas")


def test_detecta_despedida():
    """Achado no Telegram real: "Obrigado" depois da visita marcada fazia o
    agente repetir a confirmacao inteira em vez de se despedir."""
    for msg in [
        "Obrigado", "Obrigada!", "Obrigado, era só isso", "Valeu, muito obrigado!",
        "Agradeço a ajuda, pode encerrar", "Tchau!", "Até logo", "Até a próxima",
        "Bom final de semana!", "Tenha um ótimo dia", "Isso resolve, muito obrigado",
        "Ok, entendi tudo, obrigado", "Show, valeu!", "Falou!", "Fechou, obrigado",
    ]:
        assert pediu_para_encerrar(msg), msg


def test_agradecimento_no_meio_da_frase_nao_encerra():
    """O limite de tamanho existe pra isso: sem ele, "obrigado, mas..."
    fecharia o atendimento bem quando o cliente quer continuar."""
    for msg in [
        "obrigado, mas prefiro 3 quartos na verdade",
        "obrigado por perguntar, mas queria ver outra regiao antes",
        "2 quartos", "Campinas", "Quais bairros tem?", "ta caro demais", None,
    ]:
        assert not pediu_para_encerrar(msg), msg


def test_aceite_curto_nao_e_despedida():
    """"Ok" so acusa recebimento -- nao encerra, mas tambem nao pode fazer
    o agente repetir a confirmacao inteira da visita (achado no print)."""
    for msg in ["Ok", "certo", "beleza", "entendi", "Perfeito", "tudo bem"]:
        assert eh_aceite_curto(msg), msg
        assert not pediu_para_encerrar(msg), msg


def test_pergunta_nao_e_aceite_curto():
    for msg in ["Qual o endereço?", "que dia é terça?", "2 quartos", "ok obrigado", None]:
        assert not eh_aceite_curto(msg), msg
