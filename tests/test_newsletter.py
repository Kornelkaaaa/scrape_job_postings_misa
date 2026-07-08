from datetime import date, timedelta

from pipeline.db import connect, insert_new, list_since
from pipeline.models import Event, Opportunity
from pipeline.newsletter import (categorize, is_career_fair_org, render_full_html,
                                 render_full_markdown, render_html, render_markdown,
                                 write_newsletter)

CATEGORIES = {
    "🤖 AI & Machine Learning": ["ai", "machine learning"],
    "📊 Data & Analytics": ["data analyst", "analytics"],
    "💼 Business & Consulting": ["business analyst", "consulting"],
}


def seed(tmp_path):
    conn = connect(tmp_path / "test.db")
    insert_new(conn, [
        Opportunity(opportunity_type="job", source="Adzuna", title="Business Analyst",
                    org="Leidos Inc.", location="Morgantown, WV",
                    url="https://example.com/1"),
        Opportunity(opportunity_type="job", source="Stripe", title="AI Analyst Intern",
                    org="Stripe", location="Remote", url="https://example.com/2"),
    ])
    return list_since(conn, "2000-01-01T00:00:00+00:00")


def seed_jobs(tmp_path, n):
    conn = connect(tmp_path / "test.db")
    insert_new(conn, [
        Opportunity(opportunity_type="job", source="Adzuna", title=f"Analyst {i}",
                    org="Acme", location="Remote", url=f"https://example.com/{i}",
                    posted_date=(date(2026, 1, 1) + timedelta(days=i)).isoformat())
        for i in range(n)
    ])
    return list_since(conn, "2000-01-01T00:00:00+00:00")


def test_is_career_fair_org_partial_match():
    assert is_career_fair_org("Leidos Inc.", ["Leidos"])
    assert is_career_fair_org("Leidos", ["Leidos Inc."])
    assert not is_career_fair_org("Stripe", ["Leidos"])
    assert not is_career_fair_org("", ["Leidos"])


def test_short_names_match_whole_words_only():
    assert is_career_fair_org("EY", ["EY"])
    assert is_career_fair_org("EY LLP", ["EY"])
    assert not is_career_fair_org("Keyence", ["EY"])
    assert not is_career_fair_org("Harvey Industries", ["EY"])
    assert is_career_fair_org("Federal Bureau of Investigation", ["Federal Bureau of Investigation"])


def test_categorize_first_match_wins(tmp_path):
    rows = seed(tmp_path)  # "Business Analyst" (Leidos), "AI Analyst Intern" (Stripe)
    by_title = {r["title"]: r for r in rows}
    assert categorize(by_title["AI Analyst Intern"], CATEGORIES) == "🤖 AI & Machine Learning"
    assert categorize(by_title["Business Analyst"], CATEGORIES) == "💼 Business & Consulting"


def test_unmatched_job_lands_in_other(tmp_path):
    rows = seed(tmp_path)
    assert categorize(rows[0], {"🤖 AI": ["quantum"]}) == "✨ Other"


# --------------------------------------------------------------------------
# Teaser (render_markdown / render_html) - the short version that's actually
# sent. No jump-to nav, no pagination: big groups truncate with a link out.
# --------------------------------------------------------------------------

def test_teaser_has_no_jump_to_nav_or_pager(tmp_path):
    rows = seed_jobs(tmp_path, 20)
    md = render_markdown(rows, "7d")
    assert "Jump to" not in md
    assert "Page 1 of" not in md

    page = render_html(rows, "7d")
    assert "Jump to" not in page
    assert "Page 1 of" not in page


def test_teaser_shows_everything_under_the_limit(tmp_path):
    rows = seed_jobs(tmp_path, 3)
    md = render_markdown(rows, "7d")
    assert "Analyst 0" in md and "Analyst 1" in md and "Analyst 2" in md
    assert "See all" not in md and "more." not in md


