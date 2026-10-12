"""Carrega o catálogo real (`app/db/catalogo_orion.json`) na tabela `imoveis`.

Por que não passa pelo seed
---------------------------
`app/db/seed.py` começa com `TRUNCATE imoveis RESTART IDENTITY CASCADE`, e o
CASCADE leva junto as visitas agendadas (a tabela `visitas` referencia
`imoveis`). Já custou uma demonstração: `./run.sh` apagava as visitas a cada
subida. Por isso este carregador **nunca trunca**: faz upsert por `codigo`.

Consequências do upsert:
  * rodar de novo atualiza preço, descrição e fotos sem mudar o `id` --
    visitas e encaminhamentos que apontam pro imóvel continuam válidos;
  * um anúncio que saiu do site NÃO é apagado daqui, porque pode ter visita
    marcada. Sai do catálogo marcando-se `origem='arquivado'`, à mão.

Só catálogo real
----------------
A base tinha 200 imóveis sintéticos (`origem='sintetico'`) com dado e foto
inventados. Decidiu-se usar SÓ o catálogo real: texto, valores e fotos da
própria imobiliária. Por isso `remover_legado()` tira o que não é `orion`.
`visitas.imovel_id` é nulável, então a visita que apontava pra um sintético
fica no banco com o imóvel em branco, em vez de sumir com a agenda.

O embedding é recalculado só quando a descrição muda.

    python -m scripts.carregar_catalogo            # carrega
    python -m scripts.carregar_catalogo --seco     # só mostra o que faria
"""

import argparse
import json
import sys
from pathlib import Path

from app.core.llm import embed_text
from app.db.repo import get_conn

CATALOGO = Path(__file__).resolve().parent.parent / "app" / "db" / "catalogo_orion.json"

# Rótulo gravado em `imoveis.origem`.
ORIGEM = "orion"


def _linha(item: dict) -> dict:
    """Catálogo -> colunas. Tudo o que a tabela exige NOT NULL ganha um
    valor honesto (0, não inventado) quando o anúncio não traz."""
    descricao = (item.get("descricao") or "").strip() or item["titulo"]
    return {
        "codigo": item["codigo"],
        "titulo": item["titulo"],
        "tipo": item["tipo"],
        "finalidade": item["finalidade"],
        "bairro": item["bairro"] or item["cidade"],
        "zona": item["zona"],
        "cidade": item["cidade"],
        "preco": item["preco"],
        "quartos": item.get("quartos") or 0,
        "banheiros": item.get("banheiros") or 0,
        "vagas": item.get("vagas") or 0,
        "area": item.get("area") or 0,
        "condominio": item.get("condominio"),
        "iptu": item.get("iptu"),
        "suites": item.get("suites"),
        "comodidades": item.get("comodidades") or [],
        "campos_estimados": item.get("campos_estimados") or [],
        "descricao": descricao,
        "fotos": item.get("fotos") or [],
        "origem": ORIGEM,
    }


def remover_legado(cur) -> int:
    """Tira do banco o que não veio do catálogo real. Devolve quantos saíram.

    A visita fica; só perde a referência ao imóvel. Apagá-la levaria a
    agenda dos corretores junto, e o que apontava pra um imóvel que nunca
    existiu não é motivo pra perder o compromisso do cliente."""
    cur.execute(
        "UPDATE visitas SET imovel_id = NULL "
        "WHERE imovel_id IN (SELECT id FROM imoveis WHERE origem <> %s)", (ORIGEM,)
    )
    cur.execute("DELETE FROM imoveis WHERE origem <> %s", (ORIGEM,))
    return cur.rowcount


def carregar(*, seco: bool = False, truncar: bool = False) -> dict[str, int]:
    if not CATALOGO.exists():
        raise FileNotFoundError(
            f"{CATALOGO} não existe -- rode `python -m scripts.importar_orion` antes"
        )
    itens = json.loads(CATALOGO.read_text(encoding="utf-8"))
    contagem = {"novos": 0, "atualizados": 0, "sem_foto": 0, "removidos": 0}

    with get_conn() as conn, conn.cursor() as cur:
        if truncar and not seco:
            # Destrutivo: o CASCADE leva as visitas junto. Só por `--forcar`.
            cur.execute("TRUNCATE imoveis RESTART IDENTITY CASCADE")
        for item in itens:
            linha = _linha(item)
            if not linha["fotos"]:
                contagem["sem_foto"] += 1
            cur.execute("SELECT id, descricao FROM imoveis WHERE codigo = %s", (linha["codigo"],))
            existente = cur.fetchone()

            if seco:
                contagem["atualizados" if existente else "novos"] += 1
                continue

            # Embedding só se for novo ou a descrição mudou: é a parte lenta
            # (uma chamada ao modelo por imóvel).
            if existente and existente["descricao"] == linha["descricao"]:
                cur.execute("SELECT embedding FROM imoveis WHERE id = %s", (existente["id"],))
                linha["embedding"] = cur.fetchone()["embedding"]
            else:
                linha["embedding"] = embed_text(linha["descricao"])

            cur.execute(
                """
                INSERT INTO imoveis
                    (codigo, titulo, tipo, finalidade, bairro, zona, cidade, preco,
                     quartos, banheiros, vagas, area, condominio, iptu, suites,
                     comodidades, campos_estimados, descricao, fotos, origem, embedding)
                VALUES
                    (%(codigo)s, %(titulo)s, %(tipo)s, %(finalidade)s, %(bairro)s,
                     %(zona)s, %(cidade)s, %(preco)s, %(quartos)s, %(banheiros)s,
                     %(vagas)s, %(area)s, %(condominio)s, %(iptu)s, %(suites)s,
                     %(comodidades)s, %(campos_estimados)s, %(descricao)s,
                     %(fotos)s, %(origem)s, %(embedding)s)
                ON CONFLICT (codigo) DO UPDATE SET
                    titulo = EXCLUDED.titulo, tipo = EXCLUDED.tipo,
                    finalidade = EXCLUDED.finalidade, bairro = EXCLUDED.bairro,
                    zona = EXCLUDED.zona, cidade = EXCLUDED.cidade,
                    preco = EXCLUDED.preco, quartos = EXCLUDED.quartos,
                    banheiros = EXCLUDED.banheiros, vagas = EXCLUDED.vagas,
                    area = EXCLUDED.area, condominio = EXCLUDED.condominio,
                    iptu = EXCLUDED.iptu, suites = EXCLUDED.suites,
                    comodidades = EXCLUDED.comodidades,
                    campos_estimados = EXCLUDED.campos_estimados,
                    descricao = EXCLUDED.descricao, fotos = EXCLUDED.fotos,
                    origem = EXCLUDED.origem, embedding = EXCLUDED.embedding
                """,
                linha,
            )
            contagem["atualizados" if existente else "novos"] += 1
        if not seco:
            contagem["removidos"] = remover_legado(cur)
            conn.commit()
    return contagem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seco", action="store_true", help="só conta, não grava")
    args = parser.parse_args()
    try:
        c = carregar(seco=args.seco)
    except FileNotFoundError as erro:
        print(erro, file=sys.stderr)
        return 1
    prefixo = "(seco) " if args.seco else ""
    print(f"{prefixo}{c['novos']} novos · {c['atualizados']} atualizados · "
          f"{c['sem_foto']} sem foto · {c['removidos']} legados removidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
