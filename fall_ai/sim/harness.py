"""Simulation harness: drives the REAL camera.py pipeline (Tracker,
_fall_transition, process_people) through synthetic pose sequences, so
results reflect actual code behavior, not a hand-derived guess.
"""
import math
import sys
import types
import importlib
import time as timemod

sys.path.insert(0, "/home/claude/work/fall_ai/app")

for mod in ["cv2", "onnx", "onnxruntime", "requests"]:
    try:
        importlib.import_module(mod)
    except ImportError:
        sys.modules[mod] = types.ModuleType(mod)

import numpy as np
import camera
import config as cfgmod


def make_person(cx, cy, angle_deg, aspect, hide_torso=False, jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    k = np.zeros((17, 3), dtype=np.float32)
    L = 0.15
    dx = L * math.cos(math.radians(angle_deg))
    dy = L * math.sin(math.radians(angle_deg))
    jx = rng.uniform(-jitter, jitter)
    jy = rng.uniform(-jitter, jitter)
    shoulder = (cx - dx + jx, cy - dy + jy)
    hip = (cx + dx + jx, cy + dy + jy)
    vis = 0.0 if hide_torso else 0.9
    k[5] = [shoulder[0] - 0.02, shoulder[1], vis]
    k[6] = [shoulder[0] + 0.02, shoulder[1], vis]
    k[11] = [hip[0] - 0.02, hip[1], vis]
    k[12] = [hip[0] + 0.02, hip[1], vis]
    k[0] = [shoulder[0], shoulder[1] - 0.05, 0.9]
    k[1] = [shoulder[0] - 0.01, shoulder[1] - 0.05, 0.9]
    k[2] = [shoulder[0] + 0.01, shoulder[1] - 0.05, 0.9]
    k[7] = [shoulder[0] - 0.05, shoulder[1] + 0.02, 0.9]
    k[8] = [shoulder[0] + 0.05, shoulder[1] + 0.02, 0.9]
    k[9] = [shoulder[0] - 0.08, shoulder[1] + 0.04, 0.9]
    k[10] = [shoulder[0] + 0.08, shoulder[1] + 0.04, 0.9]
    k[13] = [hip[0] - 0.02, hip[1] + 0.06, 0.9]
    k[14] = [hip[0] + 0.02, hip[1] + 0.06, 0.9]
    k[15] = [hip[0] - 0.02, hip[1] + 0.1, 0.9]
    k[16] = [hip[0] + 0.02, hip[1] + 0.1, 0.9]
    return {"confidence": 0.9, "bbox": (cx + jx, cy + jy, 0.15 * aspect, 0.15), "keypoints": k}


def new_worker(g):
    cw = camera.CameraWorker.__new__(camera.CameraWorker)
    cw.cam = {"id": "sim"}
    cw.g = g
    cw.tracker = camera.Tracker()
    cw.track_history = {}
    cw.fall_candidate_since = {}
    cw.fall_stable_since = {}
    cw.fall_candidate_bad_since = {}
    cw.fall_candidate_mode = {}
    cw.fall_unknown_since = {}
    cw.fall_unknown_bad_since = {}
    cw.last_alert = 0.0
    cw.last_frame = np.zeros((10, 10, 3), dtype=np.uint8)
    cw.notifier = None
    cw.mqtt_pub = None
    cw.snapshot_dir = camera.Path("/tmp/snap_sim")

    class FakeHA:
        def fire_event(self, *a, **k):
            return True

        def call_service(self, *a, **k):
            return True

    cw.ha = FakeHA()
    return cw


def run_scenario(name, frames, g, expect, dt=0.25, verbose=False):
    """frames: list of dicts with keys angle, aspect, hide(optional), jitter(optional)"""
    cw = new_worker(g)
    t0 = 1000.0
    confirmed_at = None
    for i, fr in enumerate(frames):
        t = t0 + i * dt
        timemod.monotonic = lambda tv=t: tv
        person = make_person(
            0.5, 0.5, fr["angle"], fr["aspect"],
            hide_torso=fr.get("hide", False),
            jitter=fr.get("jitter", 0.0),
            seed=i,
        )
        cw.process_people([person])
        if cw.last_alert and confirmed_at is None:
            confirmed_at = i * dt
            if not verbose:
                break
    total_time = (len(frames) - 1) * dt
    ok = (confirmed_at is not None) == expect
    status = "OK" if ok else "MISMATCH"
    conf_str = f"{confirmed_at:.2f}s" if confirmed_at is not None else "-"
    print(f"[{status:8}] {name:32} expect={'FALL' if expect else 'no-fall':8} "
          f"got={'FALL@'+conf_str if confirmed_at is not None else 'no-fall':12} "
          f"(sim {total_time:.1f}s, {len(frames)} frames)")
    return ok, confirmed_at


# ---------------------------------------------------------------- scenarios
def scenario_fast_fall_clear():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([60, 35, 15, 8], [0.8, 1.3, 1.9, 2.2])]
    frames += [{"angle": 8, "aspect": 2.2}] * 10
    return frames