def test_teaser_dedupes_same_posting_cross_listed_across_counties(tmp_path):
    conn = connect(tmp_path / "test.db")
    counties = ["Charleston", "Parkersburg", "Morgantown", "Wheeling", "Huntington"]
    insert_new(conn, [
        Opportunity(opportunity_type="job", source="Adzuna", title="DTCC Early Access",
                    org="DTCC", location=county, url=f"https://example.com/dtcc-{county}",
                    posted_date="2026-07-08")
        for county in counties
    ] + [
        Opportunity(opportunity_type="job", source="Adzuna", title="Distinct Role",
                    org="Acme", location="Remote", url="https://example.com/distinct",
                    posted_date="2026-07-07"),
    ])
    rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    md = render_markdown(rows, "7d")
    # the 5 cross-posted DTCC listings collapse to one visible slot, leaving
    # room for the other posting instead of burning 5 of the 8 teaser slots
    assert md.count("DTCC Early Access") == 1
    assert "Distinct Role" in md


def test_teaser_truncates_and_links_to_full_list(tmp_path):
    rows = seed_jobs(tmp_path, 12)  # TEASER_LIMIT is 8
    md = render_markdown(rows, "7d", full_list_href="full.md")
    assert "💼 Jobs & Internships (12)" in md
    assert "[See all 12 →](full.md#jobs)" in md
    # newest-first: Analyst 11 (latest posted_date) is visible, Analyst 0 (oldest) is cut
    assert "Analyst 11" in md
    assert "Analyst 0" not in md

    page = render_html(rows, "7d", full_list_href="full.html")
    assert "See all 12" in page and 'href="full.html#jobs"' in page
    assert "Analyst 11" in page
    assert "Analyst 0" not in page


def test_teaser_truncation_without_full_list_href_has_no_link(tmp_path):
    rows = seed_jobs(tmp_path, 12)
    md = render_markdown(rows, "7d")
    assert "...and 4 more." in md
    assert "[See all" not in md

    page = render_html(rows, "7d")
    assert "...and 4 more." in page
    assert "See all" not in page


def test_teaser_career_fair_and_fair_precedence(tmp_path):
    rows = seed(tmp_path)
    md = render_markdown(rows, "7d", career_fair_orgs=["Leidos"])
    fair_pos = md.index("🎓 WVU Career Fair Employers")
    jobs_pos = md.index("💼 Jobs & Internships")
    assert fair_pos < jobs_pos
    assert fair_pos < md.index("Business Analyst") < jobs_pos
    assert md.index("AI Analyst Intern") > jobs_pos

    page = render_html(rows, "7d", career_fair_orgs=["Leidos"])
    assert "WVU Career Fair Employers" in page
    assert page.index("Business Analyst") < page.index("AI Analyst Intern")


def test_teaser_no_fair_section_when_list_empty(tmp_path):
    rows = seed(tmp_path)
    assert "Career Fair" not in render_markdown(rows, "7d")
    assert "Career Fair" not in render_html(rows, "7d", career_fair_orgs=[])


def test_teaser_intro_line_appears(tmp_path):
    rows = seed(tmp_path)
    intro = "Welcome back, Mountaineers!"
    assert intro in render_markdown(rows, "7d", intro=intro)
    assert intro in render_html(rows, "7d", intro=intro)


def test_teaser_no_intro_when_blank(tmp_path):
    rows = seed(tmp_path)
    assert "Welcome" not in render_markdown(rows, "7d")
    assert "Welcome" not in render_html(rows, "7d")


def test_teaser_upcoming_events_render_and_past_events_are_dropped(tmp_path):
    rows = seed(tmp_path)
    events = [
        Event(title="Future Meeting", date=(date.today() + timedelta(days=7)).isoformat()),
        Event(title="Past Meeting", date=(date.today() - timedelta(days=7)).isoformat()),
        Event(title="Undated Meeting"),
    ]
    md = render_markdown(rows, "7d", events=events)
    assert "Upcoming MISA Events" in md
    assert "Future Meeting" in md
    assert "Undated Meeting" in md
    assert "Past Meeting" not in md

    page = render_html(rows, "7d", events=events)
    assert "Upcoming MISA Events" in page
    assert "Future Meeting" in page
    assert "Undated Meeting" in page
    assert "Past Meeting" not in page


def test_teaser_no_events_section_when_empty(tmp_path):
    rows = seed(tmp_path)
    assert "Upcoming MISA Events" not in render_markdown(rows, "7d")
    assert "Upcoming MISA Events" not in render_html(rows, "7d", events=[])


