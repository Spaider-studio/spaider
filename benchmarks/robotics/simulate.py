"""
A deterministic grid-world: does memory make a robot better over sessions?

A robot is sent to fetch named objects. Without memory it sweeps until it sees
the object. With memory it goes first to where the object was last seen. Objects
sometimes move between sessions, so stale memories cost steps too.

This is a simulation with no model, physics or real robot. It shows that the
memory mechanics help a task that benefits from remembering places, and nothing
about real-world performance. Same seed, same result.

    PYTHONPATH=sdk/edge python benchmarks/robotics/simulate.py --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from spaider_edge import EdgeMemory

Point = Tuple[int, int]
_SIGHT = 2


@dataclass
class World:
    size: int
    objects: Dict[str, Point]
    rng: random.Random
    move_chance: float

    def visible_from(self, position: Point) -> Dict[str, Point]:
        return {
            name: place
            for name, place in self.objects.items()
            if abs(place[0] - position[0]) <= _SIGHT and abs(place[1] - position[1]) <= _SIGHT
        }

    def drift(self) -> None:
        for name in sorted(self.objects):
            if self.rng.random() < self.move_chance:
                self.objects[name] = (self.rng.randrange(self.size), self.rng.randrange(self.size))


@dataclass
class Robot:
    world: World
    memory: Optional[EdgeMemory]
    position: Point = (0, 0)
    steps: int = 0
    session: str = ""
    seen: Dict[str, Point] = field(default_factory=dict)

    def _observe(self) -> None:
        if self.memory is None:
            return
        for name, place in self.world.visible_from(self.position).items():
            self.memory.remember(
                f"saw {name}",
                session_id=self.session,
                location={"x": float(place[0]), "y": float(place[1])},
                idempotency_key=f"{self.session}:{self.steps}:{name}",
            )

    def _walk_to(self, target: Point, wanted: str) -> bool:
        while self.position != target:
            if wanted in self.world.visible_from(self.position):
                return True
            x, y = self.position
            if x != target[0]:
                x += 1 if target[0] > x else -1
            else:
                y += 1 if target[1] > y else -1
            self.position = (x, y)
            self.steps += 1
            self._observe()
        return wanted in self.world.visible_from(self.position)

    def _remembered(self, wanted: str) -> Optional[Point]:
        if self.memory is None:
            return None
        matches = [item for item in self.memory.recall(f"saw {wanted}", limit=50) if item["text"] == f"saw {wanted}"]
        if not matches:
            return None
        newest = max(matches, key=lambda item: item["occurred_at"])
        return int(newest["location"]["x"]), int(newest["location"]["y"])

    def _sweep(self, wanted: str) -> None:
        stride = _SIGHT * 2 + 1
        size = self.world.size
        rows = list(range(_SIGHT, size, stride))
        if rows[-1] < size - 1 - _SIGHT:
            rows.append(size - 1 - _SIGHT)
        for row_index, y in enumerate(rows):
            ends = (0, size - 1) if row_index % 2 == 0 else (size - 1, 0)
            for x in ends:
                if self._walk_to((x, y), wanted):
                    return

    def fetch(self, wanted: str) -> None:
        self._observe()
        remembered = self._remembered(wanted)
        if remembered is not None and self._walk_to(remembered, wanted):
            return
        self._sweep(wanted)


def run(seed: int, *, use_memory: bool, sessions: int = 8, tasks: int = 10, size: int = 30, objects: int = 12,
        move_chance: float = 0.1) -> List[float]:
    """Mean steps per fetch, one value per session."""
    rng = random.Random(seed)
    names = [f"object{index}" for index in range(objects)]
    world = World(size, {name: (rng.randrange(size), rng.randrange(size)) for name in names}, rng, move_chance)
    memory = EdgeMemory() if use_memory else None
    means = []
    for session in range(sessions):
        total = 0
        for task in range(tasks):
            robot = Robot(world, memory, position=(0, 0), session=f"s{session}t{task}")
            robot.fetch(names[rng.randrange(objects)])
            total += robot.steps
        means.append(total / tasks)
        world.drift()
    if memory is not None:
        memory.close()
    return means


def compare(seeds: List[int], **options) -> dict:
    with_memory = [run(seed, use_memory=True, **options) for seed in seeds]
    without = [run(seed, use_memory=False, **options) for seed in seeds]

    def average(curves: List[List[float]]) -> List[float]:
        return [round(sum(values) / len(values), 2) for values in zip(*curves)]

    return {
        "seeds": seeds,
        "note": "Deterministic grid-world simulation. Says nothing about real robots.",
        "steps_per_fetch_with_memory": average(with_memory),
        "steps_per_fetch_without_memory": average(without),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(compare([args.seed + offset for offset in range(args.runs)]), indent=2))
