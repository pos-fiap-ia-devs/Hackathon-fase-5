"""Senha e token do login do dashboard (core/auth.py).

Sem banco e sem LLM de proposito -- mesma filosofia de test_security.py e
test_router.py: o que da pra testar como funcao pura, testa-se como funcao
pura. O fluxo de rota (bootstrap, convite, guard) depende do Postgres e
mora em test_auth_rotas.py.
"""

import pytest

from app.core.auth import (
    SENHA_MIN_CARACTERES,
    SenhaFraca,
    gerar_token,
    hash_senha,
    hash_token,
    validar_senha,
    verificar_senha,
)

SENHA_BOA = "cafe-com-pao-2026"


# ---------------------------------------------------------------- senha

def test_senha_confere_com_o_proprio_hash():
    confere, _ = verificar_senha(hash_senha(SENHA_BOA), SENHA_BOA)
    assert confere


def test_senha_errada_nao_confere():
    confere, _ = verificar_senha(hash_senha(SENHA_BOA), "outra-senha-qualquer")
    assert not confere


def test_hash_nunca_contem_a_senha_em_texto():
    assert SENHA_BOA not in hash_senha(SENHA_BOA)


def test_hashes_da_mesma_senha_sao_diferentes():
    """Sal aleatorio por hash: duas contas com a mesma senha nao podem ter
    a mesma linha no banco, senao um dump entrega quem repetiu senha."""
    assert hash_senha(SENHA_BOA) != hash_senha(SENHA_BOA)


def test_hash_e_argon2id():
    """Nao basta ser argon2: `argon2i` e `argon2d` tambem sao, e nenhum dos
    dois e o recomendado pra senha."""
    assert hash_senha(SENHA_BOA).startswith("$argon2id$")


def test_senha_curta_e_recusada_antes_de_virar_hash():
    with pytest.raises(SenhaFraca):
        hash_senha("a" * (SENHA_MIN_CARACTERES - 1))


def test_senha_no_limite_passa():
    validar_senha("a" * SENHA_MIN_CARACTERES)


def test_hash_corrompido_nao_estoura():
    """Linha adulterada no banco tem que virar 'senha errada', nao 500."""
    confere, _ = verificar_senha("nao-e-um-hash-argon2", SENHA_BOA)
    assert not confere


def test_login_bem_sucedido_nao_pede_rehash_sem_motivo():
    """O segundo item so vem preenchido quando os parametros mudaram."""
    _, hash_novo = verificar_senha(hash_senha(SENHA_BOA), SENHA_BOA)
    assert hash_novo is None


# ---------------------------------------------------------------- token

def test_token_nunca_se_repete():
    assert len({gerar_token() for _ in range(100)}) == 100


def test_token_tem_entropia_de_sobra():
    # token_urlsafe(32) -> 32 bytes em base64url, ~43 caracteres.
    assert len(gerar_token()) >= 40


def test_hash_do_token_e_estavel_e_nao_reversivel():
    token = gerar_token()
    assert hash_token(token) == hash_token(token)
    assert token not in hash_token(token)


def test_tokens_diferentes_tem_hashes_diferentes():
    assert hash_token(gerar_token()) != hash_token(gerar_token())
