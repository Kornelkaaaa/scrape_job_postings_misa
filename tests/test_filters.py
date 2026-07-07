from pipeline.config import Config
from pipeline.filters import filter_relevant
from pipeline.models import Opportunity


def make_opp(title: str, description: str = "", tags: list[str] | None = None) -> Opportunity:
    return Opportunity(opportunity_type="job", source="Test", title=title,
                       description=description, tags=tags or [])


def make_config(include=None, exclude=None) -> Config:
    return Config(sources=[], include_keywords=include or [], exclude_keywords=exclude or [])


def test_include_and_exclude(make_source):
    config = make_config(include=["intern", "analyst"], exclude=["senior"])
    opportunities = [
        make_opp("Business Analyst Intern"),
        make_opp("Senior Data Analyst"),
        make_opp("Head Chef"),
    ]
    kept = filter_relevant(opportunities, config, make_source())
    assert [o.title for o in kept] == ["Business Analyst Intern"]


def test_short_keyword_matches_whole_words_only(make_source):
    config = make_config(include=["ai"])
    kept = filter_relevant(
        [make_opp("AI Engineer Intern"), make_opp("Email Marketing Specialist")],
        config, make_source(),
    )
    assert [o.title for o in kept] == ["AI Engineer Intern"]


def test_source_keywords_override_globals(make_source):
    config = make_config(include=["intern"])
    source = make_source(include_keywords=["hackathon"])
    kept = filter_relevant(
        [make_opp("MISA Hackathon 2026"), make_opp("Summer Intern")],
        config, source,
    )
    assert [o.title for o in kept] == ["MISA Hackathon 2026"]


def test_empty_include_keeps_everything(make_source):
    config = make_config()
    opportunities = [make_opp("Anything At All")]
    assert filter_relevant(opportunities, config, make_source()) == opportunities


def test_matches_in_tags_but_not_description(make_source):
    config = make_config(include=["machine learning"])
    kept = filter_relevant(
        [
            make_opp("Research Position", tags=["machine learning"]),
            make_opp("Groundskeeper", description="mentions machine learning in passing"),
        ],
        config, make_source(),
    )
    assert [o.title for o in kept] == ["Research Position"]
