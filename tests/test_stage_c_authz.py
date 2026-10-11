"""Relationship-tuple authorization (section 5.2).

Covers tuple generation, object-id normalisation, revocation as tuple deletion,
the platform-vs-policy layering, and — when an OpenFGA server is reachable — a
consistency check that the local and OpenFGA backends agree for every
user x document pair. No API calls; OpenFGA tests are skipped unless
OPENFGA_API_URL is set.

    python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import authz
from authz import tuples as tup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = json.load(open(os.path.join(ROOT, "data", "corpus.json"), encoding="utf-8"))["documents"]
STATE = os.path.join(ROOT, "state")


def _unlink(path):
    if os.path.exists(path):
        os.remove(path)


class TupleGeneration(unittest.TestCase):
    def test_object_id_normalisation(self):
        self.assertEqual(tup.oid("#db-migration"), "db-migration")
        self.assertEqual(tup.oid("payment gateway"), "payment-gateway")
        self.assertEqual(tup.oid("eng"), "eng")

    def test_every_document_has_an_object(self):
        built = tup.build(authz.directory(), DOCS)
        objects = {t["object"] for t in built}
        for doc in DOCS:
            self.assertIn(tup.object_id(doc), objects, doc["id"])

    def test_restricted_documents_get_a_policy_block_tuple(self):
        built = tup.build(authz.directory(), DOCS, policy=True)
        blocked = {t["object"] for t in built if t["relation"] == "blocked"}
        for doc in DOCS:
            if doc["sensitivity"] == "restricted":
                self.assertIn(tup.object_id(doc), blocked, doc["id"])
        # with the policy off there is no blocked tuple at all
        off = tup.build(authz.directory(), DOCS, policy=False)
        self.assertFalse([t for t in off if t["relation"] == "blocked"])


class RevocationIsTupleDeletion(unittest.TestCase):
    def test_deleting_a_membership_tuple_revokes_access(self):
        self.addCleanup(lambda: (_unlink(os.path.join(STATE, "tuples.json")), authz.reload()))
        self.assertIn("E13", authz.list_readable("jdoe"))  # member of #payments-incident
        authz.delete([{"user": "user:jdoe", "relation": "member",
                       "object": "slack_channel:payments-incident"}])
        authz.reload()
        self.assertNotIn("E13", authz.list_readable("jdoe"))
        self.assertNotIn("E13", authz.list_readable("jdoe"))


class PlatformVersusPolicy(unittest.TestCase):
    def test_e21_is_readable_on_the_platform_but_denied_by_policy(self):
        # E21 sits in the vendor-qa folder shared with atan, so the *platform* ACL
        # grants it; the policy layer (contractors never see restricted) denies it.
        self.assertIn("atan", authz.platform_readers("E21"))
        decision = authz.check("atan", "E21")
        self.assertFalse(decision.allow)
        self.assertEqual(decision.layer, "policy")

    def test_platform_denial_is_reported_as_platform(self):
        decision = authz.check("atan", "E09")
        self.assertFalse(decision.allow)
        self.assertEqual(decision.layer, "platform")

    def test_contractor_block_can_be_switched_off(self):
        os.environ["CONTRACTOR_BLOCKS_RESTRICTED"] = "0"
        self.addCleanup(lambda: os.environ.pop("CONTRACTOR_BLOCKS_RESTRICTED", None))
        authz.reload()
        self.assertTrue(authz.check("atan", "E21").allow)  # platform grant stands


class OpenFGAConsistency(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("OPENFGA_API_URL"), "OPENFGA_API_URL not set")
    def test_local_and_openfga_agree_for_every_pair(self):
        if not os.path.exists(os.path.join(ROOT, ".cache", "openfga.json")):
            self.skipTest("OpenFGA store not bootstrapped (run scripts/openfga_bootstrap.py)")
        local = authz.LocalAuthz()
        os.environ["AUTHZ_BACKEND"] = "openfga"
        self.addCleanup(lambda: (os.environ.pop("AUTHZ_BACKEND", None), authz.reset_backend()))
        authz.reset_backend()
        remote = authz.get_authz()
        for user_id in authz.directory().get("users", {}):
            for doc_id in authz.documents():
                self.assertEqual(local.check(user_id, doc_id).allow, remote.check(user_id, doc_id).allow,
                                 f"{user_id} x {doc_id}")


if __name__ == "__main__":
    unittest.main()
