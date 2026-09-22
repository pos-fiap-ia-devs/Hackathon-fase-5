"""Seguranca (secao 7 do PLANO.md). PII nunca em texto puro no banco.

O telefone do cliente e coletado pra confirmar visita (corretor precisa
saber pra quem ligar), mas o schema so tem `telefone_mascarado` de
proposito -- o numero cru nunca e persistido, so os digitos suficientes
pra reconhecer, mascarados no momento da escrita.
"""

import re


def mascarar_telefone(numero: str) -> str:
    """Mantem DDD e os ultimos 4 digitos, mascara o resto.
    "62 99999-8888" -> "62*****8888". Numero curto/invalido -> so "****"."""
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) < 8:
        return "****"
    return f"{digitos[:2]}{'*' * (len(digitos) - 6)}{digitos[-4:]}"
