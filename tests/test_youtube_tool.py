import os
import tempfile
import unittest
from unittest.mock import patch

import youtube_tool


class YoutubeToolFallbackTests(unittest.TestCase):
    def setUp(self):
        youtube_tool.ULTIMO_ERRO_DOWNLOAD = ""

    def test_obter_titulo_video_faz_fallback_sem_cookies(self):
        class FakeYoutubeDL:
            calls = []

            def __init__(self, opts):
                self.opts = opts
                FakeYoutubeDL.calls.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                if self.opts.get("cookiefile"):
                    raise Exception("Requested format is not available")
                return {"title": "Titulo sem cookies", "formats": [{"height": 720}]}

        with patch.object(youtube_tool.yt_dlp, "YoutubeDL", FakeYoutubeDL), patch(
            "youtube_tool._tentativas_ytdlp",
            return_value=[("com cookies", {"cookiefile": "/tmp/cookies.txt"}), ("sem cookies", {})],
        ):
            titulo = youtube_tool.obter_titulo_video("https://youtube.com/watch?v=abc123def45")

        self.assertEqual(titulo, "Titulo sem cookies")
        self.assertTrue(any(call.get("cookiefile") for call in FakeYoutubeDL.calls))
        self.assertTrue(any(not call.get("cookiefile") for call in FakeYoutubeDL.calls))

    def test_baixar_video_faz_fallback_sem_cookies_e_salva_arquivo(self):
        class FakeYoutubeDL:
            calls = []

            def __init__(self, opts):
                self.opts = opts
                FakeYoutubeDL.calls.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                if self.opts.get("cookiefile"):
                    raise Exception("Requested format is not available")
                return {"formats": [{"height": 360}, {"height": 720}]}

            def download(self, urls):
                if self.opts.get("cookiefile"):
                    raise Exception("Requested format is not available")
                outtmpl = self.opts["outtmpl"]
                arquivo_final = outtmpl.replace("%(ext)s", "mp4")
                os.makedirs(os.path.dirname(arquivo_final), exist_ok=True)
                with open(arquivo_final, "wb") as f:
                    f.write(b"video")

        with tempfile.TemporaryDirectory() as pasta_video, patch.object(
            youtube_tool.yt_dlp, "YoutubeDL", FakeYoutubeDL
        ), patch(
            "youtube_tool._tentativas_ytdlp",
            return_value=[("com cookies", {"cookiefile": "/tmp/cookies.txt"}), ("sem cookies", {})],
        ):
            ok = youtube_tool.baixar_video(
                "https://youtube.com/watch?v=abc123def45",
                "Video Teste",
                "720",
                pasta_video,
            )
            arquivo_esperado = os.path.join(pasta_video, "Video Teste_720p.mp4")
            self.assertTrue(os.path.exists(arquivo_esperado))

        self.assertTrue(ok)
        self.assertEqual(youtube_tool.get_ultimo_erro_download(), "")
        self.assertTrue(any(call.get("cookiefile") for call in FakeYoutubeDL.calls))
        self.assertTrue(any(("outtmpl" in call) and (not call.get("cookiefile")) for call in FakeYoutubeDL.calls))

    def test_baixar_video_preenche_ultimo_erro_quando_todas_tentativas_falham(self):
        class FailYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                raise Exception("Falha total")

            def download(self, urls):
                raise Exception("Falha total")

        with tempfile.TemporaryDirectory() as pasta_video, patch.object(
            youtube_tool.yt_dlp, "YoutubeDL", FailYoutubeDL
        ), patch(
            "youtube_tool._tentativas_ytdlp",
            return_value=[("com cookies", {"cookiefile": "/tmp/cookies.txt"}), ("sem cookies", {})],
        ):
            ok = youtube_tool.baixar_video(
                "https://youtube.com/watch?v=abc123def45",
                "Video Teste",
                "1080",
                pasta_video,
            )

        self.assertFalse(ok)
        self.assertIn("Falha total", youtube_tool.get_ultimo_erro_download())


if __name__ == "__main__":
    unittest.main()
