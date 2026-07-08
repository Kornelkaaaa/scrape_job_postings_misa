"""Phase 2 tests: hackathon/conference adapters, type-aware filters,
and past-event exclusion in the newsletter."""
from datetime import date, timedelta

from pipeline.adapters import devpost, mlh
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
    assert first.tags == ["Machine Learning/AI", "Education"]
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
    assert in_person.tags == ["In-Person"]
    assert online.location == "Digital"
    assert online.tags == ["Digital"]


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


def test_newsletter_drops_past_events_keeps_past_jobs(tmp_path):
    conn = connect(tmp_path / "test.db")
    past = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()
    insert_new(conn, [
        Opportunity(opportunity_type="hackathon", source="T", title="Ended Hackathon",
                    url="https://e.com/1", posted_date=past),
        Opportunity(opportunity_type="hackathon", source="T", title="Upcoming Hackathon",
                    url="https://e.com/2", posted_date=future),
        Opportunity(opportunity_type="hackathon", source="T", title="Undated Hackathon",
                    url="https://e.com/3"),
        Opportunity(opportunity_type="job", source="T", title="Old Job Posting",
                    url="https://e.com/4", posted_date=past),
    ])
    rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    md = render_markdown(rows, "7d")

    assert "Ended Hackathon" not in md
    assert "Upcoming Hackathon" in md
    assert "Undated Hackathon" in md   # no date -> benefit of the doubt
    assert "Old Job Posting" in md     # jobs never expire by posted_date
    conn.close()
