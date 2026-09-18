"""
Tests for author filtering in PRFilter.

Regression context: `PRFilter.authors` was declared as a dataclass field and
parsed off the wire by ParsePRFilterProcessor, but was never added to the
`conditions` map in PRFilter.filter_query. Because that map is indexed by
`self.__dict__.keys()`, setting `authors` raised KeyError: 'authors' and any
DORA endpoint given an author filter returned a 500.

These tests pin both the fix and the surrounding behaviour.
"""

from mhq.service.code.pr_filter import ParsePRFilterProcessor
from mhq.store.models.code.filter import PRFilter


def _sql(expression) -> str:
    return str(expression)


class TestPRFilterAuthors:
    def test_no_filters_produces_no_conditions(self):
        assert PRFilter().filter_query == []

    def test_authors_none_is_ignored(self):
        # The original bug was masked in this case: `getattr(...) is not None`
        # short-circuits before the missing dict key is read.
        assert PRFilter(authors=None).filter_query == []

    def test_authors_set_does_not_raise(self):
        # This is the regression. Before the fix, this raised KeyError.
        assert len(PRFilter(authors=["ada", "grace"]).filter_query) == 1

    def test_authors_produces_an_in_clause(self):
        conditions = PRFilter(authors=["ada", "grace"]).filter_query
        sql = _sql(conditions[0])
        assert '"PullRequest".author IN' in sql

    def test_empty_author_list_is_ignored(self):
        # An empty list must not become `author IN ()`, which would match
        # nothing and silently blank the dashboard.
        assert PRFilter(authors=[]).filter_query == []

    def test_authors_combines_with_base_branches(self):
        conditions = PRFilter(
            authors=["ada"], base_branches=["^main$"]
        ).filter_query
        assert len(conditions) == 2
        combined = " ".join(_sql(c) for c in conditions)
        assert '"PullRequest".author IN' in combined
        assert '"PullRequest".base_branch' in combined

    def test_authors_combines_with_excluded_pr_ids(self):
        conditions = PRFilter(
            authors=["ada"], excluded_pr_ids=["some-uuid"]
        ).filter_query
        assert len(conditions) == 2


class TestParsePRFilterProcessorAuthors:
    def test_authors_are_read_off_the_wire(self):
        pr_filter = ParsePRFilterProcessor({"authors": ["ada", "grace"]}).apply()
        assert pr_filter.authors == ["ada", "grace"]

    def test_authors_absent_leaves_none(self):
        pr_filter = ParsePRFilterProcessor({}).apply()
        assert pr_filter.authors is None

    def test_parsed_authors_produce_a_usable_query(self):
        # End to end through the parser, which is how the API receives it.
        pr_filter = ParsePRFilterProcessor(
            {"authors": ["ada"], "base_branches": ["main"]}
        ).apply()
        assert len(pr_filter.filter_query) == 2

    def test_authors_are_not_treated_as_regex(self):
        # base_branches get wrapped by regex_list; authors must not, because
        # they are matched with an exact IN comparison.
        pr_filter = ParsePRFilterProcessor({"authors": ["ada.b"]}).apply()
        assert pr_filter.authors == ["ada.b"]
