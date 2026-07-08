from pipeline.db import connect, insert_new, list_since
from pipeline.models import Opportunity
from pipeline.newsletter import (categorize, is_career_fair_org, render_html,
                                 render_markdown)

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


def test_career_fair_section_in_markdown(tmp_path):
    rows = seed(tmp_path)
    md = render_markdown(rows, "7d", career_fair_orgs=["Leidos"])

    fair_pos = md.index("WVU Career Fair Employers")
    jobs_pos = md.index("Jobs & Internships")
    assert fair_pos < jobs_pos                       # fair section comes first
    assert md.index("Business Analyst") < jobs_pos   # Leidos job in fair section
    assert md.index("AI Analyst Intern") > jobs_pos  # Stripe job stays in jobs


def test_no_fair_section_when_list_empty(tmp_path):
    rows = seed(tmp_path)
    assert "Career Fair" not in render_markdown(rows, "7d")
    assert "Career Fair" not in render_html(rows, "7d", career_fair_orgs=[])


def test_categorize_first_match_wins(tmp_path):
    rows = seed(tmp_path)  # "Business Analyst" (Leidos), "AI Analyst Intern" (Stripe)
    by_title = {r["title"]: r for r in rows}
    assert categorize(by_title["AI Analyst Intern"], CATEGORIES) == "🤖 AI & Machine Learning"
    assert categorize(by_title["Business Analyst"], CATEGORIES) == "💼 Business & Consulting"


def test_unmatched_job_lands_in_other(tmp_path):
    rows = seed(tmp_path)
    assert categorize(rows[0], {"🤖 AI": ["quantum"]}) == "✨ Other"


def test_markdown_has_category_subsections(tmp_path):
    rows = seed(tmp_path)
    md = render_markdown(rows, "7d", categories=CATEGORIES)
    assert "### 🤖 AI & Machine Learning" in md
    assert "### 💼 Business & Consulting" in md
    assert "### 📊 Data & Analytics" not in md  # empty categories are dropped
    # AI job listed under the AI subsection
    assert md.index("### 🤖") < md.index("AI Analyst Intern") < md.index("### 💼")


def test_html_has_category_subsections(tmp_path):
    rows = seed(tmp_path)
    page = render_html(rows, "7d", categories=CATEGORIES)
    assert "🤖 AI &amp; Machine Learning" in page
    assert "✨ Other" not in page


def test_career_fair_section_in_html(tmp_path):
    rows = seed(tmp_path)
    page = render_html(rows, "7d", career_fair_orgs=["Leidos"])
    assert "WVU Career Fair Employers" in page
    assert page.index("Business Analyst") < page.index("AI Analyst Intern")
