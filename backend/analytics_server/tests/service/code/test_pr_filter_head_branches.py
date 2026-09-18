"""
Tests for head-branch filtering in PRFilter.

This is the branch-prefix attribution axis: Zinc encodes the owning team in the
branch name (`blue/feat/...` = Skipper, `gold/bug/...` = Bliss), so a head
branch regex is an alternative to looking the author up in a membership list.

Unlike `authors`, which is an exact IN list, this is a POSIX regex match — the
same mechanism `base_branches` already uses.
"""

from mhq.service.code.pr_filter import ParsePRFilterProcessor
from mhq.store.models.code.filter import PRFilter


def _sql(expression) -> str:
    return str(expression)


class TestPRFilterHeadBranches:
    def test_none_is_ignored(self):
        assert PRFilter(head_branches=None).filter_query == []

    def test_empty_list_is_ignored(self):
        # Must not become a condition that matches nothing and blanks the
        # dashboard.
        assert PRFilter(head_branches=[]).filter_query == []

    def test_produces_a_regex_condition(self):
        conditions = PRFilter(head_branches=["^blue/"]).filter_query
        assert len(conditions) == 1
        assert '"PullRequest".head_branch' in _sql(conditions[0])

    def test_multiple_prefixes_are_ored(self):
        conditions = PRFilter(head_branches=["^blue/", "^skipper/"]).filter_query
        assert len(conditions) == 1
        sql = _sql(conditions[0])
        assert " OR " in sql.upper()

    def test_combines_with_base_branches(self):
        # base_branch identifies production; head_branch identifies the team.
        # Both must apply at once.
        conditions = PRFilter(
            base_branches=["^main$"], head_branches=["^blue/"]
        ).filter_query
        assert len(conditions) == 2
        combined = " ".join(_sql(c) for c in conditions)
        assert '"PullRequest".base_branch' in combined
        assert '"PullRequest".head_branch' in combined

    def test_combines_with_authors(self):
        # Both axes at once is not how the UI uses it, but it must not break.
        conditions = PRFilter(
            authors=["ada"], head_branches=["^blue/"]
        ).filter_query
        assert len(conditions) == 2


class TestParsePRFilterProcessorHeadBranches:
    def test_head_branches_are_read_off_the_wire(self):
        pr_filter = ParsePRFilterProcessor(
            {"head_branches": ["^blue/", "^skipper/"]}
        ).apply()
        assert pr_filter.head_branches == ["^blue/", "^skipper/"]

    def test_absent_leaves_none(self):
        assert ParsePRFilterProcessor({}).apply().head_branches is None

    def test_bare_prefixes_are_turned_into_regexes(self):
        # regex_list is what base_branches already goes through, so an
        # unanchored prefix still produces a valid pattern rather than an error.
        pr_filter = ParsePRFilterProcessor({"head_branches": ["blue/"]}).apply()
        assert pr_filter.head_branches
        assert len(pr_filter.filter_query) == 1

    def test_end_to_end_with_authors_and_branches(self):
        pr_filter = ParsePRFilterProcessor(
            {
                "authors": ["ada"],
                "base_branches": ["main"],
                "head_branches": ["^blue/"],
            }
        ).apply()
        assert len(pr_filter.filter_query) == 3
