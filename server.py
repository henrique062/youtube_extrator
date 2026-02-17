#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Tool — Servidor Web (Flask)
Interface web para transcrição, download, melhoria de áudio e dublagem.
"""

import json
import os
import threading
import uuid
from datetime import datetime

from flask import Flask, render_template, jsonify, request, send_from_directory

# Importar funções do script principal
from youtube_tool import (
    extrair_video_id, sanitizar_nome, obter_titulo_video,
    criar_pasta_video, obter_transcricao, baixar_video,
    DOWNLOAD_DIR, FFMPEG_LOCATION, RESOLUTIONS
)
from audio_enhancer import melhorar_audio
from dubbing import gerar_dublagem

app = Flask(__name__)
app.config["SECRET_KEY"] = "youtube-tool-secret"

# Armazenar status dos processamentos
tarefas = {}


@app.route("/")
def index():
    """Página principal."""
    return render_template("index.html")


@app.route("/api/process", methods=["POST"])
def api_processar():
    """Inicia o processamento de um vídeo."""
    data = request.json
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL não fornecida"}), 400

    video_id = extrair_video_id(url)
    if not video_id:
        return jsonify({"error": "URL inválida do YouTube"}), 400

    opcoes = {
        "download_720": data.get("download_720", True),
        "download_1080": data.get("download_1080", True),
        "transcricao": data.get("transcricao", True),
        "traducao_pt": data.get("traducao_pt", True),
        "melhoria_audio": data.get("melhoria_audio", True),
        "dublagem_pt": data.get("dublagem_pt", True),
    }

    tarefa_id = str(uuid.uuid4())[:8]
    tarefas[tarefa_id] = {
        "id": tarefa_id,
        "url": url,
        "video_id": video_id,
        "status": "iniciando",
        "etapa": "Preparando...",
        "progresso": 0,
        "titulo": "",
        "pasta": "",
        "arquivos": [],
        "erros": [],
        "concluido": False,
    }

    # Processar em background
    thread = threading.Thread(target=_processar_background, args=(tarefa_id, url, video_id, opcoes))
    thread.daemon = True
    thread.start()

    return jsonify({"tarefa_id": tarefa_id})


def _processar_background(tarefa_id: str, url: str, video_id: str, opcoes: dict):
    """Processa o vídeo em background."""
    tarefa = tarefas[tarefa_id]
    total_etapas = sum([
        opcoes.get("transcricao", False),
        opcoes.get("download_720", False),
        opcoes.get("download_1080", False),
        opcoes.get("melhoria_audio", False),
        opcoes.get("dublagem_pt", False),
    ])
    etapa_atual = 0

    try:
        # 1. Obter título
        tarefa["etapa"] = "Obtendo informações do vídeo..."
        tarefa["status"] = "processando"
        try:
            titulo = obter_titulo_video(url)
        except Exception:
            titulo = video_id
        tarefa["titulo"] = titulo

        # 2. Criar pasta
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        pasta_video = criar_pasta_video(titulo)
        tarefa["pasta"] = pasta_video

        # 3. Transcrição
        segmentos_pt = None
        if opcoes.get("transcricao"):
            etapa_atual += 1
            tarefa["etapa"] = f"({etapa_atual}/{total_etapas}) Buscando transcrição..."
            tarefa["progresso"] = int((etapa_atual / total_etapas) * 100)

            resultado = obter_transcricao(video_id, titulo, pasta_video)
            if resultado and resultado.get("sucesso"):
                nome = sanitizar_nome(titulo)
                tarefa["arquivos"].append({
                    "nome": f"{nome}_transcricao_original.txt",
                    "tipo": "transcricao",
                })
                segmentos_pt = resultado.get("segmentos_pt")
                if segmentos_pt:
                    tarefa["arquivos"].append({
                        "nome": f"{nome}_transcricao_PT.txt",
                        "tipo": "transcricao",
                    })

        # 4. Download 720p
        if opcoes.get("download_720"):
            etapa_atual += 1
            tarefa["etapa"] = f"({etapa_atual}/{total_etapas}) Baixando 720p..."
            tarefa["progresso"] = int((etapa_atual / total_etapas) * 100)
            if baixar_video(url, titulo, "720", pasta_video):
                nome = sanitizar_nome(titulo)
                tarefa["arquivos"].append({
                    "nome": f"{nome}_720p.mp4",
                    "tipo": "video",
                })

        # 5. Download 1080p
        if opcoes.get("download_1080"):
            etapa_atual += 1
            tarefa["etapa"] = f"({etapa_atual}/{total_etapas}) Baixando 1080p..."
            tarefa["progresso"] = int((etapa_atual / total_etapas) * 100)
            if baixar_video(url, titulo, "1080", pasta_video):
                nome = sanitizar_nome(titulo)
                tarefa["arquivos"].append({
                    "nome": f"{nome}_1080p.mp4",
                    "tipo": "video",
                })

        # 6. Melhoria de áudio
        video_melhorado = None
        if opcoes.get("melhoria_audio"):
            etapa_atual += 1
            tarefa["etapa"] = f"({etapa_atual}/{total_etapas}) Melhorando áudio..."
            tarefa["progresso"] = int((etapa_atual / total_etapas) * 100)
            nome = sanitizar_nome(titulo)
            video_1080 = os.path.join(pasta_video, f"{nome}_1080p.mp4")
            if os.path.exists(video_1080):
                video_melhorado = melhorar_audio(video_1080, pasta_video, FFMPEG_LOCATION)
                if video_melhorado:
                    tarefa["arquivos"].append({
                        "nome": os.path.basename(video_melhorado),
                        "tipo": "video",
                    })

        # 7. Dublagem
        if opcoes.get("dublagem_pt"):
            etapa_atual += 1
            tarefa["etapa"] = f"({etapa_atual}/{total_etapas}) Gerando dublagem PT..."
            tarefa["progresso"] = int((etapa_atual / total_etapas) * 100)
            if segmentos_pt:
                video_base = video_melhorado or os.path.join(pasta_video, f"{sanitizar_nome(titulo)}_1080p.mp4")
                if os.path.exists(video_base):
                    video_dublado = gerar_dublagem(video_base, segmentos_pt, pasta_video)
                    if video_dublado:
                        tarefa["arquivos"].append({
                            "nome": os.path.basename(video_dublado),
                            "tipo": "video",
                        })

        tarefa["etapa"] = "Concluído!"
        tarefa["progresso"] = 100
        tarefa["status"] = "concluido"
        tarefa["concluido"] = True

    except Exception as e:
        tarefa["status"] = "erro"
        tarefa["etapa"] = f"Erro: {str(e)}"
        tarefa["erros"].append(str(e))


@app.route("/api/status/<tarefa_id>")
def api_status(tarefa_id):
    """Retorna o status de uma tarefa."""
    tarefa = tarefas.get(tarefa_id)
    if not tarefa:
        return jsonify({"error": "Tarefa não encontrada"}), 404
    return jsonify(tarefa)


@app.route("/api/downloads")
def api_downloads():
    """Lista todas as pastas de download."""
    if not os.path.exists(DOWNLOAD_DIR):
        return jsonify([])

    pastas = []
    for nome in sorted(os.listdir(DOWNLOAD_DIR), reverse=True):
        caminho = os.path.join(DOWNLOAD_DIR, nome)
        if os.path.isdir(caminho):
            arquivos = []
            for arq in os.listdir(caminho):
                arq_path = os.path.join(caminho, arq)
                if os.path.isfile(arq_path):
                    tamanho = os.path.getsize(arq_path)
                    arquivos.append({
                        "nome": arq,
                        "tamanho": tamanho,
                        "tamanho_fmt": _formatar_tamanho(tamanho),
                    })
            pastas.append({
                "nome": nome,
                "arquivos": arquivos,
            })

    return jsonify(pastas)


@app.route("/downloads/<path:filepath>")
def servir_download(filepath):
    """Serve um arquivo de download."""
    return send_from_directory(DOWNLOAD_DIR, filepath, as_attachment=True)


def _formatar_tamanho(bytes_val):
    """Formata bytes para leitura humana."""
    for unidade in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unidade}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    print("=" * 50)
    print("   YouTube Tool - Interface Web")
    print(f"   http://localhost:{port}")
    print("=" * 50)
    app.run(debug=debug, host="0.0.0.0", port=port)
