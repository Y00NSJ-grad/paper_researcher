import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radar.cli import load_local_env
from radar.config import Settings


class CliEnvironmentTest(unittest.TestCase):
    def test_local_env_is_loaded_without_overriding_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=file-openai-key\n"
                "SLACK_WEBHOOK_URL=https://hooks.slack.test/file-secret\n",
                encoding="utf-8",
            )
            clean = {
                key: value
                for key, value in os.environ.items()
                if key not in {"OPENAI_API_KEY", "SLACK_WEBHOOK_URL"}
            }
            clean["OPENAI_API_KEY"] = "process-openai-key"

            with patch.dict(os.environ, clean, clear=True):
                load_local_env(env_path)
                settings = Settings.from_env()

            self.assertEqual(settings.openai_api_key, "process-openai-key")
            self.assertEqual(
                settings.slack_webhook_url,
                "https://hooks.slack.test/file-secret",
            )


if __name__ == "__main__":
    unittest.main()
