"""Canal Telegram -- long polling (secao 4 do PLANO.md: elimina o risco de
tunel/webhook caindo no meio da demo).

Adapter fino: recebe update, chama o orquestrador de turno, manda a resposta
de volta. Nao sabe nada de LLM, banco ou agentes -- texto entra, texto (mais
as fotos dos imoveis, quando o turno apresenta imoveis) sai; como isso vira
album de fotos no Telegram e decisao deste arquivo, nao do orquestrador,
que so devolve as URLs. E o que faz "trocar de canal = escrever um adapter" ser verdade
(secao 5.8) -- este arquivo e o channels/cli.py chamam exatamente a mesma
`processar_turno`.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import httpx

from telegram import InputMediaPhoto, Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.core import followup, stt
from app.core.config import get_settings
from app.core.turno import RESPOSTA_FALLBACK, processar_turno
from app.db import repo

logger = logging.getLogger(__name__)

RESPOSTA_AUDIO_FALHOU = "Não consegui entender o áudio agora. Pode escrever a mensagem, por favor?"


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    telegram_chat_id = update.effective_chat.id
    lead_chat_id = f"tg:{telegram_chat_id}"  # id interno do lead, nao confundir com o id do Telegram

    await context.bot.send_chat_action(chat_id=telegram_chat_id, action=ChatAction.TYPING)

    origem = "texto"
    if update.message.text:
        texto = update.message.text
    elif update.message.voice or update.message.audio:
        texto = await _transcrever_mensagem(update, context)
        origem = "audio"
        if texto is None:
            await update.message.reply_text(RESPOSTA_AUDIO_FALHOU)
            return
    else:
        return

    # "digitando..." do Telegram expira em ~5s, e um turno leva ~10-13s --
    # sem renovar, o cliente ve o indicador sumir no meio e acha que travou.
    # Roda em paralelo ao turno e e cancelado assim que a resposta sai.
    digitando = asyncio.create_task(_manter_digitando(context, telegram_chat_id))
    try:
        resposta = await _processar_em_thread(lead_chat_id, texto, origem)
    except Exception:
        logger.exception("falha inesperada processando turno (chat_id=%s)", lead_chat_id)
        resposta = RESPOSTA_FALLBACK
    finally:
        digitando.cancel()

    await update.message.reply_text(resposta)
    # Fotos depois do texto: o cliente le a lista numerada e ve os albuns na
    # mesma ordem logo abaixo. `getattr` porque o fallback de excecao acima
    # e uma string comum, sem galeria.
    await _enviar_galeria(context, telegram_chat_id, getattr(resposta, "galeria", []))


async def _enviar_galeria(context: ContextTypes.DEFAULT_TYPE, telegram_chat_id: int, galeria: list[dict]) -> None:
    """Um album por imovel (pedido do usuario: fotos junto com cada
    casa/apartamento), legenda na primeira foto com o mesmo numero do card
    -- e assim que o cliente liga a foto ao "quero o 2".

    Falha aqui nunca derruba o turno: o texto com preco, bairro e numero ja
    foi entregue: a foto e complemento. O caso realista de falha e o
    Telegram nao conseguir baixar a URL da foto (ele busca a imagem pelo
    proprio servidor), e um imovel sem album e melhor que um turno perdido."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        for item in galeria:
            midias = []
            for url in item["fotos"]:
                imagem = await _baixar_foto(http, url)
                if imagem:
                    legenda = item["legenda"] if not midias else None
                    midias.append(InputMediaPhoto(media=imagem, caption=legenda))
            if not midias:
                continue
            try:
                await context.bot.send_media_group(chat_id=telegram_chat_id, media=midias)
            except Exception:
                logger.warning(
                    "falha enviando fotos do imovel %s (chat=%s) -- segue sem album",
                    item.get("imovel_id"), telegram_chat_id, exc_info=True,
                )


async def _baixar_foto(http: "httpx.AsyncClient", url: str) -> bytes | None:
    """Baixa a imagem aqui e sobe os bytes, em vez de passar a URL pro
    Telegram buscar sozinho. O servico de fotos responde com um 302 pro
    arquivo final (ver app/db/seed.py), e depender do servidor do Telegram
    seguir esse redirect de terceiro e apostar num comportamento que nao
    esta no nosso controle -- baixar aqui torna o envio um upload comum."""
    try:
        r = await http.get(url)
        r.raise_for_status()
        return r.content
    except Exception:
        logger.warning("falha baixando foto %s -- segue sem ela", url, exc_info=True)
        return None


