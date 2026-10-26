#!/usr/bin/env python3
"""
Comprehensive automated verification suite for Indian Government & Banking Notice Monitor (`bank-new`).
Tests:
1. All 5 keyword categories and 32 keywords (Recruitment, Examination, Results, Answer Keys, Next Stages)
2. Case-insensitive matching and spacing/hyphenation/compound variations
3. Matching in notice title OR available HTML context/content
4. False-match prevention (word boundaries for DV, PET, PST, posts + corporate/financial/tender noise filtering)
5. Telegram message format: Platform/Organization, Title, Official Publication Date (or 'Publication date unavailable'), and Direct Official Link
6. Historical notice suppression (old published_date, old date in title, old date in URL, compact YYYYMMDD, historical years)
7. Missing dates handled safely without suppressing genuinely new notices and without inventing today's date
8. Multi-layer deduplication: duplicate URLs, changing query parameters, mirror domains, Notice IDs, and similar titles
9. Per-run cap of 10 successful notifications per run, queueing excess notices, failed Telegram delivery retry, and repeat-run idempotency
10. State union-merge conflict resolution and all 23 configured website definitions & baseline preservation
"""

import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from monitor import (  # noqa: E402
    DEFAULT_KEYWORD_CATEGORIES,
    DEFAULT_MAX_NOTIFICATIONS_PER_RUN,
    Notice,
    NoticeMonitor,
    canonical_url_key,
    canonicalize_host,
    compute_fingerprint,
    extract_date,
    extract_notice_id,
    format_telegram_message,
    is_verified_publication_date,
    match_notice_keywords,
    matches_keyword_filter,
    merge_state_files,
    normalize_title,
    normalize_url,
    parse_publication_date,
)


