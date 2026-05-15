def test_public_trust_and_proof_pages_are_served(client):
    pages = {
        "/trust-center": "Trust Center",
        "/proof-assets": "Procta Proof Assets",
        "/sample-scorecard": "Sample Exam Scorecard",
        "/dpa": "Data Processing Addendum",
        "/privacy-policy": "Privacy Policy",
        "/security-questionnaire": "Security Questionnaire",
    }

    for path, expected in pages.items():
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert expected in resp.text


def test_robots_allows_public_proof_pages(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Allow: /trust-center" in resp.text
    assert "Allow: /proof-assets" in resp.text
    assert "Allow: /sample-scorecard" in resp.text
