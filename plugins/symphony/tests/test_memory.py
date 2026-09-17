import unittest

from plugins.symphony.symphony.memory import redact_secrets


class RedactionTests(unittest.TestCase):
    def test_credential_shaped_text_is_redacted(self):
        for text in (
            "api_key=sk-live-abcdef123456",
            "password: hunter2",
            "Authorization: Bearer abcdefghijklmnop",
            "-----BEGIN RSA PRIVATE KEY-----body-----END RSA PRIVATE KEY-----",
        ):
            with self.subTest(text=text):
                self.assertIn("[REDACTED]", redact_secrets(text))

    def test_ordinary_prose_is_left_alone(self):
        text = "Ship the release once the smoke passes."
        self.assertEqual(text, redact_secrets(text))


if __name__ == "__main__":
    unittest.main()
