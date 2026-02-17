#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot do Telegram para processar vídeos do YouTube.
Fluxo:
1) Usuário envia link
2) Bot mostra menu de opções
3) Usuário confirma
4) Bot executa o pipeline completo e envia o vídeo no chat
"""

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from audio_enhancer import encontrar_ffmpeg
from dubbing import gerar_dublagem
from youtube_tool import (
    baixar_video,
    criar_pasta_video,
    extrair_video_id,
    obter_titulo_video,
    obter_transcricao,
    sanitizar_nome,
)

UPLOAD_LIMIT_BYTES = 49 * 1024 * 1024
YOUTUBE_URL_RE = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)

OPTION_LABELS = {
    "transcricao": "Transcrição",
    "download_720": "Download 720p",
    "download_1080": "Download 1080p",
    "melhoria_audio": "Melhoria de áudio",
    "dublagem_pt": "Dublagem PT-BR (voz masculina)",
}

DEFAULT_OPTIONS = {
    "transcricao": True,
    "download_720": True,
    "download_1080": True,
    "melhoria_audio": True,
    "dublagem_pt": True,
}


@dataclass
class ResultadoProcessamento:
    titulo: str
    pasta_video: str
    video_720: Optional[str] = None
    video_1080: Optional[str] = None
    video_melhorado: Optional[str] = None
    video_dublado: Optional[str] = None


def _teclado_opcoes(opcoes: Dict[str, bool]) -> InlineKeyboardMarkup:
    linhas = []
    for chave, label in OPTION_LABELS.items():
        marcado = "✅" if opcoes.get(chave, False) else "⬜"
        linhas.append([InlineKeyboardButton(f"{marcado} {label}", callback_data=f"toggle:{chave}")])

    linhas.append(
        [
            InlineKeyboardButton("Confirmar", callback_data="confirmar"),
            InlineKeyboardButton("Cancelar", callback_data="cancelar"),
        ]
    )
    return InlineKeyboardMarkup(linhas)


def _texto_menu(url: str, opcoes: Dict[str, bool]) -> str:
    total_on = sum(1 for v in opcoes.values() if v)
    return (
        "Link recebido.\n\n"
        f"URL: {url}\n"
        f"Opções ativas: {total_on}/{len(opcoes)}\n\n"
        "Selecione o que deseja e toque em Confirmar."
    )


def _obter_ffmpeg_exec() -> str:
    ffmpeg_dir = encontrar_ffmpeg()
    if ffmpeg_dir:
        return os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return "ffmpeg"


def _compactar_para_telegram(video_path: str) -> Optional[str]:
    """
    Gera versão compactada para caber no limite do Telegram.
    """
    if not os.path.exists(video_path):
        return None

    pasta = os.path.dirname(video_path)
    nome = os.path.splitext(os.path.basename(video_path))[0]
    saida = os.path.join(pasta, f"{nome}_telegram.mp4")
    ffmpeg = _obter_ffmpeg_exec()

    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "30",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        saida,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(saida):
        return None
    return saida


async def _enviar_video_ao_chat(
    bot,
    chat_id: int,
    video_path: str,
    titulo: str,
):
    tamanho = os.path.getsize(video_path)
    arquivo_envio = video_path

    if tamanho > UPLOAD_LIMIT_BYTES:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Arquivo acima do limite do Telegram (50 MB). "
                "Tentando gerar versão compactada..."
            ),
        )
        compactado = await asyncio.to_thread(_compactar_para_telegram, video_path)
        if compactado and os.path.getsize(compactado) <= UPLOAD_LIMIT_BYTES:
            arquivo_envio = compactado
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "Não consegui compactar abaixo de 50 MB.\n"
                    f"Arquivo local: {video_path}"
                ),
            )
            return

    caption = f"Pronto: {titulo}"
    with open(arquivo_envio, "rb") as f:
        await bot.send_video(
            chat_id=chat_id,
            video=f,
            filename=os.path.basename(arquivo_envio),
            caption=caption,
            read_timeout=120,
            write_timeout=120,
            supports_streaming=True,
        )


async def _processar_video_com_opcoes(
    url: str,
    opcoes: Dict[str, bool],
    bot,
    chat_id: int,
) -> Optional[ResultadoProcessamento]:
    video_id = extrair_video_id(url)
    if not video_id:
        await bot.send_message(chat_id=chat_id, text="URL inválida do YouTube.")
        return None

    await bot.send_message(chat_id=chat_id, text="Obtendo informações do vídeo...")
    try:
        titulo = await asyncio.to_thread(obter_titulo_video, url)
    except Exception:
        titulo = video_id

    pasta_video = await asyncio.to_thread(criar_pasta_video, titulo)
    resultado = ResultadoProcessamento(titulo=titulo, pasta_video=pasta_video)
    nome = sanitizar_nome(titulo)

    segmentos_pt = None
    if opcoes.get("transcricao", False):
        await bot.send_message(chat_id=chat_id, text="Buscando transcrição...")
        dados_transcricao = await asyncio.to_thread(obter_transcricao, video_id, titulo, pasta_video)
        if dados_transcricao and dados_transcricao.get("sucesso"):
            segmentos_pt = dados_transcricao.get("segmentos_pt")
        else:
            await bot.send_message(chat_id=chat_id, text="Não foi possível obter transcrição.")

    if opcoes.get("download_720", False):
        await bot.send_message(chat_id=chat_id, text="Baixando vídeo 720p...")
        ok_720 = await asyncio.to_thread(baixar_video, url, titulo, "720", pasta_video)
        if ok_720:
            candidato = os.path.join(pasta_video, f"{nome}_720p.mp4")
            if os.path.exists(candidato):
                resultado.video_720 = candidato

    if opcoes.get("download_1080", False):
        await bot.send_message(chat_id=chat_id, text="Baixando vídeo 1080p...")
        ok_1080 = await asyncio.to_thread(baixar_video, url, titulo, "1080", pasta_video)
        if ok_1080:
            candidato = os.path.join(pasta_video, f"{nome}_1080p.mp4")
            if os.path.exists(candidato):
                resultado.video_1080 = candidato

    candidatos_base = [
        resultado.video_1080,
        resultado.video_720,
        os.path.join(pasta_video, f"{nome}_1080p.mp4"),
        os.path.join(pasta_video, f"{nome}_720p.mp4"),
    ]
    video_base = next((p for p in candidatos_base if p and os.path.exists(p)), None)

    if opcoes.get("melhoria_audio", False) and video_base:
        await bot.send_message(chat_id=chat_id, text="Melhorando áudio...")
        from audio_enhancer import melhorar_audio  # import local para evitar ciclos
        video_melhorado = await asyncio.to_thread(melhorar_audio, video_base, pasta_video, None)
        if video_melhorado and os.path.exists(video_melhorado):
            resultado.video_melhorado = video_melhorado

    if opcoes.get("dublagem_pt", False):
        video_base_dublagem = resultado.video_melhorado or video_base
        if segmentos_pt and video_base_dublagem and os.path.exists(video_base_dublagem):
            await bot.send_message(chat_id=chat_id, text="Gerando dublagem PT-BR (voz masculina)...")
            video_dublado = await asyncio.to_thread(
                gerar_dublagem,
                video_base_dublagem,
                segmentos_pt,
                pasta_video,
                "masculina",
                None,
            )
            if video_dublado and os.path.exists(video_dublado):
                resultado.video_dublado = video_dublado
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="Dublagem pulada: sem transcrição PT ou sem vídeo base disponível.",
            )

    return resultado


async def _executar_job(
    application: Application,
    chat_id: int,
    user_id: int,
    url: str,
    opcoes: Dict[str, bool],
):
    bot = application.bot
    jobs = application.bot_data.setdefault("jobs", {})

    try:
        await bot.send_message(chat_id=chat_id, text="Processo iniciado. Isso pode levar alguns minutos.")
        resultado = await _processar_video_com_opcoes(url, opcoes, bot, chat_id)
        if not resultado:
            return

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Concluído.\n"
                f"Título: {resultado.titulo}\n"
                f"Pasta: {resultado.pasta_video}"
            ),
        )

        arquivo_envio = (
            resultado.video_dublado
            or resultado.video_melhorado
            or resultado.video_1080
            or resultado.video_720
        )
        if arquivo_envio and os.path.exists(arquivo_envio):
            await _enviar_video_ao_chat(bot, chat_id, arquivo_envio, resultado.titulo)
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="Processo finalizado, mas não encontrei arquivo de vídeo para envio.",
            )
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"Erro durante processamento: {e}")
    finally:
        jobs.pop(user_id, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envie um link do YouTube.\n"
        "Vou abrir um menu para você escolher as opções e confirmar."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Uso:\n"
        "1) Envie um link do YouTube\n"
        "2) Marque/desmarque opções\n"
        "3) Toque em Confirmar"
    )


async def receber_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip()
    if not YOUTUBE_URL_RE.search(texto):
        return

    video_id = extrair_video_id(texto)
    if not video_id:
        await update.message.reply_text("Não consegui identificar um link válido do YouTube.")
        return

    jobs = context.application.bot_data.setdefault("jobs", {})
    user_id = update.effective_user.id
    if jobs.get(user_id):
        await update.message.reply_text("Você já possui um processamento em andamento. Aguarde finalizar.")
        return

    opcoes = DEFAULT_OPTIONS.copy()
    context.user_data["pending_url"] = texto
    context.user_data["pending_options"] = opcoes

    await update.message.reply_text(
        _texto_menu(texto, opcoes),
        reply_markup=_teclado_opcoes(opcoes),
    )


async def callback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    opcoes = context.user_data.get("pending_options")
    url = context.user_data.get("pending_url")
    if not opcoes or not url:
        await query.edit_message_text("Sessão expirada. Envie o link novamente.")
        return

    data = query.data or ""
    if data.startswith("toggle:"):
        chave = data.split(":", 1)[1]
        if chave in opcoes:
            opcoes[chave] = not opcoes[chave]
            context.user_data["pending_options"] = opcoes
            await query.edit_message_text(
                _texto_menu(url, opcoes),
                reply_markup=_teclado_opcoes(opcoes),
            )
        return

    if data == "cancelar":
        context.user_data.pop("pending_options", None)
        context.user_data.pop("pending_url", None)
        await query.edit_message_text("Processo cancelado.")
        return

    if data == "confirmar":
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        jobs = context.application.bot_data.setdefault("jobs", {})
        if jobs.get(user_id):
            await query.edit_message_text("Já existe um processamento em andamento para você.")
            return

        opcoes_exec = dict(opcoes)
        url_exec = str(url)

        context.user_data.pop("pending_options", None)
        context.user_data.pop("pending_url", None)

        await query.edit_message_text("Configuração confirmada. Iniciando processamento...")
        task = asyncio.create_task(
            _executar_job(context.application, chat_id, user_id, url_exec, opcoes_exec)
        )
        jobs[user_id] = task


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Defina a variável de ambiente TELEGRAM_BOT_TOKEN antes de iniciar.")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(callback_menu))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_link))

    print("Bot do Telegram iniciado.")
    application.run_polling()


if __name__ == "__main__":
    main()