def test_teaser_social_footer_links_only_for_configured_keys(tmp_path):
    rows = seed(tmp_path)
    social = {"instagram": "https://instagram.com/wvumisa", "linkedin": ""}
    md = render_markdown(rows, "7d", social=social)
    assert "Instagram" in md
    assert "LinkedIn" not in md

    page = render_html(rows, "7d", social=social)
    assert "Instagram" in page
    assert "LinkedIn" not in page


def test_teaser_no_social_footer_when_empty(tmp_path):
    rows = seed(tmp_path)
    assert "instagram.com" not in render_markdown(rows, "7d")
    assert "instagram.com" not in render_html(rows, "7d")


def test_teaser_html_is_responsive(tmp_path):
    rows = seed(tmp_path)
    page = render_html(rows, "7d")
    assert "@media" in page and "max-width:600px" in page
    assert 'name="viewport"' in page


def seed_internship_and_job(tmp_path):
    conn = connect(tmp_path / "test.db")
    insert_new(conn, [
        Opportunity(opportunity_type="job", source="T", title="Business Analyst Intern",
                    org="Acme", url="https://e.com/1"),
        Opportunity(opportunity_type="job", source="T", title="Junior Business Analyst",
                    org="Acme", url="https://e.com/2"),
    ])
    return list_since(conn, "2000-01-01T00:00:00+00:00")


def test_teaser_internships_get_their_own_section(tmp_path):
    rows = seed_internship_and_job(tmp_path)
    md = render_markdown(rows, "7d", internship_keywords=["intern", "internship"])

    interns_pos = md.index("## 🎯 Internships & Co-ops")
    jobs_pos = md.index("## 💼 Jobs")  # renamed from "& Internships" after the split
    assert interns_pos < jobs_pos
    assert interns_pos < md.index("Business Analyst Intern") < jobs_pos
    assert md.index("Junior Business Analyst") > jobs_pos

    page = render_html(rows, "7d", internship_keywords=["intern", "internship"])
    assert page.index("Internships &amp; Co-ops") < page.index("💼 Jobs (")

    # without the keywords everything stays in one Jobs & Internships section
    md_plain = render_markdown(rows, "7d")
    assert "Internships &amp; Co-ops" not in md_plain
    assert "Internships & Co-ops" not in md_plain


# --------------------------------------------------------------------------
# Archive (render_full_markdown / render_full_html) - everything, with the
# jump-to panel, category subsections, and Jobs & Internships pagination.
# --------------------------------------------------------------------------

def test_full_internships_get_their_own_section(tmp_path):
    rows = seed_internship_and_job(tmp_path)
    md = render_full_markdown(rows, "7d", internship_keywords=["intern", "internship"])

    interns_pos = md.index("## 🎯 Internships & Co-ops")
    jobs_pos = md.index("## 💼 Jobs")  # renamed from "& Internships" after the split
    assert interns_pos < jobs_pos
    assert interns_pos < md.index("Business Analyst Intern") < jobs_pos
    assert md.index("Junior Business Analyst") > jobs_pos
    assert "(#internships)" in md  # present in the jump-to nav

    # without the keywords everything stays in one Jobs & Internships section
    md_plain = render_full_markdown(rows, "7d")
    assert "Internships & Co-ops" not in md_plain


def test_full_career_fair_section_in_markdown(tmp_path):
    rows = seed(tmp_path)
    md = render_full_markdown(rows, "7d", career_fair_orgs=["Leidos"])

    # "## " prefix skips past the jump-to navigation, which also names sections
    fair_pos = md.index("## 🎓 WVU Career Fair Employers")
    jobs_pos = md.index("## 💼 Jobs & Internships")
    assert fair_pos < jobs_pos                       # fair section comes first
    assert fair_pos < md.index("Business Analyst") < jobs_pos   # Leidos job in fair section
    assert md.index("AI Analyst Intern") > jobs_pos  # Stripe job stays in jobs
    # navigation panel present with per-section counts and anchors
    assert "**Jump to:**" in md
    assert "(#career-fair)" in md and "(#jobs)" in md
    assert '<a id="career-fair"></a>' in md


def test_full_no_fair_section_when_list_empty(tmp_path):
    rows = seed(tmp_path)
    assert "Career Fair" not in render_full_markdown(rows, "7d")
    assert "Career Fair" not in render_full_html(rows, "7d", career_fair_orgs=[])


