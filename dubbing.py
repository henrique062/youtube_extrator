#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo de Dublagem — Gera versão dublada em PT-BR usando edge-tts.
Sincroniza áudio TTS com timestamps da transcrição traduzida.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

import edge_tts

from audio_enhancer import encontrar_ffmpeg

# Fix para Windows: usar SelectorEventLoop
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# Vozes disponíveis em PT-BR
VOZES = {
    "feminina": "pt-BR-FranciscaNeural",
    "masculina": "pt-BR-AntonioNeural",
}

VOZ_PADRAO = "masculina"


async def _gerar_segmento_tts(texto: str, voz: str, caminho_saida: str):
    """Gera um arquivo de áudio TTS para um segmento de texto."""
    communicate = edge_tts.Communicate(texto, voz)
    await communicate.save(caminho_saida)


def _get_ffprobe_cmd(ffmpeg_path: str) -> str:
    """Descobre o comando do ffprobe com base no ffmpeg."""
    base = os.path.basename(ffmpeg_path).lower()
    if base.startswith("ffmpeg"):
        pasta = os.path.dirname(ffmpeg_path)
        if pasta:
            nome = "ffprobe.exe" if os.name == "nt" else "ffprobe"
            candidato = os.path.join(pasta, nome)
            if os.path.exists(candidato):
                return candidato
    return "ffprobe"


def _obter_duracao_audio(caminho_audio: str, ffprobe_path: str) -> float:
    """Retorna duração do arquivo de áudio em segundos."""
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        caminho_audio
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((result.stdout or "").strip()))
    except (TypeError, ValueError):
        return 0.0


def _cadeia_atempo(fator_total: float) -> str:
    """
    Cria cadeia de filtros atempo (cada estágio aceita apenas 0.5..2.0).
    fator_total > 1 acelera e encurta o áudio.
    """
    fator_restante = max(0.5, float(fator_total))
    filtros = []

    while fator_restante > 2.0:
        filtros.append("atempo=2.0")
        fator_restante /= 2.0

    filtros.append(f"atempo={fator_restante:.6f}")
    return ",".join(filtros)


def _ajustar_segmento_para_janela(
    arquivo_in: str,
    arquivo_out: str,
    duracao_alvo: float,
    ffmpeg_path: str,
    ffprobe_path: str,
):
    """
    Ajusta segmento TTS para caber na janela alvo.
    - Se o segmento for longo, acelera (atempo) e recorta.
    - Se for curto, mantém velocidade e preenche com silêncio.
    """
    duracao_alvo = max(0.15, float(duracao_alvo))
    duracao_real = _obter_duracao_audio(arquivo_in, ffprobe_path)

    if duracao_real <= 0:
        filtro = f"apad=pad_dur={duracao_alvo:.6f},atrim=0:{duracao_alvo:.6f}"
    elif duracao_real > duracao_alvo:
        fator = duracao_real / duracao_alvo
        filtro = (
            f"{_cadeia_atempo(fator)},"
            f"apad=pad_dur={duracao_alvo:.6f},"
            f"atrim=0:{duracao_alvo:.6f}"
        )
    else:
        filtro = f"apad=pad_dur={duracao_alvo:.6f},atrim=0:{duracao_alvo:.6f}"

    cmd = [
        ffmpeg_path, "-y",
        "-i", arquivo_in,
        "-af", filtro,
        "-ar", "44100",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        arquivo_out
    ]
    subprocess.run(cmd, capture_output=True)


def _normalizar_segmentos(segmentos: list[dict]) -> list[dict]:
    """
    Normaliza segmentos para manter consistência de tempo e reduzir dessincronia.
    """
    limpos = []
    for seg in segmentos:
        texto = (seg.get("text") or "").strip()
        if not texto:
            continue
        start = max(0.0, float(seg.get("start", 0.0)))
        duration = max(0.0, float(seg.get("duration", 0.0)))
        limpos.append({"text": texto, "start": start, "duration": duration})

    limpos.sort(key=lambda s: s["start"])
    if not limpos:
        return limpos

    for i in range(len(limpos)):
        if limpos[i]["duration"] > 0:
            continue
        if i + 1 < len(limpos):
            prox = limpos[i + 1]["start"]
            limpos[i]["duration"] = max(0.15, prox - limpos[i]["start"])
        else:
            # fallback simples para último trecho sem duração
            palavras = len(limpos[i]["text"].split())
            limpos[i]["duration"] = max(0.6, palavras / 2.8)

    return limpos


