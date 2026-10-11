"""Stage C ACL: the four platforms' native rules, the organisation policy layer,
and the legacy-migration equivalence. Pure unit tests, no API calls.

    python -m unittest discover tests
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = json.load(open(os.path.join(ROOT, "data", "corpus.json"), encoding="utf-8"))["documents"]
LEGACY_ROLES = ["employee", "risk_analyst", "compliance_officer", "hr_manager", "executive"]


def doc(**kw):
    """A synthetic document with sensible defaults, for exercising acl.decide."""
    base = {"id": "X", "source": "confluence", "title": "t", "department": "Engineering",
            "sensitivity": "internal", "acl": {}, "text": "x"}
    base.update(kw)
    return base


class PlatformRules(unittest.TestCase):
    def allow(self, user, **kw):
        return acl.decide(user, doc(**kw)).allow

    # ---------------------------------------------------------------- Confluence
    def test_confluence_space_view(self):
        d = {"source": "confluence", "acl": {"space": "eng", "restrictions": {}}}
        self.assertTrue(self.allow("jdoe", **d))     # in group:eng
        self.assertFalse(self.allow("glim", **d))    # not in group:eng

    def test_confluence_page_restrictions(self):
        by_user = {"source": "confluence", "acl": {"space": "payment-gateway", "restrictions": {"users": ["wlin"]}}}
        self.assertTrue(self.allow("wlin", **by_user))
        self.assertFalse(self.allow("jdoe", **by_user))   # space viewer but not named
        by_group = {"source": "confluence", "acl": {"space": "security", "restrictions": {"groups": ["security"]}}}
        self.assertTrue(self.allow("sho", **by_group))
        self.assertFalse(self.allow("jdoe", **by_group))

    # ---------------------------------------------------------------- Jira
    def test_jira_browse_and_security_level(self):
        plain = {"source": "jira", "acl": {"project": "DBMIG", "security_level": None}}
        self.assertTrue(self.allow("jdoe", **plain))
        self.assertFalse(self.allow("glim", **plain))          # no browse
        secret = {"source": "jira", "acl": {"project": "SEC", "security_level": "security-team"}}
        self.assertTrue(self.allow("sho", **secret))           # group:security
        self.assertFalse(self.allow("jdoe", **secret))         # browse but wrong security level

    # ---------------------------------------------------------------- Slack
    def test_slack_public_and_private(self):
        public = {"source": "slack", "acl": {"channel": "#db-migration"}}
        self.assertTrue(self.allow("jdoe", **public))          # employee sees public channel
        self.assertFalse(self.allow("atan", **public))         # contractor, not a member
        guest = {"source": "slack", "acl": {"channel": "#payments-eng"}}
        self.assertTrue(self.allow("atan", **guest))           # invited guest
        private = {"source": "slack", "acl": {"channel": "#sre-private"}}
        self.assertTrue(self.allow("wlin", **private))         # member
        self.assertFalse(self.allow("jdoe", **private))        # not a member

    # ---------------------------------------------------------------- Google Drive
    def test_gdrive_folder_and_file_share(self):
        folder = {"source": "gdrive", "acl": {"folder": "eng-postmortems"}}
        self.assertTrue(self.allow("jdoe", **folder))          # group:eng inherits
        self.assertFalse(self.allow("atan", **folder))
        file_share = {"source": "gdrive", "acl": {"folder": "eng-postmortems", "shared_with": ["user:atan"]}}
        self.assertTrue(self.allow("atan", **file_share))      # file-level share overrides

    def test_unknown_user_and_unknown_source(self):
        self.assertFalse(acl.decide("nobody", doc(acl={"space": "eng", "restrictions": {}})).allow)
        self.assertEqual(acl.visible_ids("nobody"), set())
        self.assertFalse(acl.decide("jdoe", doc(source="myspace")).allow)


class PolicyLayer(unittest.TestCase):
    def test_contractor_blocked_from_restricted(self):
        # platform ACL allows atan (folder shared with user:atan) but policy denies
        d = doc(source="gdrive", sensitivity="restricted", acl={"folder": "vendor-qa"})
        decision = acl.decide("atan", d)
        self.assertFalse(decision.allow)
        self.assertEqual(decision.layer, "policy")
        # an employee with the same platform grant is allowed
        self.assertTrue(acl.decide("jdoe", d).allow)

    def test_policy_switch_can_be_turned_off(self):
        d = doc(source="gdrive", sensitivity="restricted", acl={"folder": "vendor-qa"})
        os.environ["CONTRACTOR_BLOCKS_RESTRICTED"] = "0"
        self.addCleanup(lambda: os.environ.pop("CONTRACTOR_BLOCKS_RESTRICTED", None))
        self.assertTrue(acl.decide("atan", d).allow)  # policy off => platform grant stands

    def test_policy_only_tightens_never_widens(self):
        # a restricted doc atan's platform ACL already denies stays denied, layer platform
        d = doc(source="confluence", sensitivity="restricted", acl={"space": "security", "restrictions": {}})
        decision = acl.decide("atan", d)
        self.assertFalse(decision.allow)
        self.assertEqual(decision.layer, "platform")


class LegacyMigrationEquivalence(unittest.TestCase):
    """Every persona's new-ACL visible set must equal the old allowed_roles set."""

    def test_personas_match_allowed_roles(self):
        for role in LEGACY_ROLES:
            new = acl.visible_ids("persona_" + role)
            old = {d["id"] for d in DOCS if role in d["allowed_roles"]}
            self.assertEqual(new, old, role)

    def test_legacy_docs_carry_platform_fields(self):
        for d in DOCS:
            self.assertEqual(d["source"], "confluence")
            self.assertIn("space", d["acl"])
            self.assertTrue(d["updated_at"])
            self.assertTrue(d["url"].startswith("https://"))
            self.assertIsInstance(d["links"], list)


class Locations(unittest.TestCase):
    def test_location_strings(self):
        self.assertEqual(acl.location(doc(source="confluence", acl={"space": "payment-gateway"})),
                         "confluence:payment-gateway")
        self.assertEqual(acl.location(doc(source="jira", acl={"project": "DBMIG"})), "jira:DBMIG")
        self.assertEqual(acl.location(doc(source="slack", acl={"channel": "#db-migration"})), "slack:#db-migration")
        self.assertEqual(acl.location(doc(source="gdrive", acl={"folder": "eng-postmortems"})), "gdrive:eng-postmortems")


if __name__ == "__main__":
    unittest.main()
