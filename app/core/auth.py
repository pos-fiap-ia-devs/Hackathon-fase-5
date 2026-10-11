"""Senha, token e sessao do dashboard.

Separado de `core/security.py` de proposito: aquele trata do dado do LEAD
(mascarar PII antes de persistir); este trata de quem opera o painel. Os
dois sao "seguranca", mas nao se misturam -- um protege o cliente, o outro
protege o acesso ao cliente.

Duas primitivas diferentes, pelo motivo certo:

- SENHA -> argon2id (`PasswordHasher`). Entropia baixa, escolhida por
  humano, entao o hash precisa ser caro de propósito -- memory-hard, pra
  tirar a vantagem de quem ataca com GPU. Recomendacao atual do OWASP.
- TOKEN de sessao/convite -> SHA-256. Sao 256 bits de `secrets`, nao tem
  o que adivinhar; passar argon2 neles so pagaria o custo em toda
  requisicao sem ganhar nada. O hash aqui serve pra que um dump do banco
  nao entregue sessao valida nem convite usavel.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Parametros default do argon2-cffi: argon2id, 3 passadas, 64 MiB, 4 lanes
# -- ja alinhados ao OWASP. Fixar numero na mao aqui so envelheceria mal.
_hasher = PasswordHasher()

# Curto demais nao e politica de senha, e teatro. 10 e o piso do NIST
# 800-63B pra senha escolhida por humano sem outras regras de composicao
# (que o proprio NIST desaconselha -- nada de "1 maiuscula e 1 simbolo").
SENHA_MIN_CARACTERES = 10


class SenhaFraca(ValueError):
    """Senha rejeitada antes de virar hash."""


def validar_senha(senha: str) -> None:
    """Levanta `SenhaFraca` com a mensagem que vai pro usuario na tela."""
    if len(senha) < SENHA_MIN_CARACTERES:
        raise SenhaFraca(f"A senha precisa ter pelo menos {SENHA_MIN_CARACTERES} caracteres.")


def hash_senha(senha: str) -> str:
    """Hash argon2id no formato PHC -- a string ja carrega algoritmo,
    parametros e sal, entao nao existe coluna de sal no schema."""
    validar_senha(senha)
    return _hasher.hash(senha)


def verificar_senha(senha_hash: str, senha: str) -> tuple[bool, str | None]:
    """(senha confere, hash novo se precisar regravar).

    O segundo item existe por causa do `check_needs_rehash`: quando os
    parametros default do argon2-cffi sobem numa versao futura, o hash
    antigo continua valido mas fica abaixo do recomendado. Devolver o hash
    novo aqui deixa o chamador regravar em silencio, no unico momento em
    que a senha em texto existe na memoria -- o login."""
    try:
        _hasher.verify(senha_hash, senha)
    except (VerifyMismatchError, InvalidHashError):
        return False, None
    novo = _hasher.hash(senha) if _hasher.check_needs_rehash(senha_hash) else None
    return True, novo


def gerar_token() -> str:
    """Token cru de sessao/convite -- 256 bits de `secrets`. Vai pro cookie
    (ou pro link do convite) e NUNCA pro banco; la grava-se o hash."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Olho da fechadura do token: o que o banco guarda."""
    return hashlib.sha256(token.encode()).hexdigest()