def test_full_markdown_has_category_subsections(tmp_path):
    rows = seed(tmp_path)
    md = render_full_markdown(rows, "7d", categories=CATEGORIES)
    assert "### 🤖 AI & Machine Learning" in md
    assert "### 💼 Business & Consulting" in md
    assert "### 📊 Data & Analytics" not in md  # empty categories are dropped
    # AI job listed under the AI subsection
    assert md.index("### 🤖") < md.index("AI Analyst Intern") < md.index("### 💼")


def test_full_html_has_category_subsections(tmp_path):
    rows = seed(tmp_path)
    page = render_full_html(rows, "7d", categories=CATEGORIES)
    assert "🤖 AI &amp; Machine Learning" in page
    assert "✨ Other" not in page


def test_full_career_fair_section_in_html(tmp_path):
    rows = seed(tmp_path)
    page = render_full_html(rows, "7d", career_fair_orgs=["Leidos"])
    assert "WVU Career Fair Employers" in page
    assert page.index("Business Analyst") < page.index("AI Analyst Intern")


def test_full_no_pager_when_under_page_size(tmp_path):
    rows = seed_jobs(tmp_path, 3)
    md = render_full_markdown(rows, "7d", job_page_size=5)
    assert "Page 1 of" not in md
    page = render_full_html(rows, "7d", job_page_size=5)
    assert "Page 1 of" not in page


def test_full_jobs_paginate_when_over_page_size(tmp_path):
    rows = seed_jobs(tmp_path, 7)
    md = render_full_markdown(rows, "7d", job_page_size=3)
    assert "Page 1 of 3" in md
    assert '<a id="jobs-p1"></a>' in md
    assert '<a id="jobs-p2"></a>' in md
    assert "[Next ▶](#jobs-p2)" in md
    assert "Analyst 0" in md and "Analyst 6" in md  # every job still present, just paged

    page = render_full_html(rows, "7d", job_page_size=3)
    assert "Page 1 of 3" in page
    assert 'id="jobs-p1"' in page and 'id="jobs-p2"' in page
    assert "#jobs-p2" in page
    assert "Analyst 0" in page and "Analyst 6" in page


def test_full_non_job_sections_never_paginate(tmp_path):
    conn = connect(tmp_path / "test.db")
    insert_new(conn, [
        Opportunity(opportunity_type="hackathon", source="MLH", title=f"Hack {i}",
                    org="MLH", url=f"https://example.com/hack{i}",
                    posted_date=(date.today() + timedelta(days=30)).isoformat())
        for i in range(7)
    ])
    rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    md = render_full_markdown(rows, "7d", job_page_size=3)
    assert "Page 1 of" not in md


# --------------------------------------------------------------------------
# write_newsletter - both file pairs land on disk and cross-link correctly.
# --------------------------------------------------------------------------

def test_write_newsletter_creates_teaser_and_full_files(tmp_path):
    rows = seed_jobs(tmp_path / "db", 12)
    md_path, html_path = write_newsletter(rows, tmp_path / "out", "7d")

    assert md_path.exists() and html_path.exists()
    full_md_path = tmp_path / "out" / f"{md_path.stem}_full.md"
    full_html_path = tmp_path / "out" / f"{html_path.stem}_full.html"
    assert full_md_path.exists() and full_html_path.exists()

    # teaser's "See all" points at the full file sitting next to it
    assert full_html_path.name in html_path.read_text(encoding="utf-8")
    assert full_md_path.name in md_path.read_text(encoding="utf-8")
    # full page has the jump-to nav the teaser deliberately drops
    assert "Jump to" in full_html_path.read_text(encoding="utf-8")
    assert "Jump to" not in html_path.read_text(encoding="utf-8")


def test_write_newsletter_uses_archive_base_url_when_configured(tmp_path):
    rows = seed_jobs(tmp_path / "db", 12)
    md_path, html_path = write_newsletter(
        rows, tmp_path / "out", "7d",
        archive_base_url="https://kornelkaaaa.github.io/scrape_job_postings_misa",
    )
    full_html_name = f"{html_path.stem}_full.html"
    full_md_name = f"{md_path.stem}_full.md"
    assert (f"https://kornelkaaaa.github.io/scrape_job_postings_misa/{full_html_name}"
            in html_path.read_text(encoding="utf-8"))
    assert (f"https://kornelkaaaa.github.io/scrape_job_postings_misa/{full_md_name}"
            in md_path.read_text(encoding="utf-8"))
