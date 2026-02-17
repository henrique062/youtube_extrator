import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import telegram_bot


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)


class TelegramBotJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_executar_job_sem_arquivo_nao_envia_concluido(self):
        with tempfile.TemporaryDirectory() as pasta:
            resultado = telegram_bot.ResultadoProcessamento(
                titulo="Video Sem Arquivo",
                pasta_video=pasta,
            )
            bot = FakeBot()
            app = SimpleNamespace(bot=bot, bot_data={"jobs": {10: object()}})

            with patch(
                "telegram_bot._processar_video_com_opcoes",
                new=AsyncMock(return_value=resultado),
            ), patch(
                "telegram_bot.get_ultimo_erro_download",
                return_value="erro de teste",
            ), patch(
                "telegram_bot._enviar_video_ao_chat",
                new=AsyncMock(),
            ) as enviar_mock:
                await telegram_bot._executar_job(
                    app,
                    chat_id=1,
                    user_id=10,
                    url="https://youtube.com/watch?v=abc123def45",
                    opcoes={"download_1080": True},
                )

        self.assertTrue(any("Processo finalizado, mas não encontrei arquivo" in m for m in bot.messages))
        self.assertFalse(any(m.startswith("Concluído.") for m in bot.messages))
        enviar_mock.assert_not_called()
        self.assertNotIn(10, app.bot_data["jobs"])

    async def test_executar_job_com_arquivo_envia_concluido(self):
        with tempfile.TemporaryDirectory() as pasta:
            video_path = os.path.join(pasta, "teste_720p.mp4")
            with open(video_path, "wb") as f:
                f.write(b"video")

            resultado = telegram_bot.ResultadoProcessamento(
                titulo="Video OK",
                pasta_video=pasta,
                video_720=video_path,
            )
            bot = FakeBot()
            app = SimpleNamespace(bot=bot, bot_data={"jobs": {11: object()}})

            with patch(
                "telegram_bot._processar_video_com_opcoes",
                new=AsyncMock(return_value=resultado),
            ), patch(
                "telegram_bot._enviar_video_ao_chat",
                new=AsyncMock(),
            ) as enviar_mock:
                await telegram_bot._executar_job(
                    app,
                    chat_id=1,
                    user_id=11,
                    url="https://youtube.com/watch?v=abc123def45",
                    opcoes={"download_720": True},
                )

        self.assertTrue(any(m.startswith("Concluído.") for m in bot.messages))
        enviar_mock.assert_awaited_once()
        self.assertNotIn(11, app.bot_data["jobs"])


if __name__ == "__main__":
    unittest.main()
