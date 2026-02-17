#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Tool — Transcrição, Download, Melhoria de Áudio e Dublagem
Sem necessidade de API keys.

Dependências:
  pip install yt-dlp youtube-transcript-api edge-tts flask deep-translator

Requisito do sistema:
  FFmpeg instalado (para mesclar vídeo+áudio em 720p/1080p)
"""

import os
import re
import socket
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

from audio_enhancer import melhorar_audio
from dubbing import gerar_dublagem


# ──────────────────────────────────────────────
# Configurações
# ──────────────────────────────────────────────
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
RESOLUTIONS = ["720", "1080"]
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")

# Caminho do arquivo de cookies (formato Netscape)
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
ULTIMO_ERRO_DOWNLOAD = ""


def _cookiefile_runtime() -> str | None:
    """Copia cookies para /tmp para evitar erro de escrita em volume read-only."""
    try:
        if not os.path.isfile(COOKIES_FILE):
            return None
        destino = "/tmp/ytdlp_cookies.txt"
        with open(COOKIES_FILE, "r", encoding="utf-8", errors="ignore") as src, open(destino, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        return destino
    except Exception:
        return None


def _opcoes_base_ytdlp() -> dict:
    """Retorna opções base do yt-dlp com cookies e configurações anti-bloqueio."""
    opts = {
        "extractor_args": {"youtube": {"player_client": ["web", "web_safari", "tv"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        },
    }
    cookie_runtime = _cookiefile_runtime()
    if cookie_runtime:
        opts["cookiefile"] = cookie_runtime
    return opts

# Caminho do FFmpeg instalado via winget (caso não esteja no PATH)
FFMPEG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Microsoft", "WinGet", "Packages",
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
    "ffmpeg-8.0.1-full_build", "bin"
)


def _encontrar_ffmpeg() -> str | None:
    """Retorna o diretório do FFmpeg se encontrado."""
    # 1. Verificar se já está no PATH
    import shutil
    if shutil.which("ffmpeg"):
        return None  # Está no PATH, não precisa especificar

    # 2. Verificar caminho do winget
    ffmpeg_exe = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if os.path.exists(ffmpeg_exe):
        return FFMPEG_DIR

    # 3. Buscar em locais comuns
    locais_comuns = [
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin"),
    ]
    for local in locais_comuns:
        if os.path.exists(os.path.join(local, "ffmpeg.exe")):
            return local

    return None


FFMPEG_LOCATION = _encontrar_ffmpeg()


# ──────────────────────────────────────────────
# Utilitários
# ──────────────────────────────────────────────
def _campos_snippet(snippet):
    """Extrai texto/início/duração de snippets (objeto ou dict)."""
    if isinstance(snippet, dict):
        texto = snippet.get("text", "")
        inicio = float(snippet.get("start", 0.0))
        duracao = float(snippet.get("duration", 0.0))
    else:
        texto = getattr(snippet, "text", "")
        inicio = float(getattr(snippet, "start", 0.0))
        duracao = float(getattr(snippet, "duration", 0.0))

    return texto, max(inicio, 0.0), max(duracao, 0.0)


def extrair_video_id(url: str) -> str | None:
    """Extrai o ID do vídeo a partir de diversos formatos de URL do YouTube."""
    parsed = urlparse(url.strip())

    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            return qs.get("v", [None])[0]
        match = re.match(r"/(embed|shorts|v)/([A-Za-z0-9_-]{11})", parsed.path)
        if match:
            return match.group(2)
    elif parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/")[:11]

    # Tentativa genérica: procurar 11 caracteres válidos
    match = re.search(r"[A-Za-z0-9_-]{11}", url)
    return match.group(0) if match else None


def sanitizar_nome(nome: str) -> str:
    """Remove caracteres inválidos para nomes de arquivo."""
    nome = re.sub(r'[\\/*?:"<>|]', "", nome)
    nome = nome.strip(". ")
    return nome[:150] if nome else "video"


def _escolher_maior_arquivo(candidatos: list[str]) -> str | None:
    """Retorna o maior arquivo existente da lista."""
    validos = [c for c in candidatos if c and os.path.exists(c)]
    if not validos:
        return None
    return max(validos, key=lambda p: (os.path.getsize(p), os.path.getmtime(p)))


def _encontrar_video_baixado(pasta_video: str, nome_base: str, resolucao: str) -> str | None:
    """Encontra arquivo final de vídeo para uma resolução, com fallback por extensão/nome."""
    prefixo = f"{nome_base}_{resolucao}p"

    candidatos_exatos = [
        os.path.join(pasta_video, f"{prefixo}{ext}")
        for ext in VIDEO_EXTENSIONS
    ]
    achado = _escolher_maior_arquivo(candidatos_exatos)
    if achado:
        return achado

    try:
        arquivos = os.listdir(pasta_video)
    except OSError:
        return None

    candidatos_prefixo = []
    for f in arquivos:
        caminho = os.path.join(pasta_video, f)
        if not os.path.isfile(caminho):
            continue
        nome_lower = f.lower()
        ext = os.path.splitext(nome_lower)[1]
        if ext not in VIDEO_EXTENSIONS:
            continue
        if nome_lower.startswith(prefixo.lower()):
            candidatos_prefixo.append(caminho)

    achado = _escolher_maior_arquivo(candidatos_prefixo)
    if achado:
        return achado

    candidatos_por_tag = []
    tag = f"_{resolucao}p"
    for f in arquivos:
        caminho = os.path.join(pasta_video, f)
        if not os.path.isfile(caminho):
            continue
        nome_lower = f.lower()
        ext = os.path.splitext(nome_lower)[1]
        if ext not in VIDEO_EXTENSIONS:
            continue
        if tag in nome_lower:
            candidatos_por_tag.append(caminho)

    return _escolher_maior_arquivo(candidatos_por_tag)


def criar_pasta_video(titulo: str) -> str:
    """Cria uma subpasta com formato DD-MM-AA (Titulo) e retorna o caminho."""
    data_hoje = datetime.now().strftime("%d-%m-%y")
    nome_pasta = f"{data_hoje} {sanitizar_nome(titulo)}"
    caminho = os.path.join(DOWNLOAD_DIR, nome_pasta)
    os.makedirs(caminho, exist_ok=True)
    return caminho


def obter_titulo_video(url: str) -> str:
    """Obtém o título do vídeo usando yt-dlp sem baixar."""
    opts = {**_opcoes_base_ytdlp(), "quiet": True, "no_warnings": True, "skip_download": True}
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", "video")


# ──────────────────────────────────────────────
# Transcrição (API v1.2.4+) + Tradução para PT
# ──────────────────────────────────────────────
def _salvar_transcricao(pasta_video: str, titulo: str, sufixo: str, idioma_label: str, fetched) -> str:
    """Salva uma transcrição em arquivo .txt. Retorna o caminho do arquivo."""
    nome_arquivo = sanitizar_nome(titulo)
    caminho = os.path.join(pasta_video, f"{nome_arquivo}_{sufixo}.txt")

    with open(caminho, "w", encoding="utf-8") as f:
        f.write(f"Transcrição: {titulo}\n")
        f.write(f"Idioma: {idioma_label}\n")
        f.write("=" * 60 + "\n\n")
        for snippet in fetched:
            texto, inicio, duracao = _campos_snippet(snippet)
            fim = inicio + duracao
            f.write(f"[{inicio:.2f}s - {fim:.2f}s] {texto}\n")

    tamanho = os.path.getsize(caminho)
    print(f"    Salvo: {caminho} ({tamanho / 1024:.1f} KB)")
    return caminho


def _traduzir_segmentos_localmente_para_pt(fetched) -> list | None:
    """Traduz segmentos para PT-BR preservando os timestamps originais."""
    try:
        from deep_translator import GoogleTranslator
    except Exception:
        print("     Tradução local indisponível: instale 'deep-translator'.")
        return None

    print("     Usando tradução local com timestamps originais...")
    translator = GoogleTranslator(source="auto", target="pt")
    segmentos_pt = []
    timeout_padrao = socket.getdefaulttimeout()
    socket.setdefaulttimeout(20)

    try:
        for i, snippet in enumerate(fetched):
            texto, inicio, duracao = _campos_snippet(snippet)
            if not texto.strip():
                continue

            try:
                texto_pt = translator.translate(texto) or texto
            except Exception as e:
                print(f"       [!] Falha no segmento {i}, mantendo original: {e}")
                texto_pt = texto

            segmentos_pt.append({
                "text": texto_pt,
                "start": inicio,
                "duration": duracao,
            })

            if (i + 1) % 20 == 0:
                print(f"       Traduzidos localmente: {i + 1}")
    finally:
        socket.setdefaulttimeout(timeout_padrao)

    return segmentos_pt if segmentos_pt else None


def obter_transcricao(video_id: str, titulo: str, pasta_video: str) -> dict | None:
    """Busca a transcrição, salva .txt, traduz para PT. Retorna dados para dublagem."""
    print("\n Buscando transcrição...")

    idiomas_preferidos = ["pt", "pt-BR", "en", "en-US"]
    ytt_api = YouTubeTranscriptApi()
    resultado = {"sucesso": False, "segmentos_pt": None}

    try:
        transcript_list = ytt_api.list(video_id)

        transcript = None
        idioma_encontrado = ""
        is_portuguese = False

        try:
            transcript = transcript_list.find_transcript(idiomas_preferidos)
            idioma_encontrado = f"{transcript.language} ({transcript.language_code})"
            is_portuguese = transcript.language_code.startswith("pt")
            print(f"   [OK] Transcrição encontrada: {idioma_encontrado}")
        except Exception:
            try:
                transcript = transcript_list.find_generated_transcript(idiomas_preferidos)
                idioma_encontrado = f"{transcript.language} ({transcript.language_code}) [auto-gerada]"
                is_portuguese = transcript.language_code.startswith("pt")
                print(f"   [OK] Transcrição gerada: {idioma_encontrado}")
            except Exception:
                for t in transcript_list:
                    transcript = t
                    idioma_encontrado = f"{t.language} ({t.language_code})"
                    is_portuguese = t.language_code.startswith("pt")
                    print(f"   [OK] Transcrição disponível: {idioma_encontrado}")
                    break

        if transcript is None:
            print("   [X] Nenhuma transcrição disponível para este vídeo.")
            return resultado

        # 1. Salvar transcrição original
        fetched = transcript.fetch()
        fetched_items = list(fetched)
        _salvar_transcricao(pasta_video, titulo, "transcricao_original", idioma_encontrado, fetched_items)
        resultado["sucesso"] = True

        # 2. Traduzir para português (se não for PT)
        if not is_portuguese:
            print("\n Traduzindo transcrição para Português...")
            fetched_pt = None
            try:
                if transcript.is_translatable:
                    translated = transcript.translate("pt")
                    fetched_pt = list(translated.fetch())
                    _salvar_transcricao(
                        pasta_video,
                        titulo,
                        "transcricao_PT",
                        "Português (pt) [traduzido via YouTube]",
                        fetched_pt
                    )
                    print("   [OK] Tradução para português concluída!")
                    # Guardar segmentos PT para dublagem
                    resultado["segmentos_pt"] = [
                        {"text": s.text, "start": s.start, "duration": s.duration}
                        for s in fetched_pt
                    ]
                else:
                    print("     Esta transcrição não suporta tradução automática do YouTube.")
            except Exception as e:
                print(f"     Não foi possível traduzir para português: {e}")

            # Fallback para vídeos/idiomas sem suporte de tradução no YouTube
            if not resultado.get("segmentos_pt"):
                segmentos_local = _traduzir_segmentos_localmente_para_pt(fetched_items)
                if segmentos_local:
                    _salvar_transcricao(
                        pasta_video,
                        titulo,
                        "transcricao_PT",
                        "Português (pt-BR) [traduzido localmente]",
                        segmentos_local
                    )
                    resultado["segmentos_pt"] = segmentos_local
                    print("   [OK] Tradução local para português concluída!")
                else:
                    print("   [X] Não foi possível gerar tradução PT para dublagem.")
        else:
            print("   [PT-BR] Transcrição já está em português! (dublagem não necessária)")

        return resultado

    except Exception as e:
        # Fallback: tentar fetch direto com idiomas
        try:
            print(f"     list() falhou ({e}), tentando fetch direto...")
            fetched = ytt_api.fetch(video_id, languages=idiomas_preferidos)
            fetched_items = list(fetched)
            _salvar_transcricao(pasta_video, titulo, "transcricao_original",
                                f"{fetched.language} ({fetched.language_code})", fetched_items)
            resultado["sucesso"] = True

            if not fetched.language_code.startswith("pt"):
                segmentos_local = _traduzir_segmentos_localmente_para_pt(fetched_items)
                if segmentos_local:
                    _salvar_transcricao(
                        pasta_video,
                        titulo,
                        "transcricao_PT",
                        "Português (pt-BR) [traduzido localmente]",
                        segmentos_local
                    )
                    resultado["segmentos_pt"] = segmentos_local

            return resultado
        except Exception as e2:
            print(f"   [X] Erro ao buscar transcrição: {e2}")
            return resultado


# ──────────────────────────────────────────────
# Download de Vídeo
# ──────────────────────────────────────────────
def get_ultimo_erro_download() -> str:
    return ULTIMO_ERRO_DOWNLOAD


def baixar_video(url: str, titulo: str, resolucao: str, pasta_video: str) -> bool:
    """Baixa o vídeo na resolução especificada. Retorna True se bem-sucedido."""
    global ULTIMO_ERRO_DOWNLOAD
    ULTIMO_ERRO_DOWNLOAD = ""
    print(f"\n Baixando vídeo em {resolucao}p...")

    nome_arquivo = sanitizar_nome(titulo)
    caminho_saida = os.path.join(pasta_video, f"{nome_arquivo}_{resolucao}p.%(ext)s")

    opts = {
        **_opcoes_base_ytdlp(),
        # Seleciona o melhor vídeo até a resolução + melhor áudio, com fallbacks amplos
        "format": f"bestvideo[height<={resolucao}][ext=mp4]+bestaudio[ext=m4a]/"
                  f"bestvideo[height<={resolucao}]+bestaudio/"
                  f"best[height<={resolucao}]/"
                  f"bestvideo+bestaudio/"
                  f"best",
        "outtmpl": caminho_saida,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [_hook_progresso],
    }

    # Adicionar caminho do FFmpeg se necessário
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # Verificar se o arquivo final foi realmente gerado
        arquivo_final = _encontrar_video_baixado(pasta_video, nome_arquivo, resolucao)
        if arquivo_final:
            tamanho = os.path.getsize(arquivo_final) / (1024 * 1024)
            print(f"   [OK] Download {resolucao}p concluído: {arquivo_final} ({tamanho:.1f} MB)")
            return True

        ULTIMO_ERRO_DOWNLOAD = f"Download {resolucao}p terminou sem arquivo final"
        print(f"   [X] Download {resolucao}p terminou, mas o arquivo final não foi encontrado.")
        return False

    except Exception as e:
        ULTIMO_ERRO_DOWNLOAD = str(e)
        print(f"   [X] Erro no download {resolucao}p: {e}")
        return False


def _hook_progresso(d):
    """Callback de progresso do download."""
    if d["status"] == "downloading":
        percentual = d.get("_percent_str", "N/A")
        velocidade = d.get("_speed_str", "N/A")
        eta = d.get("_eta_str", "N/A")
        print(f"\r   ⬇  {percentual} | Velocidade: {velocidade} | ETA: {eta}   ", end="", flush=True)
    elif d["status"] == "finished":
        print(f"\n   [?] Mesclando vídeo e áudio...")


# ──────────────────────────────────────────────
# Fluxo Principal
# ──────────────────────────────────────────────
def processar_video(url: str):
    """Fluxo completo: transcrição + download + melhoria de áudio + dublagem."""
    print("\n" + "=" * 60)
    print(" YouTube Tool — Transcrição, Download, Áudio & Dublagem")
    print("=" * 60)

    # 1. Extrair Video ID
    video_id = extrair_video_id(url)
    if not video_id:
        print("[X] Não foi possível extrair o ID do vídeo desta URL.")
        return

    print(f"\n URL: {url}")
    print(f" Video ID: {video_id}")

    # 2. Criar pasta de downloads
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f" Pasta de downloads: {DOWNLOAD_DIR}")

    # FFmpeg status
    if FFMPEG_LOCATION:
        print(f" FFmpeg: {FFMPEG_LOCATION}")
    else:
        print(" FFmpeg: encontrado no PATH do sistema")

    # 3. Obter título do vídeo
    print("\n Obtendo informações do vídeo...")
    try:
        titulo = obter_titulo_video(url)
        print(f" Título: {titulo}")
    except Exception as e:
        print(f"  Não foi possível obter o título: {e}")
        titulo = video_id

    # 4. Criar subpasta DD-MM-AA Titulo
    pasta_video = criar_pasta_video(titulo)
    print(f" Pasta do vídeo: {pasta_video}")

    # 5. Transcrição + Tradução
    resultado_transcricao = obter_transcricao(video_id, titulo, pasta_video)
    sucesso_transcricao = resultado_transcricao and resultado_transcricao.get("sucesso", False)
    segmentos_pt = resultado_transcricao.get("segmentos_pt") if resultado_transcricao else None

    # 6. Downloads
    resultados = {}
    for res in RESOLUTIONS:
        resultados[res] = baixar_video(url, titulo, res, pasta_video)

    # 7. Melhoria de Áudio (usar o 1080p como base)
    nome_arquivo = sanitizar_nome(titulo)
    video_1080 = os.path.join(pasta_video, f"{nome_arquivo}_1080p.mp4")
    sucesso_audio = False
    video_melhorado = None

    if os.path.exists(video_1080):
        video_melhorado = melhorar_audio(video_1080, pasta_video, FFMPEG_LOCATION)
        sucesso_audio = video_melhorado is not None
    else:
        print("\n  Vídeo 1080p não encontrado, pulando melhoria de áudio.")

    # 8. Dublagem PT-BR
    sucesso_dublagem = False
    if segmentos_pt and video_melhorado:
        video_dublado = gerar_dublagem(video_melhorado, segmentos_pt, pasta_video)
        sucesso_dublagem = video_dublado is not None
    elif segmentos_pt and os.path.exists(video_1080):
        video_dublado = gerar_dublagem(video_1080, segmentos_pt, pasta_video)
        sucesso_dublagem = video_dublado is not None
    else:
        print("\n  Sem transcrição PT disponível, pulando dublagem.")

    # 9. Resumo final
    print("\n" + "=" * 60)
    print(" RESUMO")
    print("=" * 60)
    print(f"   Transcrição:      {'[OK] Salva' if sucesso_transcricao else '[X] Não disponível'}")
    for res in RESOLUTIONS:
        status = "[OK] Baixado" if resultados.get(res) else "[X] Falhou"
        print(f"   {res}p:            {status}")
    print(f"   Áudio melhorado:  {'[OK] Pronto' if sucesso_audio else '[X] Não processado'}")
    print(f"    Dublagem PT:      {'[OK] Pronta' if sucesso_dublagem else '[X] Não processada'}")
    print(f"   Pasta:            {pasta_video}")
    print("=" * 60)


def main():
    """Ponto de entrada do script."""
    print("=" * 50)
    print("   YouTube Tool - Transcricao & Download")
    print("   Sem necessidade de API")
    print("=" * 50)

    if len(sys.argv) > 1:
        # URL passada como argumento
        url = sys.argv[1]
    else:
        # Solicitar URL interativamente
        print("\nCole a URL do vídeo do YouTube abaixo:")
        url = input("> URL: ").strip()

    if not url:
        print("[X] Nenhuma URL fornecida. Saindo.")
        sys.exit(1)

    processar_video(url)

    # Perguntar se quer processar outro vídeo
    while True:
        print("\n" + "-" * 40)
        resposta = input("[?] Deseja processar outro vídeo? (s/n): ").strip().lower()
        if resposta in ("s", "sim", "y", "yes"):
            url = input("> URL: ").strip()
            if url:
                processar_video(url)
            else:
                print("[X] URL vazia.")
        else:
            print("\n Até mais! Os arquivos estão em:", DOWNLOAD_DIR)
            break


if __name__ == "__main__":
    main()
