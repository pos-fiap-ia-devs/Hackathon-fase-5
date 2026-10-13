"""Seed dos imóveis: carrega o catálogo REAL.

Antes, este arquivo GERAVA 200 imóveis sintéticos -- bairros reais, mas
preço, área, descrição e foto inventados. Decidiu-se usar só o catálogo
real, da imobiliária parceira: texto, valores e fotos dela, sem nada de
banco de imagem.

O catálogo é produzido por `scripts/importar_orion.py` (rede, lento) e
fica versionado em `app/db/catalogo_orion.json` + `app/web/static/imoveis/
orion/`, então semear NÃO depende de internet. Este módulo só o carrega;
a lógica mora em `scripts/carregar_catalogo.py`.

Rodar: python -m app.db.seed
"""

from scripts.carregar_catalogo import carregar


def run(*, truncar: bool = False) -> None:
    """`truncar=True` apaga `imoveis` antes -- e o CASCADE leva as visitas.
    O padrão é upsert por código, que preserva visitas e ids."""
    c = carregar(truncar=truncar)
    print(
        f"seed concluido: {c['novos']} novos, {c['atualizados']} atualizados, "
        f"{c['removidos']} legados removidos, {c['sem_foto']} sem foto."
    )


if __name__ == "__main__":
    run()
