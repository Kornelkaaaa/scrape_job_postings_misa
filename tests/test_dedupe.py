from pipeline.db import connect, insert_new, list_since
from pipeline.models import Opportunity, normalize_url


def make_opp(**overrides) -> Opportunity:
    defaults = dict(
        opportunity_type="job", source="Test", title="Business Analyst Intern",
        org="Acme", url="https://example.com/jobs/1", posted_date="2026-07-01",
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


def test_normalize_url_strips_tracking_and_trailing_slash():
    a = normalize_url("http://Example.com/jobs/1/?utm_source=newsletter&gclid=xyz")
    b = normalize_url("https://example.com/jobs/1")
    assert a == b


def test_same_run_twice_inserts_nothing_new(tmp_path):
    conn = connect(tmp_path / "test.db")
    batch = [make_opp(), make_opp(title="Junior Consultant", url="https://example.com/jobs/2")]

    assert insert_new(conn, batch) == 2
    assert insert_new(conn, batch) == 0  # rerun: everything already known
    conn.close()


def test_url_variants_dedupe_to_one_row(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert insert_new(conn, [make_opp(url="https://example.com/jobs/1?utm_source=a")]) == 1
    assert insert_new(conn, [make_opp(url="http://EXAMPLE.com/jobs/1/")]) == 0
    conn.close()


def test_fallback_hash_dedupe_when_no_url(tmp_path):
    conn = connect(tmp_path / "test.db")
    assert insert_new(conn, [make_opp(url="")]) == 1
    assert insert_new(conn, [make_opp(url="")]) == 0            # identical -> dupe
    assert insert_new(conn, [make_opp(url="", org="Other")]) == 1  # different org -> new
    conn.close()


def test_list_since_filters_by_type(tmp_path):
    conn = connect(tmp_path / "test.db")
    insert_new(conn, [
        make_opp(),
        make_opp(opportunity_type="hackathon", title="MISA Hacks",
                 url="https://example.com/hack"),
    ])
    all_rows = list_since(conn, "2000-01-01T00:00:00+00:00")
    jobs = list_since(conn, "2000-01-01T00:00:00+00:00", opportunity_type="job")
    assert len(all_rows) == 2
    assert len(jobs) == 1 and jobs[0]["opportunity_type"] == "job"
    conn.close()
