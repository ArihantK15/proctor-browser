"""Regression tests for the InputValidationMiddleware SQLi screen.

The patterns must catch classic injection shapes WITHOUT false-positiving
on legitimate exam content: a blocked request on /save-answer is silent
answer loss for the student. These tests pin both sides of that contract
(the original patterns blocked "'apples' and 'oranges'", code comments,
and trailing "--").
"""
from app.main import _looks_malicious, _json_contains_malicious


class TestLegitimateContentNotBlocked:
    """Real student/teacher content that previously false-positived."""

    def test_quoted_prose_with_and(self):
        assert not _looks_malicious("I like 'apples' and 'oranges'")

    def test_quoted_prose_with_or(self):
        assert not _looks_malicious("The answer is 'yes' or 'no' depending")

    def test_code_comment_in_answer(self):
        assert not _looks_malicious("int x = 5; /* loop counter */")

    def test_decrement_operator(self):
        assert not _looks_malicious("for(i=n; i>0; i--)")

    def test_trailing_double_hyphen(self):
        # base64url (LTI id_token segments) can legitimately end in "--"
        assert not _looks_malicious("eyJhbGc.payload--")

    def test_sql_keywords_in_essay(self):
        assert not _looks_malicious("UPDATE and DELETE are SQL statements")


class TestInjectionShapesBlocked:
    def test_classic_tautology_quoted(self):
        assert _looks_malicious("' OR '1'='1")

    def test_tautology_unquoted(self):
        assert _looks_malicious("' OR 1=1--")

    def test_and_tautology(self):
        assert _looks_malicious("admin' AND password='x")

    def test_stacked_drop_table(self):
        assert _looks_malicious("'; DROP TABLE users; --")

    def test_stacked_delete_from(self):
        assert _looks_malicious("1; DELETE FROM exam_sessions WHERE 1=1")

    def test_stacked_insert_into(self):
        assert _looks_malicious("x'; INSERT INTO teachers VALUES('h')")

    def test_stacked_update_set(self):
        assert _looks_malicious("0; UPDATE teachers SET org_role='admin'")


class TestJsonRecursion:
    def test_nested_attack_found(self):
        body = {"answers": [{"text": "fine"}, {"text": "' OR '1'='1"}]}
        assert _json_contains_malicious(body)

    def test_nested_legit_clean(self):
        body = {"answers": [{"text": "'apples' and 'oranges'"},
                            {"code": "x /* comment */ --"}]}
        assert not _json_contains_malicious(body)
