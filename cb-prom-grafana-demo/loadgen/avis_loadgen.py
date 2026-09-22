"""
Avis-style rental fleet workload generator for the Couchbase demo cluster.

Models the KV traffic pattern of a car-rental operation:
  - "vehicle" docs   -> live fleet status per rental hub (airport code)
  - "reservation" docs -> bookings created against available vehicles

Traffic mix (roughly matches a real rental app):
  65% GET  vehicle            -- availability lookup ("is this car free?")
  15% GET  reservation        -- booking status lookup
  15% GET+UPSERT vehicle      -- checkout/return event (status flip)
   5% UPSERT reservation      -- new booking created

Run inside the demo's docker network so it can resolve the `couchbase`
hostname and use standard ports -- see the loadgen service in
docker-compose.yml. Stop anytime with Ctrl+C; it prints a running
ops/sec line and a summary on exit.
"""

import os
import random
import signal
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import CouchbaseException, DocumentNotFoundException
from couchbase.options import ClusterOptions

CB_CONN_STR = os.environ.get("CB_CONN_STR", "couchbase://couchbase")
CB_USERNAME = os.environ.get("CB_USERNAME", "Administrator")
CB_PASSWORD = os.environ.get("CB_PASSWORD", "password123")
CB_BUCKET = os.environ.get("CB_BUCKET", "avis-fleet")

FLEET_SIZE = int(os.environ.get("FLEET_SIZE", "500"))
TARGET_OPS = float(os.environ.get("TARGET_OPS", "200"))
THREADS = int(os.environ.get("THREADS", "8"))
DURATION_SECONDS = int(os.environ.get("DURATION_SECONDS", "0"))  # 0 = run forever
SEED_ONLY = os.environ.get("SEED_ONLY", "0") == "1"

