"""Measure current production persistence/projection code on read-only source copies."""
from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
from copy import deepcopy
import json
import threading
import time

from benchmark_support import (
    SCRATCH, TimedLock, export_frontend, fingerprint, fixture_manager,
    initialize, load_samples, stats, timed,
)


def baseline(manager, sample):
    from server.conversation_view.snapshots import VIEW_LOCK
    fixture, rec, segment = fixture_manager(sample)
    measurements = {k: [] for k in (
        "read", "history_deepcopy", "history_json", "projection_deepcopy", "projection_json", "capture", "capture_json", "save")}
    response_bytes = 0
    for _ in range(3):
        document, duration = timed(lambda: manager.read_conversation(sample["id"]))
        assert document and len(document["messages"]) == len(sample["document"]["messages"])
        measurements["read"].append(duration)
        copied, duration = timed(lambda: deepcopy(document["messages"]))
        measurements["history_deepcopy"].append(duration)
        encoded, duration = timed(lambda: json.dumps(copied, ensure_ascii=False))
        measurements["history_json"].append(duration)
        del copied, encoded
        projection, duration = timed(rec.display_projection.snapshot)
        measurements["projection_deepcopy"].append(duration)
        _, duration = timed(lambda: json.dumps(projection, ensure_ascii=False))
        measurements["projection_json"].append(duration)
        captured, duration = timed(lambda: fixture.capture_display(
            "benchmark", sample["id"], manager, workspace_id="benchmark"))
        measurements["capture"].append(duration)
        encoded, duration = timed(lambda: json.dumps(
            {"messages": captured["messages"], "display": captured["display"]}, ensure_ascii=False))
        response_bytes = len(encoded.encode("utf-8"))
        measurements["capture_json"].append(duration)
        del captured, encoded
        saved, duration = timed(lambda: manager.save_conversation(
            sample["id"], sample["document"]["messages"]))
        assert saved, "Real save_conversation returned False"
        measurements["save"].append(duration)
    return {"id": sample["id"], "file_mib": round(sample["file_bytes"] / 1048576, 3),
            "messages": len(sample["document"]["messages"]),
            "roles": dict(Counter(m.get("role") for m in sample["document"]["messages"])),
            "tool_calls": sum(len(m.get("tool_calls") or []) for m in sample["document"]["messages"]),
            "reconstructed_segment": segment, "response_mib": round(response_bytes / 1048576, 3),
            "timings": {k: stats(v) for k, v in measurements.items()}}


def contention(manager, samples, writer_count):
    import server.conversation_view.snapshots as view
    fixture, rec, segment = fixture_manager(samples[0])
    previous_view, previous_io = view.VIEW_LOCK, manager._io_lock
    view_lock, io_lock = TimedLock(), TimedLock()
    view.VIEW_LOCK, manager._io_lock = view_lock, io_lock
    output = {"writer_count": writer_count, "saves_per_writer": 3,
              "capture_threads": 2, "captures_per_thread": 3,
              "poll_threads": 2, "polls_per_thread": 8,
              "save_ms": [], "capture_ms": [], "empty_poll_ms": [], "errors": []}
    barrier = threading.Barrier(writer_count + 2 + 2 + 1)
    finished = threading.Event()
    jitters = []
    def heartbeat():
        while not finished.is_set():
            start = time.perf_counter()
            finished.wait(0.02)
            jitters.append(max(0, (time.perf_counter() - start) * 1000 - 20))
    def guarded(fn):
        def work():
            try:
                barrier.wait(timeout=15)
                fn()
            except Exception as exc:
                output["errors"].append(f"{threading.current_thread().name}: {type(exc).__name__}: {exc}")
        return work
    def writer(sample):
        for _ in range(3):
            ok, ms = timed(lambda: manager.save_conversation(sample["id"], sample["document"]["messages"]))
            assert ok
            output["save_ms"].append(ms)
    def reader():
        for _ in range(3):
            value, ms = timed(lambda: fixture.capture_display(
                "benchmark", samples[0]["id"], manager, workspace_id="benchmark"))
            assert value
            output["capture_ms"].append(ms)
            del value
    def poller():
        for _ in range(8):
            value, ms = timed(lambda: fixture.capture_event_window(rec, rec.next_event_idx))
            assert not value["events"]
            output["empty_poll_ms"].append(ms)
            time.sleep(0.05)
    threads = [threading.Thread(name=f"writer-{i}", target=guarded(lambda s=samples[i]: writer(s)))
               for i in range(writer_count)]
    threads += [threading.Thread(name=f"capture-{i}", target=guarded(reader)) for i in range(2)]
    threads += [threading.Thread(name=f"poll-{i}", target=guarded(poller)) for i in range(2)]
    ticker = threading.Thread(name="heartbeat", target=heartbeat)
    start = time.perf_counter()
    try:
        ticker.start()
        for thread in threads:
            thread.start()
        barrier.wait(timeout=15)
        for thread in threads:
            thread.join(timeout=45)
        assert not any(thread.is_alive() for thread in threads), "bounded workload exceeded 45s/thread"
    finally:
        finished.set()
        ticker.join(timeout=2)
        view.VIEW_LOCK, manager._io_lock = previous_view, previous_io
    output["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    for key in ("save_ms", "capture_ms", "empty_poll_ms"):
        output[key] = stats(output[key])
    output["heartbeat_overshoot"] = stats(jitters)
    output["locks"] = {}
    for label, lock in (("view", view_lock), ("io", io_lock)):
        grouped = {}
        for row in lock.rows:
            role = row["thread"].split("-")[0]
            grouped.setdefault(role, []).append(row)
        output["locks"][label] = {role: {
            "wait": stats([r["wait_ms"] for r in rows]),
            "hold": stats([r["hold_ms"] for r in rows]),
        } for role, rows in grouped.items()}
    assert not output["errors"], output["errors"]
    return output


def main():
    initialize()
    manager, samples = load_samples()
    export_frontend(samples)
    result = {
        "method": "Current production methods; original files read-only, sandboxed copies writable. Three baseline repetitions; bounded concurrent stress, not measured production duty cycle.",
        "projection_limit": "Reconstructed from persisted semantic messages in the work segment with most tools; original SSE timing and monitor snapshots are unavailable.",
        "baseline": [], "contention": [],
    }
    with (SCRATCH / "production-methods.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
        for sample in samples:
            result["baseline"].append(baseline(manager, sample))
        for writers in (0, 1, 3):
            result["contention"].append(contention(manager, samples, writers))
    result["originals_unchanged"] = {
        s["id"]: fingerprint(s["source"]) == s["original_sha256"] for s in samples}
    path = SCRATCH / "backend-results.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
