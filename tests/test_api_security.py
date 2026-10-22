"""Regression tests for authentication, client isolation, limits, and local storage."""

import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend import storage
from backend.security import api_keys


class PrivateApiSecurityTests(unittest.TestCase):
    def setUp(self):
        api_keys._rate_buckets.clear()
        api_keys._daily_buckets.clear()
        self.raw_key = "test-client-key-not-for-production"
        digest = hashlib.sha256(self.raw_key.encode("utf-8")).hexdigest()
        self.env = patch.dict(os.environ, {
            "API_CLIENT_KEY_HASHES": f"test-client:{digest}",
            "RATE_LIMIT_REQUESTS_PER_MINUTE": "60",
        })
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        api_keys._rate_buckets.clear()

    def test_private_models_endpoint_rejects_missing_key(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401)

    def test_private_models_endpoint_accepts_authorized_key(self):
        response = self.client.get(
            "/v1/models", headers={"Authorization": f"Bearer {self.raw_key}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["id"], "nexus-council")

    def test_legacy_conversations_endpoint_is_also_protected(self):
        response = self.client.get("/api/conversations")
        self.assertEqual(response.status_code, 401)

    def test_conversation_is_scoped_to_owning_client(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(storage, "DATA_DIR", directory):
                conversation = storage.create_conversation(
                    "12345678-1234-4234-9234-123456789abc", "client-a"
                )
                self.assertEqual(
                    storage.get_conversation(conversation["id"], "client-a")["id"],
                    conversation["id"],
                )
                self.assertIsNone(storage.get_conversation(conversation["id"], "client-b"))
                self.assertEqual(storage.list_conversations("client-b"), [])

    def test_conversation_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            storage.get_conversation("../../.env", "test-client")

    def test_admin_key_management_fails_closed_without_admin_secret(self):
        with patch.dict(os.environ, {"NEXUS_ADMIN_TOKEN": ""}):
            response = self.client.post(
                "/v1/admin/keys",
                json={"client_id": "test-client", "display_name": "Test"},
            )
        self.assertEqual(response.status_code, 503)

    def test_per_client_rate_limit_returns_429(self):
        with patch.dict(os.environ, {"RATE_LIMIT_REQUESTS_PER_MINUTE": "1"}):
            headers = {"Authorization": f"Bearer {self.raw_key}"}
            self.assertEqual(self.client.get("/v1/models", headers=headers).status_code, 200)
            response = self.client.get("/v1/models", headers=headers)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers.get("Retry-After"), "60")


if __name__ == "__main__":
    unittest.main()