def scenario_fast_fall_occluded_landing():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([60, 35, 15, 8], [0.8, 1.3, 1.9, 2.2])]
    frames += [{"angle": 8, "aspect": 2.2, "hide": True}] * 15
    return frames


def scenario_slow_fall_gradual():
    # ~4s gradual decline then stays put
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    decline = [80, 72, 64, 56, 50, 45, 38, 30, 22, 15, 10]
    aspects = np.linspace(0.6, 2.1, len(decline))
    frames += [{"angle": a, "aspect": asp} for a, asp in zip(decline, aspects)]
    frames += [{"angle": 8, "aspect": 2.2}] * 15
    return frames


def scenario_slow_fall_jittery_landing():
    # Gradual fall, but keeps shifting slightly afterward (person in pain).
    # jitter=0.008 approximates realistic per-frame pose-estimation noise
    # (~0.8% of frame) for a person who isn't perfectly still, as opposed to
    # genuine repositioning/crawling.
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    decline = [80, 72, 64, 56, 50, 45, 38, 30, 22, 15, 10]
    aspects = np.linspace(0.6, 2.1, len(decline))
    frames += [{"angle": a, "aspect": asp} for a, asp in zip(decline, aspects)]
    frames += [{"angle": 8, "aspect": 2.2, "jitter": 0.008}] * 20
    return frames


def scenario_unknown_onset():
    return [{"angle": 8, "aspect": 2.2}] * 60  # 15s at dt=0.25, margin over 12s config


def scenario_sit_down_normal():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([78, 68, 60, 58], [0.65, 0.75, 0.85, 0.9])]
    frames += [{"angle": 58, "aspect": 0.9}] * 30
    return frames


def scenario_recline_in_chair_normal():
    # Sit back deeply into a sofa/recliner - stops around 55-60 degrees,
    # never reaches horizontal. The common case of "sitting back to relax",
    # as opposed to fully lying down flat.
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([78, 68, 62, 58, 56], [0.65, 0.72, 0.8, 0.85, 0.87])]
    frames += [{"angle": 56, "aspect": 0.87}] * 40
    return frames


def scenario_bend_pick_object():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": 0.7} for a in [55, 35, 30, 35, 55]]
    frames += [{"angle": 85, "aspect": 0.6}] * 8
    return frames


def scenario_lie_on_sofa_nap():
    # Structurally similar to a slow fall: upright -> gradual recline -> stays still a long time.
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    decline = [78, 68, 58, 50, 44, 38, 30, 20, 12]
    aspects = np.linspace(0.65, 2.0, len(decline))
    frames += [{"angle": a, "aspect": asp} for a, asp in zip(decline, aspects)]
    frames += [{"angle": 10, "aspect": 2.1}] * 60  # lies still a long time (nap)
    return frames


def scenario_floor_exercise():
    # Sit-ups: person gets down onto the floor under control (not an abrupt
    # single-frame drop - that would itself look like a fast fall), then
    # does sit-up reps with the torso angle oscillating continuously.
    frames = [{"angle": 85, "aspect": 0.6}] * 4
    getdown = [70, 50, 30, 15, 10]  # ~1.25s controlled descent to the floor
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip(getdown, np.linspace(0.65, 2.0, len(getdown)))]
    cycle = [10, 20, 35, 55, 35, 20, 10, 10]  # one sit-up cycle
    frames += [{"angle": a, "aspect": 2.0} for a in cycle] * 8  # ~8 reps
    return frames


