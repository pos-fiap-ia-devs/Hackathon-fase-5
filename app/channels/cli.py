"""Canal CLI -- fallback final se Telegram/rede cair na demo (secao 10 e 12
do PLANO.md: "demonstra o agente sem rede nenhuma"). Mesmo `processar_turno`
do Telegram e do web chat -- e a prova de que canal e so um adapter fino
(secao 5.8).

Uso: python -m app.channels.cli
"""

import sys
import uuid

from app.core.turno import RESPOSTA_FALLBACK, processar_turno


def run() -> None:
    chat_id = f"cli:{uuid.uuid4().hex[:8]}"
    print("Agente SDR Imobiliario -- canal CLI (sem Telegram, sem rede).")
    print(f"lead: {chat_id}  (Ctrl+D ou 'sair' para encerrar)\n")

    while True:
        try:
            mensagem = input("voce> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nate mais!")
            break

        if not mensagem:
            continue
        if mensagem.lower() in ("sair", "exit", "quit"):
            print("ate mais!")
            break

        try:
            resposta = processar_turno(chat_id=chat_id, canal="cli", mensagem=mensagem, origem="texto")
        # Excecao nunca pode travar o loop da demo.
        except Exception as e:  # noqa: BLE001
            print(f"[erro: {e}]", file=sys.stderr)
            resposta = RESPOSTA_FALLBACK

        print(f"bia > {resposta}\n")

        # Terminal nao renderiza imagem: o canal entrega o que sabe
        # entregar -- as URLs das fotos, na ordem dos cards.
        for item in getattr(resposta, "galeria", []):
            print(f"       fotos {item['numero']}: " + " ".join(item["fotos"]))
        if getattr(resposta, "galeria", []):
            print()


if __name__ == "__main__":
    run()