async def _gerar_audio_completo(segmentos: list, voz: str, pasta_temp: str, ffmpeg_path: str) -> str:
    """
    Gera áudio TTS completo sincronizado com timestamps.
    
    segmentos: lista de dicts com 'text', 'start', 'duration'
    """
    ffprobe_path = _get_ffprobe_cmd(ffmpeg_path)
    segmentos = _normalizar_segmentos(segmentos)
    arquivos_segmento = []
    
    for i, seg in enumerate(segmentos):
        arquivo_tts = os.path.join(pasta_temp, f"seg_raw_{i:05d}.mp3")
        arquivo_sync = os.path.join(pasta_temp, f"seg_{i:05d}.wav")
        
        try:
            await _gerar_segmento_tts(seg["text"], voz, arquivo_tts)
            _ajustar_segmento_para_janela(
                arquivo_tts,
                arquivo_sync,
                seg["duration"],
                ffmpeg_path,
                ffprobe_path,
            )
            try:
                os.remove(arquivo_tts)
            except OSError:
                pass
            arquivos_segmento.append({
                "arquivo": arquivo_sync,
                "start": seg["start"],
                "duration": seg["duration"],
                "index": i,
            })
            if (i + 1) % 20 == 0:
                print(f"      TTS sincronizados: {i + 1}/{len(segmentos)}")
        except Exception as e:
            print(f"   [!]  Segmento {i} falhou: {e}")
            continue
    
    if not arquivos_segmento:
        raise Exception("Nenhum segmento de áudio foi gerado")
    
    # Calcular duração total do áudio com base no último término conhecido
    duracao_total = max((float(seg["start"]) + float(seg["duration"]) for seg in segmentos), default=0.0) + 1.0
    
    # Criar arquivo de silêncio base com a duração total
    silencio_base = os.path.join(pasta_temp, "silencio_base.wav")
    cmd_silencio = [
        ffmpeg_path, "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duracao_total),
        silencio_base
    ]
    subprocess.run(cmd_silencio, capture_output=True)
    
    # Construir filtro complexo para sobrepor cada segmento no tempo correto
    inputs = ["-i", silencio_base]
    filter_parts = []
    
    for idx, seg_info in enumerate(arquivos_segmento):
        inputs.extend(["-i", seg_info["arquivo"]])
        input_idx = idx + 1  # 0 é o silêncio base
        delay_ms = int(seg_info["start"] * 1000)
        filter_parts.append(
            f"[{input_idx}]aresample=44100,aformat=channel_layouts=mono,adelay={delay_ms}[d{idx}]"
        )
    
    # Mixar todos os segmentos
    mix_inputs = "[0]" + "".join(f"[d{i}]" for i in range(len(arquivos_segmento)))
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(arquivos_segmento) + 1}:duration=longest:normalize=0:dropout_transition=0[out]"
    )
    
    filtro_completo = ";".join(filter_parts)
    filtro_path = os.path.join(pasta_temp, "mix_filter.txt")
    with open(filtro_path, "w", encoding="utf-8") as f:
        f.write(filtro_completo)
    
    audio_completo = os.path.join(pasta_temp, "dublagem_completa.wav")
    cmd_mix = [
        ffmpeg_path, "-y",
        *inputs,
        "-filter_complex_script", filtro_path,
        "-map", "[out]",
        "-ar", "44100",
        audio_completo
    ]
    
    result = subprocess.run(cmd_mix, capture_output=True, text=True)
    if result.returncode != 0:
        # Fallback: concatenar linearmente se o filtro complexo falhar
        print("   [!]  Mix complexo falhou, usando concatenação simples...")
        return await _concatenar_simples(arquivos_segmento, pasta_temp, ffmpeg_path)
    
    return audio_completo


