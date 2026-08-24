"""Matching employer replies to applications, and classifying what they say.

No mailbox is involved: the parts worth testing are the ones that decide, and
they are pure functions over already-fetched messages.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import mailsync  # noqa: E402

APPS = [
    {
        "id": 1, "slug": "acme", "company_name": "Acme", "role": "Frontend Developer",
        "status": "applied", "status_label": "Applied",
        "url": "https://jobs.acme.com/postings/123",
        "company_website": "https://acme.com",
    },
    {
        "id": 2, "slug": "globex", "company_name": "Globex", "role": "Engineer",
        "status": "draft", "status_label": "Draft",
        "url": None, "company_website": "https://globex.io",
    },
]


def message(subject="", body="", domain="somewhere.example"):
    return {"subject": subject, "body": body, "domain": domain,
            "from": f"someone@{domain}", "from_address": f"someone@{domain}"}


class ClassificationTest(unittest.TestCase):
    def kind(self, subject, body=""):
        return mailsync.classify(message(subject, body))[0]

    def test_recognises_each_kind(self):
        self.assertEqual(self.kind("Interview invitation",
                                   "we would like to schedule a call"), "interview")
        self.assertEqual(self.kind("Your application",
                                   "Unfortunately we are not proceeding"), "rejection")
        self.assertEqual(self.kind("Offer of employment", ""), "offer")
        self.assertEqual(self.kind("Take-home task", ""), "assignment")
        self.assertEqual(self.kind("We have received your application", ""),
                         "acknowledgement")

    def test_unrelated_mail_is_not_forced_into_a_category(self):
        self.assertEqual(self.kind("Weekly newsletter", "articles for you"), "unknown")

    def test_an_offer_outranks_a_rejection_in_the_same_message(self):
        # "Unfortunately not for role A, but we are delighted to offer role B"
        # must not be filed as a rejection.
        self.assertEqual(
            self.kind("Update",
                      "Unfortunately not for that role, but we are pleased to "
                      "offer you the other position"),
            "offer",
        )

    def test_an_interview_invitation_outranks_an_acknowledgement(self):
        self.assertEqual(
            self.kind("Application received",
                      "We have received your application. Would you be available "
                      "for an interview on Tuesday?"),
            "interview",
        )

    def test_matched_phrase_is_reported_so_a_human_can_disagree(self):
        kind, phrase = mailsync.classify(
            message("Update", "we regret to inform you")
        )
        self.assertEqual(kind, "rejection")
        self.assertIn("we regret", phrase.lower())


class MatchingTest(unittest.TestCase):
    def test_posting_url_is_the_strongest_evidence(self):
        app, why = mailsync.match_application(
            message("Re: application",
                    "about https://jobs.acme.com/postings/123",
                    domain="unrelated.example"),
            APPS,
        )
        self.assertEqual(app["slug"], "acme")
        self.assertIn("URL", why)

    def test_sender_domain_matches_the_company_site(self):
        app, why = mailsync.match_application(
            message("Hello", "regarding your application", domain="careers.acme.com"),
            APPS,
        )
        self.assertEqual(app["slug"], "acme")
        self.assertIn("acme.com", why)

    def test_company_name_in_the_text_is_the_weakest_match(self):
        app, why = mailsync.match_application(
            message("Globex interview", "", domain="recruiter.example"), APPS
        )
        self.assertEqual(app["slug"], "globex")
        self.assertIn("company name", why)

    def test_no_match_returns_nothing_rather_than_a_guess(self):
        app, why = mailsync.match_application(
            message("Unrelated", "nothing here", domain="other.example"), APPS
        )
        self.assertIsNone(app)
        self.assertEqual(why, "")

    def test_a_three_letter_name_is_too_weak_to_match_on(self):
        apps = [{**APPS[0], "company_name": "IBM", "company_website": None,
                 "url": None}]
        app, _ = mailsync.match_application(
            message("Subscribe", "unsubscribe here", domain="x.example"), apps
        )
        self.assertIsNone(app)

    def test_company_name_matches_on_word_boundaries_not_substrings(self):
        apps = [{**APPS[0], "company_name": "Meta", "company_website": None,
                 "url": None}]
        # "metadata" is not Meta.
        app, _ = mailsync.match_application(
            message("Your metadata export is ready", "", domain="x.example"), apps
        )
        self.assertIsNone(app)
        # The company itself still matches.
        app, _ = mailsync.match_application(
            message("Interview at Meta", "", domain="x.example"), apps
        )
        self.assertIsNotNone(app)


class UrlDomainTest(unittest.TestCase):
    def test_extracts_and_normalises(self):
        self.assertEqual(mailsync.domain_of("https://www.acme.com/careers"), "acme.com")
        self.assertEqual(mailsync.domain_of("http://acme.io"), "acme.io")
        self.assertEqual(mailsync.domain_of(None), "")
        self.assertEqual(mailsync.domain_of("not a url"), "")


class StatusProposalTest(unittest.TestCase):
    def test_only_actionable_kinds_propose_a_status(self):
        self.assertEqual(mailsync.SUGGESTED_STATUS["rejection"], "rejected")
        self.assertEqual(mailsync.SUGGESTED_STATUS["interview"], "screening")
        self.assertEqual(mailsync.SUGGESTED_STATUS["offer"], "offer")
        # An automated "we got it" is not a funnel stage.
        self.assertIsNone(mailsync.SUGGESTED_STATUS["acknowledgement"])
        self.assertIsNone(mailsync.SUGGESTED_STATUS["unknown"])


if __name__ == "__main__":
    unittest.main()
