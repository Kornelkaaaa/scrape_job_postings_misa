"""Phase 2 tests: hackathon/conference adapters, type-aware filters,
and past-event exclusion in the newsletter."""
from datetime import date, timedelta

from pipeline.adapters import confstech, devpost, mlh
from pipeline.config import Config
from pipeline.db import connect, insert_new, list_since
from pipeline.filters import filter_relevant
from pipeline.models import Opportunity
from pipeline.newsletter import render_markdown


def test_devpost_parse(fixture, make_source):
    source = make_source(name="Devpost", type="devpost", opportunity_type="hackathon")
    opportunities = devpost.parse(source, fixture("devpost.json"))

    assert len(opportunities) == 2
    first = opportunities[0]
    assert first.opportunity_type == "hackathon"
    assert first.title == "AI for Good Hackathon"
    assert first.location == "Online"
    assert first.posted_date == "2026-08-17"  # END of the range = deadline
    assert first.tags == ["Free", "Machine Learning/AI", "Education"]
    # single-date format and missing org fall back sensibly
    assert opportunities[1].posted_date == "2026-09-12"
    assert opportunities[1].org == "Devpost"


def test_mlh_parse(fixture, make_source):
    source = make_source(name="MLH", type="mlh", opportunity_type="hackathon")
    opportunities = mlh.parse(source, fixture("mlh.html"))

    assert len(opportunities) == 2  # the title-less card is skipped
    in_person, online = opportunities
    assert in_person.title == "HackWV"
    assert in_person.location == "Morgantown, West Virginia, US"
    assert in_person.url == "https://hackwv.example.com/"  # microdata url, no utm
    assert in_person.posted_date == "2026-10-03"
    assert in_person.tags == ["Free", "In-Person"]  # isAccessibleForFree=true
    assert online.location == "Digital"
    assert online.tags == ["Digital"]  # no isAccessibleForFree meta -> no label


def test_json_api_flags_become_tags(make_source):
    from pipeline.adapters import json_api
    payload = {"events": [
        {"event": {"title": "Free Workshop", "localist_url": "https://e.com/1", "free": True}},
        {"event": {"title": "Paid Gala", "localist_url": "https://e.com/2", "free": False}},
    ]}
    source = make_source(
        name="Campus", opportunity_type="conference",
        options={"items_path": "events",
                 "fields": {"title": "event.title", "url": "event.localist_url"},
                 "flags": {"Free": "event.free"}},
    )
    free, paid = json_api.parse(source, payload)
    assert free.tags == ["Free"]
    assert paid.tags == []


def test_confstech_parse(fixture, make_source):
    source = make_source(name="Confs.tech", type="confstech",
                         opportunity_type="conference")
    opportunities = confstech.parse(source, fixture("confstech.json"), topic="data")

    assert len(opportunities) == 3  # nameless entry skipped
    in_person, online, hybrid = opportunities
    assert in_person.title == "Data Council East"
    assert in_person.location == "Washington, DC, U.S.A."
    assert in_person.posted_date == "2026-10-14"
    assert in_person.tags == ["data"]
    assert online.location == "Online"
    assert online.org == "confs.tech"
    # hybrid (online=true + city) keeps the city, so far-away hybrids filter out
    assert hybrid.location == "Berlin, Germany"


def test_global_job_filters_do_not_apply_to_events(make_source):
    config = Config(sources=[], include_keywords=["intern", "analyst"],
                    exclude_keywords=["senior"],
                    include_locations=["west virginia", "remote"])
    hackathon_source = make_source(name="Devpost", type="devpost",
                                   opportunity_type="hackathon")
    events = [Opportunity(opportunity_type="hackathon", source="Devpost",
                          title="HackWV 2026", location="Tokyo, Japan")]
    # no job keyword in the title, foreign location - still kept, because
    # global filters are job-only
    assert filter_relevant(events, config, hackathon_source) == events

    job_source = make_source(name="Board", opportunity_type="job")
    jobs = [Opportunity(opportunity_type="job", source="Board",
                        title="HackWV 2026", location="Tokyo, Japan")]
    assert filter_relevant(jobs, config, job_source) == []  # jobs still filtered


def test_hackathon_categories_independent_of_job_categories(tmp_path):
    conn = connect(tmp_path / "test.db")
    future = (date.today() + timedelta(days=30)).isoformat()
    insert_new(conn, [
        Opportunity(opportunity_type="hackathon", source="MLH", title="HackWV",
                    url="https://e.com/1", posted_date=future, tags=["In-Person"]),
        Opportunity(opportunity_type="hackathon", source="Devpost", title="Agent Jam",
                    url="https://e.com/2", posted_date=future, tags=["Machine Learning/AI"]),
        Opportunity(opportunity_type="job", source="T", title="AI Analyst Intern",
                    url="https://e.com/3"),
    ])
    rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    md = render_markdown(
        rows, "7d",
        categories={"🤖 AI Jobs": ["ai"]},
        hackathon_categories={"🏟 In-Person": ["in-person"], "🤖 AI Hacks": ["machine learning/ai"]},
    )

    # each type is grouped by its OWN scheme
    assert md.index("### 🏟 In-Person") < md.index("HackWV")
    assert md.index("### 🤖 AI Hacks") < md.index("Agent Jam")
    assert "### 🤖 AI Jobs" in md
    # job categories never appear inside the hackathon section and vice versa
    assert "AI Jobs" not in md[md.index("## 🚀"):]
    conn.close()


def test_newsletter_drops_past_events_keeps_past_jobs(tmp_path):
    conn = connect(tmp_path / "test.db")
    past = (date.today() - timedelta(days=3)).isoformat()
    soon = (date.today() + timedelta(days=3)).isoformat()     # < 5-day lead
    future = (date.today() + timedelta(days=30)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    insert_new(conn, [
        Opportunity(opportunity_type="hackathon", source="T", title="Ended Hackathon",
                    url="https://e.com/1", posted_date=past),
        Opportunity(opportunity_type="hackathon", source="T", title="Deadline Too Soon Hackathon",
                    url="https://e.com/5", posted_date=soon),
        Opportunity(opportunity_type="hackathon", source="T", title="Upcoming Hackathon",
                    url="https://e.com/2", posted_date=future),
        Opportunity(opportunity_type="hackathon", source="T", title="Undated Hackathon",
                    url="https://e.com/3"),
        Opportunity(opportunity_type="conference", source="T", title="Conference Tomorrow",
                    url="https://e.com/6", posted_date=tomorrow),
        Opportunity(opportunity_type="job", source="T", title="Old Job Posting",
                    url="https://e.com/4", posted_date=past),
    ])
    rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    md = render_markdown(rows, "7d")

    assert "Ended Hackathon" not in md
    assert "Deadline Too Soon Hackathon" not in md  # < 5 days of runway
    assert "Upcoming Hackathon" in md
    assert "Undated Hackathon" in md     # no date -> benefit of the doubt
    assert "Conference Tomorrow" in md   # conferences have no lead requirement
    assert "Old Job Posting" in md       # jobs never expire by posted_date
    conn.close()
