"""Integration tests for the PostgreSQL repository; run with DATABASE_URL set."""

import os
import uuid
import unittest

from backend import database, repository


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is not set")
class PostgresRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await database.connect()
        self.client_id = "it-" + uuid.uuid4().hex
        self.raw_key = "nxs-integration-test-" + uuid.uuid4().hex
        import hashlib
        self.digest = hashlib.sha256(self.raw_key.encode()).hexdigest()
        self.key_id = await repository.create_client_key(
            self.client_id, "CI integration test", self.raw_key, self.digest
        )

    async def asyncTearDown(self):
        async with database.pool().acquire() as conn:
            await conn.execute("DELETE FROM nexus_usage_events WHERE client_id=$1", self.client_id)
            await conn.execute("DELETE FROM nexus_conversations WHERE client_id=$1", self.client_id)
            await conn.execute("DELETE FROM nexus_api_keys WHERE client_id=$1", self.client_id)
            await conn.execute("DELETE FROM nexus_api_clients WHERE client_id=$1", self.client_id)

    async def test_key_hash_lookup_and_revocation(self):
        found = await repository.find_active_key(self.digest)
        self.assertIsNotNone(found)
        self.assertEqual(found["client_id"], self.client_id)
        self.assertTrue(await repository.revoke_client_key(self.key_id))
        self.assertIsNone(await repository.find_active_key(self.digest))

    async def test_conversation_is_durable_and_client_scoped(self):
        conversation_id = str(uuid.uuid4())
        created = await repository.create_conversation(conversation_id, self.client_id)
        self.assertEqual(created["id"], conversation_id)
        self.assertIsNotNone(await repository.get_conversation(conversation_id, self.client_id))
        self.assertIsNone(await repository.get_conversation(conversation_id, "another-client"))
        await repository.add_user_message(conversation_id, "hello", self.client_id)
        loaded = await repository.get_conversation(conversation_id, self.client_id)
        self.assertEqual(loaded["messages"][0], {"role": "user", "content": "hello"})
        items = await repository.list_conversations(self.client_id)
        self.assertTrue(any(item["id"] == conversation_id for item in items))

    async def test_usage_summary_aggregates_events(self):
        await repository.record_usage(self.client_id, self.key_id, "GET", "/test", 200, 25)
        await repository.record_usage(self.client_id, self.key_id, "POST", "/test", 500, 75)
        summary = await repository.usage_summary(self.client_id, 30)
        self.assertGreaterEqual(summary["requests"], 2)
        self.assertGreaterEqual(summary["errors"], 1)
        self.assertGreaterEqual(summary["duration_ms"], 100)


if __name__ == "__main__":
    unittest.main()
