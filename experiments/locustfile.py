"""Load profiles for the scaling experiments.

Each simulated user sends about one request per second to /work?ms=WORK_MS, so the user count
is roughly the offered load in req/s. Choose the profile with the SCENARIO environment variable:

  step      5 users -> PEAK for 4 minutes -> 5 users                    (7 min)
  spike     two short bursts to PEAK separated by a quiet period         (8 min)
  periodic  smooth sine between 5 and PEAK users with a 5-minute period  (10 min)

Example:
  $env:SCENARIO="step"; locust -f locustfile.py --headless --host http://flask-app.<ip>.nip.io
"""
import math
import os

from locust import HttpUser, LoadTestShape, constant_pacing, task

SCENARIO = os.getenv("SCENARIO", "step")
PEAK = int(os.getenv("PEAK_USERS", "40"))
BASE = int(os.getenv("BASE_USERS", "5"))
WORK_MS = int(os.getenv("WORK_MS", "50"))
TIME_SCALE = float(os.getenv("TIME_SCALE", "1"))  # e.g. 0.05 for a quick dry run of the tooling


class AppUser(HttpUser):
    wait_time = constant_pacing(1)  # one request per second per user, independent of latency

    @task
    def work(self):
        self.client.get(f"/work?ms={WORK_MS}", name="/work")


# (end_second, users) stages for the stepped profiles.
STAGES = {
    "step": [(60, BASE), (300, PEAK), (420, BASE)],
    "spike": [(60, BASE), (120, PEAK), (300, BASE), (360, PEAK), (480, BASE)],
}
PERIODIC_SECONDS, PERIODIC_PERIOD = 600, 300


class ScenarioShape(LoadTestShape):
    def tick(self):
        t = self.get_run_time() / TIME_SCALE
        if SCENARIO == "periodic":
            if t > PERIODIC_SECONDS:
                return None
            phase = 0.5 - 0.5 * math.cos(2 * math.pi * t / PERIODIC_PERIOD)
            return round(BASE + (PEAK - BASE) * phase), 20
        for end, users in STAGES[SCENARIO]:
            if t < end:
                return users, 20  # spawn rate high enough that steps are steps
        return None