async def _manter_digitando(context: ContextTypes.DEFAULT_TYPE, telegram_chat_id: int) -> None:
    """Renova o chat action ate ser cancelado. Falha de rede aqui nunca
    pode derrubar o turno -- e so indicador visual."""
    try:
        while True:
            await asyncio.sleep(4)
            await context.bot.send_chat_action(chat_id=telegram_chat_id, action=ChatAction.TYPING)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("falha renovando 'digitando' (chat_id=%s)", telegram_chat_id, exc_info=True)


async def _transcrever_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Baixa o voice/audio do Telegram (sempre OGG/Opus) pra um arquivo
    temporario e transcreve (Etapa 8, diferencial). `stt.transcrever` cuida
    da conversao pra WAV, guard de duracao e timeout -- aqui e so
    download+chamada."""
    arquivo_telegram = update.message.voice or update.message.audio
    with tempfile.TemporaryDirectory() as tmp:
        caminho_ogg = str(Path(tmp) / "audio.ogg")
        arquivo = await context.bot.get_file(arquivo_telegram.file_id)
        await arquivo.download_to_drive(caminho_ogg)
        return await asyncio.to_thread(stt.transcrever, caminho_ogg)


async def _processar_em_thread(lead_chat_id: str, texto: str, origem: str = "texto") -> str:
    """`processar_turno` e sincrona (psycopg + httpx bloqueantes, ~9-10s por
    turno -- secao 5.2). Rodar em thread evita travar o loop de eventos do
    bot enquanto o LLM responde."""
    return await asyncio.to_thread(
        processar_turno, chat_id=lead_chat_id, canal="telegram", mensagem=texto, origem=origem
    )


async def _checar_followups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periodico (Etapa 6, cenario 3 do PDF) -- varre follow_up_jobs
    vencidos e envia as mensagens de reengajamento. So sabe empurrar
    mensagem pro Telegram (unico canal com push de verdade neste projeto);
    job de lead de outro canal (cli, web) e cancelado aqui, nao processado."""
    jobs = await asyncio.to_thread(repo.get_followups_vencidos)
    for job in jobs:
        if job["canal"] != "telegram":
            await asyncio.to_thread(repo.marcar_followup_cancelado, job["job_id"])
            continue

        try:
            mensagem = await asyncio.to_thread(followup.processar_job, job)
        except Exception:
            logger.exception("falha processando follow-up (lead_id=%s)", job["lead_id"])
            continue

        if not mensagem:
            continue

        telegram_chat_id = int(job["chat_id"].removeprefix("tg:"))
        try:
            await context.bot.send_message(chat_id=telegram_chat_id, text=mensagem)
        except Exception:
            logger.exception("falha enviando follow-up (lead_id=%s)", job["lead_id"])


async def _checar_inatividade(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periodico separado do follow-up (pedido do usuario) -- fecha
    atendimento depois de `INATIVIDADE_FECHAMENTO_MIN` minutos de silencio
    total, independente das tentativas de reengajamento do follow-up.
    `verificar_inatividade` ja persiste a mensagem e muda o status pra
    TODO canal; aqui so entrega de fato pro Telegram (mesmo padrao de
    `_checar_followups` -- so este canal tem push de verdade)."""
    encerrados = await asyncio.to_thread(followup.verificar_inatividade)
    for item in encerrados:
        if item["canal"] != "telegram":
            continue
        telegram_chat_id = int(item["chat_id"].removeprefix("tg:"))
        try:
            await context.bot.send_message(chat_id=telegram_chat_id, text=item["mensagem"])
        except Exception:
            logger.exception("falha enviando encerramento por inatividade (lead_id=%s)", item["lead_id"])


def build_app() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(
        MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.VOICE | filters.AUDIO, _on_message)
    )
    application.job_queue.run_repeating(_checar_followups, interval=settings.followup_scan_s, first=10)
    application.job_queue.run_repeating(_checar_inatividade, interval=settings.followup_scan_s, first=15)
    return application


def run_polling() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN vazio -- canal Telegram desligado. Configure no .env.")
        return
    app = build_app()
    logger.info(
        "Telegram: long polling iniciado. Follow-up a cada %ss (DEMO_MODE=%s).",
        settings.followup_scan_s, settings.demo_mode,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # httpx loga a URL inteira da requisicao em INFO -- e o token do bot vai
    # embutido na URL (e como a API do Telegram funciona: /bot<TOKEN>/metodo).
    # Suprimir aqui evita vazar o token em log de texto puro (secao 7 do
    # PLANO.md: seguranca e mascaramento de dado sensivel em log).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_polling()
