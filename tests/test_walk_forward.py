from datetime import date

from backtests.walk_forward import _fold_windows


def test_fold_windows_count_and_embargo():
    wins = _fold_windows(date(2018, 1, 1), date(2026, 1, 1), folds=5, embargo_days=5)
    assert len(wins) == 5
    for train_start, train_end, test_start, test_end in wins:
        assert train_start == "2018-01-01"  # anchored expanding train
        assert train_end < test_start < test_end  # train ends (with embargo) before test
        # the embargo gap: train_end is 5 days before test_start
        assert (date.fromisoformat(test_start) - date.fromisoformat(train_end)).days == 5


def test_fold_windows_are_sequential_and_non_overlapping_tests():
    wins = _fold_windows(date(2020, 1, 1), date(2025, 1, 1), folds=4, embargo_days=7)
    test_ranges = [(w[2], w[3]) for w in wins]
    # each fold's test starts at the previous fold's test end (contiguous, forward-moving)
    for (s1, e1), (s2, e2) in zip(test_ranges, test_ranges[1:]):
        assert e1 == s2
        assert s1 < e1