def scenario_duck_quickly():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": 1.0} for a in [40, 25, 40]]
    frames += [{"angle": 85, "aspect": 0.6}] * 8
    return frames


def scenario_fall_then_recover_fast():
    frames = [{"angle": 85, "aspect": 0.6}] * 6
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([60, 35, 15, 8], [0.8, 1.3, 1.9, 2.2])]
    frames += [{"angle": 8, "aspect": 2.2}] * 3  # only ~0.75s lying, then...
    frames += [{"angle": a, "aspect": asp} for a, asp in
               zip([30, 55, 75, 85], [1.6, 1.1, 0.7, 0.6])]
    frames += [{"angle": 85, "aspect": 0.6}] * 8
    return frames


def scenario_topdown_camera_fall():
    # Reproduces the real "san" (outdoor, steep/overhead-angle) camera log:
    # torso angle crashes low (real rotation) but aspect ratio stays flat
    # because the camera looks down on the scene rather than side-on.
    bouncy_angles = [20, 36, 42, 45, 10, 10, 23, 27, 33, 36, 44, 44, 46, 43, 44,
                      41, 42, 41, 40, 44, 43, 39, 40, 44, 39, 38, 42, 36, 17, 44]
    frames = [{"angle": 85, "aspect": 0.62}] * 6
    frames += [{"angle": a, "aspect": 0.68} for a in bouncy_angles]
    return frames


SCENARIOS = [
    ("fast_fall_clear", scenario_fast_fall_clear, True),
    ("fast_fall_occluded_landing", scenario_fast_fall_occluded_landing, True),
    ("slow_fall_gradual", scenario_slow_fall_gradual, True),
    ("slow_fall_jittery_landing", scenario_slow_fall_jittery_landing, True),
    ("unknown_onset_lying", scenario_unknown_onset, True),
    ("topdown_camera_fall", scenario_topdown_camera_fall, True),
    ("sit_down_normal", scenario_sit_down_normal, False),
    ("recline_in_chair_normal", scenario_recline_in_chair_normal, False),
    ("bend_pick_object", scenario_bend_pick_object, False),
    ("lie_on_sofa_nap", scenario_lie_on_sofa_nap, False),
    ("floor_exercise_situps", scenario_floor_exercise, False),
    ("duck_quickly", scenario_duck_quickly, False),
    ("fall_then_recover_fast", scenario_fall_then_recover_fast, False),
]


def run_all(g, label=""):
    print(f"\n=== {label} ===")
    results = {}
    n_ok = 0
    for name, fn, expect in SCENARIOS:
        ok, t = run_scenario(name, fn(), g, expect)
        results[name] = (ok, t)
        n_ok += ok
    print(f"--- {n_ok}/{len(SCENARIOS)} scenarios matched expectation ---")
    return results


if __name__ == "__main__":
    g = dict(cfgmod.DEFAULT_GLOBAL)
    g["fast_fall_score_threshold"] = 0.52
    g["slow_fall_score_threshold"] = 0.30
    g["slow_fall_transition_seconds"] = 6.0
    g["unknown_onset_seconds"] = 12.0
    run_all(g, "CURRENT DEPLOYED CONFIG")


# ---------------------------------------------------------------------------
# Known accepted trade-off (not a bug): a person who fully reclines flat on a
# sofa/bed and stays still is, from 2D pose alone, kinematically
# indistinguishable from a real slow fall onto the floor (both are
# upright -> gradual decline to horizontal -> sustained stillness). Given
# this project's recall-first priority, this can trigger a false alarm.
# Fixing it properly needs additional context the pose model doesn't have
# (e.g. a per-camera "resting zone" mask so a person lying still ONLY inside
# a marked sofa/bed area gets a longer confirmation window or is
# suppressed) - tracked as a future enhancement, not implemented here.
# ---------------------------------------------------------------------------