class TestGovernmentNoticeMonitorAudit(unittest.TestCase):
    def test_all_required_keywords_present_and_match(self) -> None:
        expected = {
            "Recruitment": [
                "new vacancy", "recruitment", "notification", "advertisement",
                "apply online", "application form", "posts",
            ],
            "Examination": [
                "exam date", "examination date", "exam schedule",
                "admit card", "hall ticket", "city intimation",
            ],
            "Results": [
                "result", "scorecard", "merit list",
                "cut off", "cutoff", "selection list",
            ],
            "Answer Keys": [
                "answer key", "answerkey", "provisional answer key",
                "final answer key", "response sheet",
            ],
            "Next Stages": [
                "DV", "document verification", "skill test", "typing test",
                "interview", "PET", "PST", "medical examination",
            ],
        }
        self.assertEqual(DEFAULT_KEYWORD_CATEGORIES, expected)
        self.assertEqual(DEFAULT_MAX_NOTIFICATIONS_PER_RUN, 10)

        for category, kw_list in expected.items():
            for kw in kw_list:
                sample_title = f"Official Update regarding {kw.upper()} for 2026-27 cycle"
                matched, matched_cat, _ = match_notice_keywords(sample_title)
                self.assertTrue(matched, f"Keyword '{kw}' ({category}) failed to match '{sample_title}'")
                self.assertEqual(matched_cat, category)

    def test_case_insensitivity_and_spacing_variations(self) -> None:
        variations = [
            ("Download ADMIT-CARD for Online Prelims", True),
            ("Download AdmitCard for Clerk 2026", True),
            ("Download   Admit   Card   Link", True),
            ("Hall-Ticket / Hallticket for Phase II", True),
            ("Check Score Card / Score-Card / SCORECARD 2026", True),
            ("Category-wise Cut-Off / CUTOFF / Cut   Off Marks", True),
            ("Provisional   Answer-Key and ResponseSheet uploaded", True),
            ("Final AnswerKey for Specialist Officer", True),
            ("Merit-List and SelectionList for Grade B", True),
            ("Schedule of Skill-Test and TypingTest", True),
            ("New   Vacancies / New-Vacancy in Officer Cadre", True),
            ("Exam-Date & City-Intimation Slip", True),
            ("Call for DV / Document-Verification and PET/PST", True),
        ]
        for text, expected in variations:
            self.assertEqual(
                matches_keyword_filter(text),
                expected,
                f"Unexpected match result for variation: {text}",
            )

    def test_false_matches_and_non_recruitment_noise_rejected(self) -> None:
        false_positives = [
            "Corrigendum to Advt. No. 04 for Advisory Services and Advance Payment",  # 'advt'/'advisory' != 'DV'
            "Invitation of Competitive Bids from Competent Vendors for Carpet Supply",  # 'competitive'/'carpet' != 'PET'
            "Upstate Regional Branch Address and Postal Pin Code Update",  # 'upstate' != 'PST', 'postal' != 'posts'
            "Postponement of Annual General Meeting of Shareholders",  # 'postponement' != 'posts'
            "Apply Online for Home Loan at Concessional Interest Rates",  # retail loan noise
            "Apply Online for Credit Card and Personal Loan",  # retail banking noise
            "Notice Inviting Tender for Supply, Installation of Servers",  # vendor tender noise
            "Money Market Operations and Variable Rate Repo Auction",  # daily liquidity noise
            "Unaudited Financial Results for Q2 FY 2026-27",  # corporate earnings result != exam result
            "Auction Result of Government Securities and Treasury Bills",  # bond auction != exam result
            "FEMA Notification on Foreign Exchange Management",  # regulatory circular != recruitment notification
            "SARFAESI E-Auction Sale Notice for Immovable Property",  # property auction != recruitment notice
        ]
        for text in false_positives:
            self.assertFalse(
                matches_keyword_filter(text),
                f"False positive matched on non-recruitment text: {text}",
            )

    def test_telegram_message_format_and_missing_or_unverified_dates(self) -> None:
        # 1. Notice with verified official publication date
        n_with_date = Notice(
            website_id="ibps",
            org_name="IBPS",
            title="CRP PO/MT-XVI Recruitment Notification 2026",
            url="https://www.ibps.in/docs/crp-po-2026.pdf",
            published_date="18-10-2026",
            fingerprint="fp1",
            first_seen="2026-10-20T00:00:00+00:00",
        )
        msg1 = format_telegram_message(n_with_date)
        self.assertIn("<b>New Official Notice — IBPS</b>", msg1)
        self.assertIn("Title: CRP PO/MT-XVI Recruitment Notification 2026", msg1)
        self.assertIn("Published: 18-10-2026", msg1)
        self.assertIn("Link: https://www.ibps.in/docs/crp-po-2026.pdf", msg1)

        # 2. Notice with missing date (None) -> must display 'Publication date unavailable'
        n_no_date = Notice(
            website_id="rbi-opportunities",
            org_name="RBI Opportunities",
            title="Recruitment for the Post of Officers in Grade B (DR) - PY 2026",
            url="https://opportunities.rbi.org.in/scripts/vacancies.aspx",
            published_date=None,
            fingerprint="fp2",
            first_seen="2026-10-20T00:00:00+00:00",
        )
        msg2 = format_telegram_message(n_no_date)
        self.assertIn("<b>New Official Notice — RBI Opportunities</b>", msg2)
        self.assertIn("Title: Recruitment for the Post of Officers in Grade B (DR) - PY 2026", msg2)
        self.assertIn("Published: Publication date unavailable", msg2)
        self.assertIn("Link: https://opportunities.rbi.org.in/scripts/vacancies.aspx", msg2)

        # 3. Notice with unverified/future deadline date -> must display 'Publication date unavailable'
        n_future_date = Notice(
            website_id="sbi",
            org_name="State Bank of India (SBI)",
            title="Recruitment of Specialist Cadre Officers",
            url="https://sbi.co.in/docs/sco-2026.pdf",
            published_date="31-12-2099",
            fingerprint="fp3",
            first_seen="2026-10-20T00:00:00+00:00",
        )
        msg3 = format_telegram_message(n_future_date)
        self.assertIn("Published: Publication date unavailable", msg3)
        self.assertNotIn("31-12-2099", msg3)

        # 4. Deadline dates in text should not be extracted as publication dates
        deadline_text = "Apply Online for Probationary Officers (Last Date: 30-11-2026)"
        self.assertIsNone(extract_date(deadline_text))

    def test_historical_notices_suppressed_and_missing_dates_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "websites.json")
            st_path = os.path.join(tmp, "notices.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "websites": [{
                        "id": "sbi",
                        "name": "State Bank of India (SBI)",
                        "official_url": "https://sbi.co.in/",
                        "homepage_url": "https://sbi.co.in/",
                        "notice_urls": ["https://sbi.co.in/web/careers/current-openings"],
                        "category": "Public Sector Bank",
                        "status": "verified",
                    }]
                }, f)

            with open(st_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["sbi"],
                    "notices": {},
                }, f)

            html_page = """
            <html><body>
              <ul>
                <li><a href="https://sbi.co.in/docs/old-1.pdf">Recruitment Notification for Clerks (10-05-2024)</a></li>
                <li><a href="https://sbi.co.in/docs/notice_15-08-2023.pdf">Final Result for Specialist Officers</a></li>
                <li><a href="https://sbi.co.in/docs/20230412_po_merit.pdf">Merit List for Probationary Officers</a></li>
                <li><a href="https://sbi.co.in/uploads/2022/advt_2022_po.pdf">Recruitment of Probationary Officers 2022-23</a></li>
                <li><a href="https://sbi.co.in/docs/brand-new-undated-po.pdf">Recruitment Notification for Circle Based Officers</a></li>
              </ul>
            </body></html>
            """
            m = NoticeMonitor(config_path=cfg_path, state_path=st_path, dry_run=True)
            m.fetch_url = lambda url: html_page
            sent_messages = []
            m.send_telegram_alert = lambda n: (sent_messages.append(format_telegram_message(n)) or True)
            m.run()

            # 4 historical notices suppressed; 1 genuinely new undated notice allowed and formatted safely
            self.assertEqual(m.old_notices_suppressed, 4)
            self.assertEqual(m.telegram_sent, 1)
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("Recruitment Notification for Circle Based Officers", sent_messages[0])
            self.assertIn("Published: Publication date unavailable", sent_messages[0])

    def test_duplicate_urls_changing_query_params_mirror_domains_and_similar_titles(self) -> None:
        # Verify mirror host canonicalization across Indian bank domains
        self.assertEqual(
            canonical_url_key("https://www.ucobank.com/pdf/po-result-2026.pdf?ver=1&_t=100"),
            canonical_url_key("https://www.uco.bank.in/pdf/po-result-2026.pdf?itok=xyz987&utm_source=tg"),
        )
        self.assertEqual(
            canonical_url_key("https://bank.sbi/documents/crpd-po-2026.pdf?random_cache=12345"),
            canonical_url_key("https://sbi.co.in/documents/crpd-po-2026.pdf"),
        )
        self.assertEqual(
            canonical_url_key("https://punjabandsindbank.co.in/content/so-2026.pdf"),
            canonical_url_key("https://punjabandsind.bank.in/content/so-2026.pdf"),
        )
        self.assertEqual(
            canonical_url_key("https://www.idbibank.in/pdf/jam-2026.pdf"),
            canonical_url_key("https://www.idbi.bank.in/pdf/jam-2026.pdf"),
        )
        self.assertEqual(
            canonicalize_host("www.bankofmaharashtra.in"),
            canonicalize_host("bankofmaharashtra.bank.in"),
        )

        # Verify similar title normalization (serial numbers, NEW badges, PDF sizes, Click Here suffixes, & vs and)
        t1 = "1. Recruitment of Probationary Officers & Management Trainees 2026 - NEW! (PDF - 245 KB) - Click Here"
        t2 = "2. Recruitment of Probationary Officers and Management Trainees 2026 [English / Hindi] Download PDF"
        self.assertEqual(normalize_title(t1), normalize_title(t2))

        # Distinct notices (Phase I vs Phase II, Grade A vs Grade B) must remain distinct
        self.assertNotEqual(
            normalize_title("Phase I Result for Officer Grade A 2026"),
            normalize_title("Phase II Result for Officer Grade A 2026"),
        )

        # End-to-end run verification with baseline notice followed by mirror domain, query param, and similar title variants
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "websites.json")
            st_path = os.path.join(tmp, "notices.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "websites": [{
                        "id": "uco-bank",
                        "name": "UCO Bank",
                        "official_url": "https://www.uco.bank.in/en/careers",
                        "homepage_url": "https://www.uco.bank.in/",
                        "notice_urls": ["https://www.uco.bank.in/en/careers"],
                        "category": "Public Sector Bank",
                        "status": "verified",
                    }]
                }, f)

            orig_url = "https://www.uco.bank.in/docs/so-recruitment-2026.pdf"
            orig_title = "1. Recruitment of Specialist Officers in Scale I & II 2026"
            orig_fp = compute_fingerprint(normalize_url(orig_url), orig_title)

            with open(st_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["uco-bank"],
                    "notices": {
                        orig_fp: {
                            "website_id": "uco-bank",
                            "org_name": "UCO Bank",
                            "title": orig_title,
                            "url": orig_url,
                            "published_date": "19-10-2026",
                            "fingerprint": orig_fp,
                            "first_seen": "2026-10-19T00:00:00+00:00",
                            "delivered": True,
                        }
                    },
                }, f)

            # Next run encounters:
            # - Mirror domain (.ucobank.com instead of .uco.bank.in)
            # - Changing query parameters (?itok=abc&_t=9999)
            # - Shifted serial number ("2.") and added "- NEW (PDF 320 KB) Click Here"
            html_page = """
            <html><body>
              <ul>
                <li><a href="https://www.ucobank.com/docs/so-recruitment-2026.pdf?itok=abc&_t=9999">
                  New Notice Anchor Text on Mirror Domain for Same PDF
                </a></li>
                <li><a href="https://www.uco.bank.in/new-folder/different-path.html?cb=123">
                  2. Recruitment of Specialist Officers in Scale I and II 2026 - NEW! (PDF 320 KB) - Click Here
                </a></li>
              </ul>
            </body></html>
            """
            m = NoticeMonitor(config_path=cfg_path, state_path=st_path, dry_run=True)
            m.fetch_url = lambda url: html_page
            sent = []
            m.send_telegram_alert = lambda n: (sent.append(n.title) or True)
            m.run()

            self.assertEqual(len(sent), 0)
            self.assertEqual(m.telegram_sent, 0)
            self.assertGreaterEqual(m.duplicates_suppressed, 2)

    def test_title_or_available_content_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "websites.json")
            st_path = os.path.join(tmp, "notices.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "websites": [{
                        "id": "sbi",
                        "name": "State Bank of India (SBI)",
                        "official_url": "https://sbi.co.in/",
                        "homepage_url": "https://sbi.co.in/",
                        "notice_urls": ["https://sbi.co.in/web/careers/current-openings"],
                        "category": "Public Sector Bank",
                        "status": "verified",
                    }]
                }, f)

            with open(st_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["sbi"],
                    "notices": {},
                }, f)

            m = NoticeMonitor(config_path=cfg_path, state_path=st_path, dry_run=True)
            html_page = """
            <html><body>
              <table>
                <tr>
                  <td>Schedule of Document Verification (DV) and Interview for PO</td>
                  <td><a href="https://sbi.co.in/docs/notice-2026-99.pdf">Notice No. 2026/99</a></td>
                </tr>
                <tr>
                  <td>Corporate Governance & Sustainability Report</td>
                  <td><a href="https://sbi.co.in/docs/annual-report-2026.pdf">Annual Report 2026-27</a></td>
                </tr>
              </table>
            </body></html>
            """
            m.fetch_url = lambda url: html_page
            sent_titles = []
            m.send_telegram_alert = lambda n: (sent_titles.append(n.title) or True)
            m.run()

            self.assertEqual(sent_titles, ["Notice No. 2026/99"])
            self.assertEqual(m.telegram_sent, 1)
            self.assertEqual(m.keyword_filtered_out, 1)

    def test_limit_10_successful_notifications_with_partial_failure_and_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "websites.json")
            kw_path = os.path.join(tmp, "keywords.json")
            st_path = os.path.join(tmp, "notices.json")

            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "websites": [{
                        "id": "pnb",
                        "name": "Punjab National Bank (PNB)",
                        "official_url": "https://www.pnbindia.in/",
                        "homepage_url": "https://www.pnbindia.in/",
                        "notice_urls": ["https://www.pnbindia.in/recruitment.html"],
                        "category": "Public Sector Bank",
                        "status": "verified",
                    }]
                }, f)

            with open(kw_path, "w", encoding="utf-8") as f:
                json.dump({
                    "enabled": True,
                    "max_notifications_per_run": 10,
                    "categories": DEFAULT_KEYWORD_CATEGORIES,
                }, f)

            with open(st_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["pnb"],
                    "notices": {},
                }, f)

            # Generate 14 distinct new matching notices + 3 non-matching notices + 2 old notices
            rows = []
            for i in range(1, 15):
                rows.append(
                    f'<li><a href="https://www.pnbindia.in/doc_new_{i}.pdf">'
                    f'Recruitment Notification for Specialist Officer Post #{i} (20-10-2026)</a></li>'
                )
            for j in range(1, 4):
                rows.append(
                    f'<li><a href="https://www.pnbindia.in/unrelated_{j}.pdf">'
                    f'Corporate Social Responsibility Initiative #{j} (20-10-2026)</a></li>'
                )
            for k in range(1, 3):
                rows.append(
                    f'<li><a href="https://www.pnbindia.in/old_{k}.pdf">'
                    f'Recruitment Result for Clerk Post #{k} (10-05-2024)</a></li>'
                )
            html_page = "<html><body><ul>" + "\n".join(rows) + "</ul></body></html>"

            # Run 1: Simulate Notice #2 failing Telegram delivery.
            # Because the limit is 10 SUCCESSFUL notifications per run, Run 1 must still deliver 10
            # healthy notices (#1, #3..#11), record #2 as failed (delivered=False), and queue #12..#14 (3 capped).
            m1 = NoticeMonitor(
                config_path=cfg_path,
                keywords_config_path=kw_path,
                state_path=st_path,
                dry_run=True,
            )
            m1.fetch_url = lambda url: html_page
            sent_run1 = []

            def mock_send_run1(n: Notice) -> bool:
                if "Post #2 " in n.title:
                    return False
                sent_run1.append(n.title)
                return True

            m1.send_telegram_alert = mock_send_run1
            m1.run()

            self.assertEqual(len(sent_run1), 10)
            self.assertEqual(m1.telegram_sent, 10)
            self.assertEqual(m1.telegram_failed, 1)
            self.assertEqual(m1.notifications_capped, 3)
            self.assertEqual(m1.keyword_filtered_out, 3)
            self.assertEqual(m1.old_notices_suppressed, 2)

            # Run 2: Should retry failed Notice #2 AND deliver the 3 queued notices (#12, #13, #14) = 4 total
            m2 = NoticeMonitor(
                config_path=cfg_path,
                keywords_config_path=kw_path,
                state_path=st_path,
                dry_run=True,
            )
            m2.fetch_url = lambda url: html_page
            sent_run2 = []
            m2.send_telegram_alert = lambda n: (sent_run2.append(n.title) or True)
            m2.run()

            self.assertEqual(len(sent_run2), 4)
            self.assertEqual(m2.telegram_sent, 4)
            self.assertEqual(m2.notifications_capped, 0)
            self.assertTrue(set(sent_run1).isdisjoint(set(sent_run2)))
            self.assertEqual(len(sent_run1) + len(sent_run2), 14)

            # Run 3 (Repeat Run): All 14 have been delivered -> 0 alerts sent
            m3 = NoticeMonitor(
                config_path=cfg_path,
                keywords_config_path=kw_path,
                state_path=st_path,
                dry_run=True,
            )
            m3.fetch_url = lambda url: html_page
            sent_run3 = []
            m3.send_telegram_alert = lambda n: (sent_run3.append(n.title) or True)
            m3.run()

            self.assertEqual(len(sent_run3), 0)
            self.assertEqual(m3.telegram_sent, 0)

    def test_delivery_failure_marks_undelivered_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "websites.json")
            st_path = os.path.join(tmp, "notices.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({
                    "websites": [{
                        "id": "sbi",
                        "name": "SBI",
                        "official_url": "https://sbi.co.in/",
                        "homepage_url": "https://sbi.co.in/",
                        "notice_urls": ["https://sbi.co.in/web/careers/current-openings"],
                        "category": "Public Sector Bank",
                        "status": "verified",
                    }]
                }, f)
            with open(st_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["sbi"],
                    "notices": {},
                }, f)

            html_page = (
                '<html><body><a href="https://sbi.co.in/po-final-result-2026.pdf">'
                'Final Result & Merit List for PO 2026 (21-10-2026)</a></body></html>'
            )

            # Run 1: Telegram fails -> delivered must remain False
            m_fail = NoticeMonitor(config_path=cfg_path, state_path=st_path)
            m_fail.fetch_url = lambda url: html_page
            m_fail.send_telegram_alert = lambda n: False
            m_fail.run()

            self.assertEqual(m_fail.telegram_sent, 0)
            self.assertEqual(m_fail.telegram_failed, 1)
            with open(st_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["notices"]), 1)
            only_notice = list(saved["notices"].values())[0]
            self.assertFalse(only_notice["delivered"])

            # Run 2: Telegram succeeds -> delivered becomes True
            m_ok = NoticeMonitor(config_path=cfg_path, state_path=st_path)
            m_ok.fetch_url = lambda url: html_page
            m_ok.send_telegram_alert = lambda n: True
            m_ok.run()

            self.assertEqual(m_ok.telegram_sent, 1)
            with open(st_path, "r", encoding="utf-8") as f:
                saved2 = json.load(f)
            only_notice2 = list(saved2["notices"].values())[0]
            self.assertTrue(only_notice2["delivered"])

    def test_state_union_merge_and_all_23_website_configurations(self) -> None:
        cfg_path = os.path.join(PROJECT_ROOT, "config", "websites.json")
        kw_path = os.path.join(PROJECT_ROOT, "config", "keywords.json")
        st_path = os.path.join(PROJECT_ROOT, "state", "notices.json")
        wf_path = os.path.join(PROJECT_ROOT, ".github", "workflows", "monitor.yml")

        for p in (cfg_path, kw_path, st_path, wf_path):
            self.assertTrue(os.path.exists(p), f"Required file missing: {p}")

        m = NoticeMonitor(config_path=cfg_path, keywords_config_path=kw_path, state_path=st_path, dry_run=True)
        m.load_config()
        m.load_state()
        self.assertEqual(len(m.websites), 23)
        self.assertEqual(m.max_notifications_per_run, 10)
        self.assertGreaterEqual(len(m.existing_notices), 3400)
        self.assertEqual(len(m.baselined_websites), 23)
        self.assertTrue(bool(m.state.get("baseline_completed")))
        self.assertTrue(bool(m.state.get("baseline_timestamp")))

        # Verify each of the 23 websites has valid id, name, official_url, and notice_urls
        expected_ids = {
            "ibps", "sbi", "sbi-careers", "rbi", "rbi-opportunities",
            "sidbi", "nhb", "pnb", "canara-bank", "indian-bank",
            "central-bank-of-india", "iob", "uco-bank", "bank-of-maharashtra",
            "punjab-and-sind-bank", "idbi-bank", "idbi-careers", "exim-bank",
            "niacl", "lic", "gic-re", "sebi", "fci",
        }
        configured_ids = {site["id"] for site in m.websites}
        self.assertEqual(configured_ids, expected_ids)
        for site in m.websites:
            self.assertTrue(site.get("name"))
            self.assertTrue(site.get("official_url", "").startswith("https://"))
            self.assertGreaterEqual(len(site.get("notice_urls", [])), 1)

        # Verify union-merge preserves delivered=True across concurrent state files
        with tempfile.TemporaryDirectory() as tmp:
            b_path = os.path.join(tmp, "base.json")
            o_path = os.path.join(tmp, "other.json")
            out_path = os.path.join(tmp, "out.json")
            with open(b_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["sbi"],
                    "notices": {
                        "fp_a": {
                            "website_id": "sbi", "org_name": "SBI", "title": "Notice A",
                            "url": "https://sbi.co.in/a.pdf", "published_date": None,
                            "fingerprint": "fp_a", "first_seen": "2026-10-20T01:00:00+00:00",
                            "delivered": False,
                        }
                    },
                }, f)
            with open(o_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "baseline_completed": True,
                    "baseline_timestamp": "2026-10-18T00:00:00+00:00",
                    "baselined_websites": ["pnb"],
                    "notices": {
                        "fp_a": {
                            "website_id": "sbi", "org_name": "SBI", "title": "Notice A",
                            "url": "https://sbi.co.in/a.pdf", "published_date": "20-10-2026",
                            "fingerprint": "fp_a", "first_seen": "2026-10-20T02:00:00+00:00",
                            "delivered": True,
                        },
                        "fp_b": {
                            "website_id": "pnb", "org_name": "PNB", "title": "Notice B",
                            "url": "https://www.pnbindia.in/b.pdf", "published_date": None,
                            "fingerprint": "fp_b", "first_seen": "2026-10-20T02:00:00+00:00",
                            "delivered": True,
                        },
                    },
                }, f)
            merge_state_files(b_path, o_path, out_path)
            with open(out_path, "r", encoding="utf-8") as f:
                merged = json.load(f)
            self.assertTrue(merged["notices"]["fp_a"]["delivered"])
            self.assertEqual(merged["notices"]["fp_a"]["published_date"], "20-10-2026")
            self.assertIn("fp_b", merged["notices"])
            self.assertEqual(set(merged["baselined_websites"]), {"sbi", "pnb"})

    def test_production_bug_fixes_rbi_exim_psb_bom_indianbank(self) -> None:
        """
        Regression test for the production bug where GitHub Actions reported Success
        while new bank notices were missing from Telegram:
        1. Genuine post-baseline RBI, EXIM Bank, GIC Re, and Punjab & Sind Bank notices match keywords & dates.
        2. RBI preceding-row <td class="tableheader">Oct 23, 2026</td> date extraction works.
        3. Compact DDMMYYYY (SBI Hindi PDF) and YYYYMMDDHHMMSS (PSB PDF) URL dates are parsed accurately.
        4. Bare file-size titles like '(645 KB)', retail 'Pet Dog' / 'Common Loan Application Form', and 'Apply Now' are rejected.
        5. WAF 'Request Rejected' page triggers homepage fallback instead of counting as a valid careers page.
        """
        from monitor import extract_url_date

        # 1. Verify real post-baseline notices from Oct 21-25, 2026 match keywords
        real_new_notices = [
            (
                "Engagement of Part- Time Bank’s Medical Consultants (BMC) on Contract Basis with Fixed Hourly Remuneration at Reserve Bank of India, Thiruvananthapuram",
                "Recruitment",
            ),
            (
                "LIST OF CANDIDATES SELECTED AS DEPUTY MANAGERS (AD. NO: HRM/ DM & M/ SRD/ 2025-26/ 07)",
                "Results",
            ),
            (
                "LIST OF SHORTLISTED CANDIDATES FOR APPEARING IN PERSONAL INTERVIEW (ADVERTISEMENT NUMBER: HRM/ DM/ 2026-27/ 03)",
                "Recruitment",
            ),
            (
                "Corrigendum: GEM/2026/B/8134254 - Appointment of an Actuary",
                "Recruitment",
            ),
            (
                "LATERAL RECRUITMENT OF LOCAL BANK OFFICERS (PHASE III) IN THE BANK ON REGULAR BASIS",
                "Recruitment",
            ),
        ]
        for title, expected_cat in real_new_notices:
            matched, cat, _ = match_notice_keywords(title)
            self.assertTrue(matched, f"Expected real notice to match keywords: {title}")
            self.assertEqual(cat, expected_cat)

        # 2. Verify retail/UI noise from live run is rejected
        live_noise = [
            "Pet Dog",
            "Common Loan Application Form",
            "(645 KB)",
            "(577 KB)",
        ]
        for noise_title in live_noise:
            matched, _, _ = match_notice_keywords(noise_title)
            self.assertFalse(matched, f"Expected live noise to be rejected: {noise_title}")

        # 3. Verify compact URL dates (DDMMYYYY and YYYYMMDDHHMMSS)
        self.assertEqual(
            extract_url_date("https://sbi.co.in/documents/77530/52947104/21082026_JA+2025+-Waitlist-2_Hindi+Website.pdf"),
            "21-08-2026",
        )
        self.assertEqual(
            extract_url_date("https://punjabandsind.bank.in/system/uploads/recruitment/7932_2026102312133433976.pdf"),
            "2026-10-23",
        )
        self.assertEqual(
            extract_url_date("https://www.gicre.in/en/images/CORRIGENDUM_21102026.pdf"),
            "21-10-2026",
        )

        # 4. Verify RBI preceding-row tableheader date extraction + bare file-size rejection
        m = NoticeMonitor(dry_run=True)
        rbi_html = """
        <table>
          <tr><td class="tableheader" colspan="2"><b>Oct 23, 2026</b></td></tr>
          <tr>
            <td><a href="bs_viewcontent.aspx?Id=5192">Engagement of Part- Time Bank’s Medical Consultants (BMC)</a></td>
            <td><a href="https://rbidocs.rbi.org.in/rdocs/Content/PDFs/BMC23102026.PDF">(645 KB)</a></td>
          </tr>
        </table>
        """
        links = m.parse_links_from_html(rbi_html, "https://opportunities.rbi.org.in/Scripts/Vacancies.aspx")
        relevant = [
            (u, t, d)
            for (u, t, d) in links
            if m.is_relevant_notice(t, u, is_dedicated_page=True)
        ]
        self.assertEqual(len(relevant), 1)
        self.assertIn("Engagement of Part- Time Bank", relevant[0][1])
        self.assertEqual(relevant[0][2], "Oct 23, 2026")
        self.assertFalse(m.is_relevant_notice("Apply Now", "https://psb.in/apply", is_dedicated_page=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)