HUBS = [
    "LAX", "JFK", "ORD", "ATL", "DFW",
    "MIA", "SFO", "SEA", "DEN", "LAS",
]
VEHICLE_CLASSES = [
    ("economy", 0.30), ("compact", 0.20), ("midsize", 0.20),
    ("suv", 0.15), ("luxury", 0.10), ("ev", 0.05),
]
MODELS = {
    "economy": ["Nissan Versa", "Kia Rio"],
    "compact": ["Toyota Corolla", "Hyundai Elantra"],
    "midsize": ["Toyota Camry", "Honda Accord"],
    "suv": ["Ford Explorer", "Jeep Grand Cherokee"],
    "luxury": ["BMW 5 Series", "Mercedes E-Class"],
    "ev": ["Tesla Model 3", "Chevrolet Bolt"],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def weighted_choice(pairs):
    r = random.random()
    upto = 0.0
    for value, weight in pairs:
        upto += weight
        if r <= upto:
            return value
    return pairs[-1][0]


def connect():
    cluster = Cluster(
        CB_CONN_STR,
        ClusterOptions(PasswordAuthenticator(CB_USERNAME, CB_PASSWORD)),
    )
    cluster.wait_until_ready(timedelta(seconds=30))
    return cluster


def seed_fleet(collection):
    print(f"Seeding {FLEET_SIZE} vehicles across {len(HUBS)} hubs into bucket '{CB_BUCKET}'...")
    vehicle_keys = []
    for i in range(FLEET_SIZE):
        vclass = weighted_choice(VEHICLE_CLASSES)
        key = f"vehicle::{i:05d}"
        doc = {
            "type": "vehicle",
            "vehicle_id": key,
            "plate": f"{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{random.randint(100,999)}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}",
            "location_code": random.choice(HUBS),
            "vehicle_class": vclass,
            "model": random.choice(MODELS[vclass]),
            "status": "available",
            "odometer_miles": random.randint(500, 45000),
            "last_updated": now_iso(),
        }
        collection.upsert(key, doc)
        vehicle_keys.append(key)
    print("Seed complete.")
    return vehicle_keys


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.gets = 0
        self.sets = 0
        self.errors = 0
        self.window_gets = 0
        self.window_sets = 0
        self.window_errors = 0

    def record(self, gets=0, sets=0, errors=0):
        with self.lock:
            self.gets += gets
            self.sets += sets
            self.errors += errors
            self.window_gets += gets
            self.window_sets += sets
            self.window_errors += errors

    def flush_window(self):
        with self.lock:
            g, s, e = self.window_gets, self.window_sets, self.window_errors
            self.window_gets = self.window_sets = self.window_errors = 0
            return g, s, e


def worker(collection, vehicle_keys, reservation_keys, res_lock, stats, stop_event, per_thread_ops):
    interval = 1.0 / per_thread_ops if per_thread_ops > 0 else 0
    next_tick = time.monotonic()
    while not stop_event.is_set():
        roll = random.random()
        try:
            if roll < 0.65:
                # Availability lookup
                key = random.choice(vehicle_keys)
                try:
                    collection.get(key)
                except DocumentNotFoundException:
                    pass
                stats.record(gets=1)

            elif roll < 0.80:
                # Reservation status lookup
                with res_lock:
                    key = random.choice(reservation_keys) if reservation_keys else None
                if key:
                    try:
                        collection.get(key)
                    except DocumentNotFoundException:
                        pass
                stats.record(gets=1)

            elif roll < 0.95:
                # Checkout / return event: read-modify-write a vehicle's status
                key = random.choice(vehicle_keys)
                try:
                    result = collection.get(key)
                    doc = result.content_as[dict]
                    if doc.get("status") == "rented":
                        doc["status"] = "available"
                    else:
                        doc["status"] = "rented"
                        doc["odometer_miles"] = doc.get("odometer_miles", 0) + random.randint(5, 60)
                    doc["last_updated"] = now_iso()
                    collection.upsert(key, doc)
                    stats.record(gets=1, sets=1)
                except DocumentNotFoundException:
                    stats.record(gets=1)

            else:
                # New booking
                res_id = uuid.uuid4().hex[:10]
                key = f"reservation::{res_id}"
                vehicle_key = random.choice(vehicle_keys)
                hub = random.choice(HUBS)
                doc = {
                    "type": "reservation",
                    "confirmation_number": res_id.upper(),
                    "vehicle_id": vehicle_key,
                    "location_code": hub,
                    "customer_id": f"customer::{random.randint(1, 5000):05d}",
                    "status": "confirmed",
                    "created_at": now_iso(),
                }
                collection.upsert(key, doc)
                with res_lock:
                    reservation_keys.append(key)
                stats.record(sets=1)

        except CouchbaseException as exc:
            stats.record(errors=1)
            if random.random() < 0.02:
                print(f"  [warn] op failed: {exc}", file=sys.stderr)

        if interval:
            next_tick += interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.monotonic()


def main():
    print(f"Connecting to {CB_CONN_STR} as {CB_USERNAME} ...")
    cluster = connect()
    bucket = cluster.bucket(CB_BUCKET)
    collection = bucket.default_collection()

    vehicle_keys = seed_fleet(collection)

    if SEED_ONLY:
        print("SEED_ONLY=1 set, exiting after seed.")
        return

    reservation_keys = deque(maxlen=2000)
    res_lock = threading.Lock()
    stats = Stats()
    stop_event = threading.Event()

    def handle_sigint(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    per_thread_ops = TARGET_OPS / THREADS if THREADS else TARGET_OPS
    print(
        f"Starting {THREADS} workers, target ~{TARGET_OPS:.0f} ops/sec total "
        f"against bucket '{CB_BUCKET}' (Ctrl+C to stop)..."
    )

    threads = [
        threading.Thread(
            target=worker,
            args=(collection, vehicle_keys, reservation_keys, res_lock, stats, stop_event, per_thread_ops),
            daemon=True,
        )
        for _ in range(THREADS)
    ]
    for t in threads:
        t.start()

    start = time.monotonic()
    last_report = start
    try:
        while not stop_event.is_set():
            time.sleep(1)
            now = time.monotonic()
            elapsed = now - last_report
            last_report = now
            g, s, e = stats.flush_window()
            print(
                f"OPS/SEC: {round((g + s) / elapsed):>5}  "
                f"(GET {round(g / elapsed):>5}/s  SET {round(s / elapsed):>5}/s)  "
                f"errors={e}  total={stats.gets + stats.sets}"
            )
            if DURATION_SECONDS and (now - start) >= DURATION_SECONDS:
                stop_event.set()
    except KeyboardInterrupt:
        stop_event.set()

    for t in threads:
        t.join(timeout=5)

    total_elapsed = time.monotonic() - start
    print("\n--- Summary ---")
    print(f"Duration: {total_elapsed:.1f}s")
    print(f"Total GET: {stats.gets}  Total SET: {stats.sets}  Errors: {stats.errors}")
    print(f"Avg ops/sec: {(stats.gets + stats.sets) / total_elapsed:.1f}")

    cluster.close()


if __name__ == "__main__":
    main()
