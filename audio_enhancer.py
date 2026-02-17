#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo de Melhoria de Áudio — Pipeline FFmpeg para tratamento de áudio.
Extrai, normaliza, reduz ruído e remescla com o vídeo.
"""

import os
import shutil
import subprocess


def encontrar_ffmpeg() -> str | None:
    """Retorna o diretório do FFmpeg se encontrado, ou None se estiver no PATH."""
    if shutil.which("ffmpeg"):
        return None  # Já está no PATH

    # Caminho do winget
    ffmpeg_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WinGet", "Packages",
        "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "ffmpeg-8.0.1-full_build", "bin"
    )
    if os.path.exists(os.path.join(ffmpeg_dir, "ffmpeg.exe")):
        return ffmpeg_dir

    # Locais comuns
    for local in [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]:
        if os.path.exists(os.path.join(local, "ffmpeg.exe")):
            return local

    return None


def _get_ffmpeg_cmd(ffmpeg_dir: str | None) -> str:
    """Retorna o caminho completo do ffmpeg."""
    if ffmpeg_dir:
        return os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return "ffmpeg"


def melhorar_audio(video_path: str, pasta_saida: str, ffmpeg_dir: str | None = None) -> str | None:
    """
    Pipeline de melhoria de áudio:
    1. Extrai áudio do vídeo
    2. Aplica filtros: loudnorm, afftdn, highpass, lowpass, acompressor
    3. Remescla áudio tratado com o vídeo

    Args:
        video_path: Caminho do vídeo MP4
        pasta_saida: Pasta para salvar o resultado
        ffmpeg_dir: Diretório do FFmpeg (None = usar PATH)

    Returns:
        Caminho do vídeo com áudio melhorado ou None se falhar
    """
    ffmpeg = _get_ffmpeg_cmd(ffmpeg_dir)
    nome_base = os.path.splitext(os.path.basename(video_path))[0]

    # Arquivos intermediários
    audio_original = os.path.join(pasta_saida, f"{nome_base}_audio_temp.wav")
    audio_melhorado = os.path.join(pasta_saida, f"{nome_base}_audio_tratado.wav")
    video_final = os.path.join(pasta_saida, f"{nome_base}_audio_melhorado.mp4")

    print(f"\n Melhorando áudio de: {os.path.basename(video_path)}")

    try:
        # 1. Extrair áudio
        print("    Extraindo áudio...")
        cmd_extrair = [
            ffmpeg, "-y",
            "-i", video_path,
            "-vn",                    # Sem vídeo
            "-acodec", "pcm_s16le",   # WAV 16-bit
            "-ar", "44100",           # 44.1kHz
            "-ac", "2",               # Stereo
            audio_original
        ]
        result = subprocess.run(cmd_extrair, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   [X] Erro ao extrair áudio: {result.stderr[:300]}")
            return None

        # 2. Aplicar pipeline de filtros
        print("     Aplicando filtros de melhoria...")
        filtros = ",".join([
            "highpass=f=80",            # Remove rumble/graves indesejados
            "lowpass=f=12000",          # Remove sibilância/agudos extremos
            "afftdn=nf=-25",            # Redução de ruído (FFT denoise)
            "acompressor=threshold=-20dB:ratio=4:attack=5:release=50",  # Compressão dinâmica
            "loudnorm=I=-16:TP=-1.5:LRA=11",  # Normalização EBU R128
        ])

        cmd_filtros = [
            ffmpeg, "-y",
            "-i", audio_original,
            "-af", filtros,
            "-ar", "44100",
            audio_melhorado
        ]
        result = subprocess.run(cmd_filtros, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   [X] Erro nos filtros: {result.stderr[:300]}")
            return None

        # 3. Remesclar áudio tratado com vídeo
        print("    Mesclando áudio tratado com vídeo...")
        cmd_mesclar = [
            ffmpeg, "-y",
            "-i", video_path,          # Vídeo original
            "-i", audio_melhorado,     # Áudio tratado
            "-c:v", "copy",            # Copiar vídeo sem recodificar
            "-c:a", "aac",             # Codificar áudio em AAC
            "-b:a", "192k",            # Bitrate do áudio
            "-map", "0:v:0",           # Vídeo do input 0
            "-map", "1:a:0",           # Áudio do input 1
            "-shortest",
            video_final
        ]
        result = subprocess.run(cmd_mesclar, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   [X] Erro ao mesclar: {result.stderr[:300]}")
            return None

        # Limpar arquivos temporários
        for f in [audio_original, audio_melhorado]:
            try:
                os.remove(f)
            except OSError:
                pass

        tamanho = os.path.getsize(video_final) / (1024 * 1024)
        print(f"   [OK] Áudio melhorado: {video_final} ({tamanho:.1f} MB)")
        return video_final

    except Exception as e:
        print(f"   [X] Erro na melhoria de áudio: {e}")
        # Limpar arquivos temporários em caso de erro
        for f in [audio_original, audio_melhorado]:
            try:
                os.remove(f)
            except OSError:
                pass
        return None
