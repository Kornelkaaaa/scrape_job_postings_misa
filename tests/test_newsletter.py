from pipeline.db import connect, insert_new, list_since
from pipeline.models import Opportunity
from pipeline.newsletter import is_career_fair_org, render_html, render_markdown


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


def test_career_fair_section_in_html(tmp_path):
    rows = seed(tmp_path)
    page = render_html(rows, "7d", career_fair_orgs=["Leidos"])
    assert "WVU Career Fair Employers" in page
    assert page.index("Business Analyst") < page.index("AI Analyst Intern")
