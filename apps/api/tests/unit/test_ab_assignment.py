"""Bucketing and split validation - the parts that must be deterministic."""

from __future__ import annotations

import pytest

from zk2.ab.models import AbVariant
from zk2.ab.service import BUCKETS, bucket_of, pick_variant, validate_split
from zk2.core.errors import ValidationError

pytestmark = pytest.mark.unit


def variant(vid: int, percent: int, *, control: bool = False) -> AbVariant:
    return AbVariant(
        id=vid, experiment_id=1, name=f"v{vid}", traffic_percent=percent, is_control=control
    )


def test_bucket_is_stable_for_a_subject() -> None:
    """The same person must not flip variants between turns."""
    first = bucket_of(7, "user:42")
    assert all(bucket_of(7, "user:42") == first for _ in range(20))


def test_bucket_is_in_range() -> None:
    assert all(0 <= bucket_of(1, f"user:{i}") < BUCKETS for i in range(200))


def test_different_experiments_bucket_the_same_subject_independently() -> None:
    """Otherwise every experiment would test the same half of the users."""
    subjects = [f"user:{i}" for i in range(200)]
    first = [bucket_of(1, s) for s in subjects]
    second = [bucket_of(2, s) for s in subjects]
    assert first != second


def test_split_is_roughly_respected_over_many_subjects() -> None:
    variants = [variant(1, 70, control=True), variant(2, 30)]
    counts = {1: 0, 2: 0}
    for i in range(2000):
        chosen = pick_variant(variants, bucket_of(9, f"user:{i}"))
        counts[chosen.id] += 1
    share = counts[2] / sum(counts.values())
    assert 0.25 < share < 0.35


def test_bucket_boundaries_map_to_the_expected_variant() -> None:
    variants = [variant(1, 40, control=True), variant(2, 60)]
    assert pick_variant(variants, 0).id == 1
    assert pick_variant(variants, 39).id == 1
    assert pick_variant(variants, 40).id == 2
    assert pick_variant(variants, 99).id == 2


def test_split_must_add_up() -> None:
    with pytest.raises(ValidationError, match="add up to 100"):
        validate_split([variant(1, 50, control=True), variant(2, 30)])


def test_experiment_needs_two_variants() -> None:
    with pytest.raises(ValidationError, match="at least two"):
        validate_split([variant(1, 100, control=True)])


def test_exactly_one_control() -> None:
    with pytest.raises(ValidationError, match="control"):
        validate_split([variant(1, 50), variant(2, 50)])
    with pytest.raises(ValidationError, match="control"):
        validate_split([variant(1, 50, control=True), variant(2, 50, control=True)])