async def _concatenar_simples(arquivos_segmento: list, pasta_temp: str, ffmpeg_path: str) -> str:
    """Fallback: concatena segmentos TTS linearmente."""
    lista_path = os.path.join(pasta_temp, "concat_list.txt")
    with open(lista_path, "w", encoding="utf-8") as f:
        for seg_info in arquivos_segmento:
            f.write(f"file '{seg_info['arquivo']}'\n")
    
    audio_concat = os.path.join(pasta_temp, "dublagem_concat.wav")
    cmd = [
        ffmpeg_path, "-y",
        "-f", "concat", "-safe", "0",
        "-i", lista_path,
        "-c:a", "pcm_s16le",
        "-ar", "44100",
        audio_concat
    ]
    subprocess.run(cmd, capture_output=True)
    return audio_concat


def gerar_dublagem(video_path: str, segmentos_pt: list, pasta_saida: str,
                   voz: str = None, ffmpeg_path: str = None) -> str | None:
    """
    Gera uma versão dublada do vídeo em PT-BR.
    
    Args:
        video_path: Caminho do vídeo original
        segmentos_pt: Lista de dicts com 'text', 'start', 'duration' (transcrição PT)
        pasta_saida: Pasta para salvar o vídeo dublado
        voz: 'feminina' ou 'masculina'
        ffmpeg_path: Caminho do executável ffmpeg
    
    Returns:
        Caminho do vídeo dublado ou None se falhar
    """
    if voz is None:
        voz = VOZ_PADRAO
    
    voz_id = VOZES.get(voz, VOZES[VOZ_PADRAO])
    
    if ffmpeg_path is None:
        ffmpeg_path = encontrar_ffmpeg()
        if ffmpeg_path:
            ffmpeg_path = os.path.join(ffmpeg_path, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        else:
            ffmpeg_path = "ffmpeg"
    
    print(f"\n  Gerando dublagem PT-BR (voz: {voz} — {voz_id})...")
    print(f"    {len(segmentos_pt)} segmentos de texto")
    
    pasta_temp = tempfile.mkdtemp(prefix="dubbing_")
    
    try:
        # 1. Gerar áudio TTS sincronizado
        print("    Gerando áudio TTS por segmento...")
        audio_dublagem = asyncio.run(
            _gerar_audio_completo(segmentos_pt, voz_id, pasta_temp, ffmpeg_path)
        )
        
        # 2. Render final (padrão: substituir totalmente o áudio original)
        nome_base = os.path.splitext(os.path.basename(video_path))[0]
        # Remover sufixo de resolução para o nome
        nome_limpo = nome_base.replace("_audio_melhorado", "")
        video_dublado = os.path.join(pasta_saida, f"{nome_limpo}_dublado_PT.mp4")
        
        print("    Substituindo áudio original por dublagem PT...")
        cmd_final = [
            ffmpeg_path, "-y",
            "-i", video_path,         # Vídeo original
            "-i", audio_dublagem,     # Áudio TTS
            "-filter_complex", "[1:a]apad[aout]",
            "-map", "0:v:0",          # Vídeo do input 0
            "-map", "[aout]",         # Áudio dublado (com pad para cobrir o vídeo)
            "-c:v", "copy",           # Copiar vídeo sem recodificar
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            video_dublado
        ]
        
        result = subprocess.run(cmd_final, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(video_dublado):
            tamanho = os.path.getsize(video_dublado) / (1024 * 1024)
            print(f"   [OK] Vídeo dublado salvo: {video_dublado} ({tamanho:.1f} MB)")
            return video_dublado
        else:
            print(f"   [X] Erro no mix final: {result.stderr[:500] if result.stderr else 'desconhecido'}")
            return None
            
    except Exception as e:
        print(f"   [X] Erro na dublagem: {e}")
        return None
    finally:
        # Limpar pasta temporária
        try:
            shutil.rmtree(pasta_temp, ignore_errors=True)
        except Exception:
            pass
