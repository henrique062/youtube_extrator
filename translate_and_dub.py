#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para traduzir transcrição localmente e gerar dublagem PT-BR.
"""

import os
import re
import socket

from deep_translator import GoogleTranslator

from audio_enhancer import encontrar_ffmpeg
from dubbing import gerar_dublagem

TIMESTAMP_RANGE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)s\s*-\s*(\d+(?:\.\d+)?)s\]\s*(.*)$")
TIMESTAMP_START_RE = re.compile(r"^\[(\d+(?:\.\d+)?)s\]\s*(.*)$")


def _estimar_duracao(texto: str) -> float:
    """Estimativa de fala para fallback sem timestamps."""
    palavras = len(texto.split())
    return max(1.2, palavras / 2.8)  # ~168 palavras/minuto


def _extrair_corpo_transcricao(conteudo: str) -> list[str]:
    """Retorna linhas úteis após o cabeçalho da transcrição."""
    linhas = conteudo.splitlines()
    corpo = []
    iniciou = False

    for linha in linhas:
        if re.match(r"^={10,}$", linha.strip()):
            iniciou = True
            continue
        if iniciou and linha.strip():
            corpo.append(linha.strip())

    return corpo


def _segmentar_com_timestamps(linhas: list[str]) -> list[dict]:
    """Converte linhas timestampadas em segmentos."""
    segmentos = []

    for linha in linhas:
        m_range = TIMESTAMP_RANGE_RE.match(linha)
        if m_range:
            start = float(m_range.group(1))
            end = float(m_range.group(2))
            texto = m_range.group(3).strip()
            if texto:
                segmentos.append({
                    "text": texto,
                    "start": start,
                    "duration": max(0.2, end - start),
                })
            continue

        m_start = TIMESTAMP_START_RE.match(linha)
        if m_start:
            start = float(m_start.group(1))
            texto = m_start.group(2).strip()
            if texto:
                segmentos.append({
                    "text": texto,
                    "start": start,
                    "duration": None,
                })

    if not segmentos:
        return []

    for i, seg in enumerate(segmentos):
        if seg["duration"] is not None:
            continue
        if i + 1 < len(segmentos):
            next_start = segmentos[i + 1]["start"]
            seg["duration"] = max(0.2, next_start - seg["start"])
        else:
            seg["duration"] = _estimar_duracao(seg["text"])

    return segmentos


def _segmentar_sem_timestamps(linhas: list[str]) -> list[dict]:
    """Fallback para transcrições antigas sem timestamps."""
    texto = " ".join(linhas).strip()
    if not texto:
        return []

    sentencas = [s.strip() for s in re.split(r"(?<=[.!?])\s+", texto) if s.strip()]
    if not sentencas:
        sentencas = []
        palavras = texto.split()
        bloco = []
        for palavra in palavras:
            bloco.append(palavra)
            if len(bloco) >= 14:
                sentencas.append(" ".join(bloco))
                bloco = []
        if bloco:
            sentencas.append(" ".join(bloco))

    segmentos = []
    tempo_atual = 0.0
    for sentenca in sentencas:
        duracao = _estimar_duracao(sentenca)
        segmentos.append({
            "text": sentenca,
            "start": tempo_atual,
            "duration": duracao,
        })
        tempo_atual += duracao

    return segmentos


def _carregar_segmentos_transcricao(arquivo_transcricao: str) -> list[dict]:
    with open(arquivo_transcricao, "r", encoding="utf-8") as f:
        conteudo = f.read()

    linhas = _extrair_corpo_transcricao(conteudo)
    segmentos = _segmentar_com_timestamps(linhas)
    if segmentos:
        return segmentos
    return _segmentar_sem_timestamps(linhas)


def traduzir_transcricao_para_dublagem(arquivo_transcricao: str, pasta_video: str, video_path: str):
    """
    Lê transcrição, traduz para PT-BR e gera dublagem.
    """
    print(f"\n[1/4] Lendo transcrição: {arquivo_transcricao}")
    segmentos_origem = _carregar_segmentos_transcricao(arquivo_transcricao)
    if not segmentos_origem:
        print("[X] Não foi possível extrair segmentos da transcrição.")
        return None

    print(f"\n[2/4] Segmentos detectados: {len(segmentos_origem)}")
    print("\n[3/4] Traduzindo segmentos para PT-BR...")
    translator = GoogleTranslator(source="auto", target="pt")
    timeout_padrao = socket.getdefaulttimeout()
    socket.setdefaulttimeout(20)

    segmentos_pt = []
    try:
        for i, seg in enumerate(segmentos_origem):
            try:
                texto_pt = translator.translate(seg["text"]) or seg["text"]
                segmentos_pt.append({
                    "text": texto_pt,
                    "start": float(seg["start"]),
                    "duration": float(seg["duration"]),
                })
                if (i + 1) % 20 == 0:
                    print(f"   Traduzidos: {i + 1}/{len(segmentos_origem)}")
            except Exception as e:
                print(f"   [!] Erro no segmento {i}: {e}")
    finally:
        socket.setdefaulttimeout(timeout_padrao)

    if not segmentos_pt:
        print("[X] Nenhum segmento traduzido.")
        return None

    print(f"   [OK] {len(segmentos_pt)} segmentos traduzidos!")

    arquivo_traducao = arquivo_transcricao.replace("_transcricao_original.txt", "_transcricao_PT_traduzida.txt")
    with open(arquivo_traducao, "w", encoding="utf-8") as f:
        f.write("Transcrição Traduzida para Português (PT-BR)\n")
        f.write("=" * 60 + "\n\n")
        for seg in segmentos_pt:
            fim = seg["start"] + seg["duration"]
            f.write(f"[{seg['start']:.2f}s - {fim:.2f}s] {seg['text']}\n")

    print(f"   Tradução salva: {arquivo_traducao}")

    print("\n[4/4] Gerando dublagem...")
    ffmpeg_dir = encontrar_ffmpeg()
    if ffmpeg_dir:
        ffmpeg_path = os.path.join(ffmpeg_dir, "ffmpeg.exe")
    else:
        ffmpeg_path = "ffmpeg"

    video_dublado = gerar_dublagem(
        video_path,
        segmentos_pt,
        pasta_video,
        ffmpeg_path=ffmpeg_path,
    )

    if video_dublado:
        print("\n[OK] Dublagem concluída com sucesso!")
        print(f"   Arquivo: {video_dublado}")
        return video_dublado

    print("\n[X] Falha ao gerar dublagem")
    return None


if __name__ == "__main__":
    # Configurar caminhos
    pasta_video = r"D:\1 - Projetos pessoais\youtube_extrator\downloads\17-02-26 I Built My Second Brain with Claude Code + Obsidian + Skills (Here's How)"

    arquivo_transcricao = os.path.join(
        pasta_video,
        "I Built My Second Brain with Claude Code + Obsidian + Skills (Here's How)_transcricao_original.txt"
    )

    video_path = os.path.join(
        pasta_video,
        "I Built My Second Brain with Claude Code + Obsidian + Skills (Here's How)_1080p_audio_melhorado.mp4"
    )

    # Verificar se arquivos existem
    if not os.path.exists(arquivo_transcricao):
        print(f"[X] Transcrição não encontrada: {arquivo_transcricao}")
        raise SystemExit(1)

    if not os.path.exists(video_path):
        print(f"[X] Vídeo não encontrado: {video_path}")
        raise SystemExit(1)

    # Executar
    resultado = traduzir_transcricao_para_dublagem(arquivo_transcricao, pasta_video, video_path)

    if resultado:
        print("\n" + "=" * 60)
        print("SUCESSO! Vídeo dublado gerado:")
        print(resultado)
        print("=" * 60)
