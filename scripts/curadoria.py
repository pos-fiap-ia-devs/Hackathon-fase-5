"""Registro do que já foi feito em cada foto do acervo.

Importar, borrar a marca d'água e conferir se a imagem é de terceiro são
três passos caros (rede, imagem, OCR) e **nenhum é idempotente por conta
própria**: reimportar rebaixa o que foi excluído de propósito, borrar de
novo borra duas vezes. Este arquivo é o que os torna seguros de repetir.

    marca_removida          já passou por scripts/borrar_marca.py
    terceiros_verificadas   já passou por scripts/achar_terceiros.py
    excluidas               fora do projeto de propósito (imagem de
                            terceiro); o importador NÃO rebaixa
"""

import json
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent.parent / "app" / "db" / "curadoria_fotos.json"
CHAVES = ("marca_removida", "terceiros_verificadas", "excluidas")


def carregar() -> dict[str, set[str]]:
    if not ARQUIVO.exists():
        return {c: set() for c in CHAVES}
    bruto = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    return {c: set(bruto.get(c, [])) for c in CHAVES}


def salvar(dados: dict[str, set[str]]) -> None:
    ARQUIVO.write_text(
        json.dumps({c: sorted(dados[c]) for c in CHAVES}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
