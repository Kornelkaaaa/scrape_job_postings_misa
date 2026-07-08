from pipeline.adapters import (adzuna, greenhouse, html_page, json_api, lever,
                               rss, usajobs, workday)


def test_greenhouse_parse(fixture, make_source):
    source = make_source(name="Acme", type="greenhouse", org="Acme",
                         options={"board_token": "acme"})
    opportunities = greenhouse.parse(source, fixture("greenhouse.json"))

    assert len(opportunities) == 2
    first = opportunities[0]
    assert first.title == "Business Analyst Intern"
    assert first.org == "Acme"
    assert first.location == "Toronto, ON"
    assert first.posted_date == "2026-07-01"
    assert first.tags == ["Operations"]
    assert "greenhouse.io/acme/jobs/101" in first.url


def test_lever_parse(fixture, make_source):
    source = make_source(name="Acme", type="lever", org="Acme",
                         options={"company": "acme"})
    opportunities = lever.parse(source, fixture("lever.json"))

    assert len(opportunities) == 2
    first = opportunities[0]
    assert first.title == "AI Consultant (Junior)"
    assert first.location == "London, UK"
    assert first.posted_date == "2026-07-01"  # epoch millis 1782864000000
    assert "Consulting" in first.tags


def test_json_api_parse_skips_metadata_item(fixture, make_source):
    source = make_source(
        name="RemoteOK",
        options={
            "items_path": "",
            "fields": {
                "title": "position", "org": "company", "location": "location",
                "url": "url", "description": "description",
                "posted_date": "date", "tags": "tags",
            },
        },
    )
    opportunities = json_api.parse(source, fixture("remoteok.json"))

    assert len(opportunities) == 1  # legal-notice element skipped (no title)
    assert opportunities[0].title == "Junior Data Analyst"
    assert opportunities[0].org == "RemoteCo"
    assert opportunities[0].posted_date == "2026-07-02"
    assert opportunities[0].tags == ["data", "analyst", "sql"]


def test_json_api_unmapped_fields_fall_back_cleanly(make_source):
    # regression: unmapped fields (empty dot-path) must not dump the whole item
    payload = [{"title": "AI Engineer", "jobUrl": "https://jobs.example.com/1",
                "id": "x", "department": "Eng", "descriptionHtml": "<p>...</p>"}]
    source = make_source(name="Ashby", org="OpenAI",
                         options={"items_path": "", "fields": {"title": "title", "url": "jobUrl"}})
    opp = json_api.parse(source, payload)[0]

    assert opp.org == "OpenAI"          # source org, not str(item)
    assert opp.location == ""
    assert opp.description == ""
    assert opp.posted_date is None


def test_rss_parse(fixture, make_source):
    source = make_source(name="Feed", type="rss")
    opportunities = rss.parse(source, fixture("feed.rss"))

    assert len(opportunities) == 2
    assert opportunities[0].title == "Graduate Business Analyst"
    assert opportunities[0].org == "Example Org"
    assert opportunities[0].posted_date == "2026-07-01"
    assert opportunities[0].location == "USA Only"  # WWR-style <region> field
    assert opportunities[1].org == "Feed"  # falls back to source name


def test_html_parse(fixture, make_source):
    source = make_source(
        name="Example Careers", type="html", org="Example Company",
        url="https://example.com/careers",
        options={"selectors": {
            "item": ".job-listing", "title": ".job-title", "link": "a",
            "location": ".job-location", "date": ".job-date", "tags": ".job-tags",
        }},
    )
    opportunities = html_page.parse(source, fixture("careers.html"))

    assert len(opportunities) == 2  # card without a title is dropped
    first = opportunities[0]
    assert first.title == "Business Analyst Intern"
    assert first.url == "https://example.com/careers/ba-intern-2026"  # relative href resolved
    assert first.posted_date == "2026-06-30"
    assert first.tags == ["consulting", "analytics"]
    assert opportunities[1].url == "https://example.com/careers/consultant-junior"


def test_workday_parse(fixture, make_source):
    source = make_source(
        name="WVU Medicine", type="workday", org="WVU Medicine",
        options={"host": "wvumedicine.wd1.myworkdayjobs.com",
                 "tenant": "wvumedicine", "site": "WVUH"},
    )
    opportunities = workday.parse(source, fixture("workday.json"))

    assert len(opportunities) == 2
    first = opportunities[0]
    assert first.title == "Business Systems Analyst"
    assert first.org == "WVU Medicine"
    assert first.location == "Morgantown, WV"
    assert first.url == ("https://wvumedicine.wd1.myworkdayjobs.com/en-US/WVUH"
                         "/job/Morgantown-WV/Business-Systems-Analyst_JR26-12345")
    assert first.posted_date is None  # Workday only reports relative ages


def test_usajobs_parse(fixture, make_source):
    source = make_source(name="USAJobs WV", type="usajobs",
                         options={"keyword": "analyst", "location_name": "West Virginia"})
    opportunities = usajobs.parse(source, fixture("usajobs.json"))

    assert len(opportunities) == 2
    first = opportunities[0]
    assert first.title == "Management and Program Analyst"
    assert first.org == "Federal Bureau of Investigation"
    assert first.location == "Clarksburg, West Virginia"
    assert first.posted_date == "2026-07-01"
    assert first.tags == ["Management And Program Analysis"]
    assert opportunities[1].posted_date == "2026-07-03"


def test_usajobs_fetch_skips_without_credentials(make_source, monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("USAJOBS_EMAIL", raising=False)
    source = make_source(name="USAJobs", type="usajobs", options={"keyword": "analyst"})

    assert usajobs.fetch(source, client=None) == []


def test_adzuna_dedupes_same_job_across_ad_ids(make_source):
    payload = {"results": [
        {"title": "AI Compliance Analyst", "company": {"display_name": "Acme"},
         "location": {"display_name": "Charleston, West Virginia"},
         "redirect_url": "https://www.adzuna.com/land/ad/111?se=aaa&utm_medium=api&utm_source=secret_app_id&v=E46",
         "created": "2026-07-01T00:00:00Z"},
        {"title": "AI Compliance Analyst", "company": {"display_name": "Acme"},
         "location": {"display_name": "Charleston, West Virginia"},
         "redirect_url": "https://www.adzuna.com/land/ad/222?se=bbb&v=A88",
         "created": "2026-07-02T00:00:00Z"},
    ]}
    source = make_source(name="Adzuna", type="adzuna", options={})
    opportunities = adzuna.parse(source, payload)

    assert opportunities[0].dedupe_key == opportunities[1].dedupe_key
    # utm_* (incl. app_id) stripped, but the se redirect token must survive
    assert "se=aaa" in opportunities[0].url
    assert "utm" not in opportunities[0].url


def test_adzuna_fetch_skips_without_credentials(make_source, monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    source = make_source(name="Adzuna", type="adzuna", options={"what": "analyst"})

    assert adzuna.fetch(source, client=None) == []
