"""Deterministic planning algorithms: partitioning, coverage paths, assignment.

Nothing in this package consults an AI model. Every function here is pure and unit-tested.
"""

from aeris.planning.assignment import (
    AssignmentCandidate,
    AssignmentStrategy,
    AssignmentWeights,
    GreedyAssignmentStrategy,
    ZoneAssignmentProposal,
)
from aeris.planning.coverage import BoustrophedonPlanner, CoveragePlanner, CoverageRequest
from aeris.planning.geo_frame import LocalFrame
from aeris.planning.partition import GridPartitioner, Partitioner
from aeris.planning.progress import PlanProgressTracker

__all__ = [
    "AssignmentCandidate",
    "AssignmentStrategy",
    "AssignmentWeights",
    "BoustrophedonPlanner",
    "CoveragePlanner",
    "CoverageRequest",
    "GreedyAssignmentStrategy",
    "GridPartitioner",
    "LocalFrame",
    "Partitioner",
    "PlanProgressTracker",
    "ZoneAssignmentProposal",
]
