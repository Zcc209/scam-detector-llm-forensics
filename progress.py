"""Atomic pipeline milestones for the local interface."""
import json
from pathlib import Path


class Progress:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.steps = [{"state": "waiting", "detail": ""} for _ in range(5)]
        self.experiment = None

    def experiment_progress(self, completed, total):
        self.experiment = {"completed": completed, "total": total}
        self.save()

    def set(self, index, state, detail=""):
        self.steps[index] = {"state": state, "detail": detail}
        self.save()

    def stop(self, detail):
        for step in self.steps:
            if step["state"] == "running":
                step.update(state="warning", detail=detail)
            elif step["state"] == "waiting":
                step.update(state="skipped", detail="Not executed")
        self.save()

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"steps": self.steps, "experiment": self.experiment}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
