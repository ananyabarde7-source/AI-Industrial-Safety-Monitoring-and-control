import time
import json
import urllib.request
import DobotDllType as dType

PI_URL = "http://172.20.10.2:5000/api/state"

# Continuous route loop. Test one point first by setting TEST_ONE_POINT = True.
MOTION_ENABLED = True
TEST_ONE_POINT = False

PTP_MODE = dType.PTPMode.PTPMOVJXYZMode
POLL_INTERVAL = 0.25
POINT_TIMEOUT = 60

POINTS = [
    (171.4896, 91.8161, 155.2511, 56.2901),
    (97.8798, 240.0588, -9.0516, 95.9430),
    (44.1461, 149.3729, 31.5412, 101.6606),
    (-75.5811, 137.1809, 31.7746, 146.9783),
]

api = dType.load()
connected = False
stop_requested = False


def read_state():
    req = urllib.request.Request(PI_URL)
    with urllib.request.urlopen(req, timeout=2) as response:
        data = json.loads(response.read().decode("utf-8"))

    state = str(data.get("status", "UNKNOWN")).upper()
    risk = str(data.get("risk_level", "UNKNOWN")).upper()

    # Fail closed: only continue when Pi reports NORMAL + LOW.
    if state != "NORMAL" or risk != "LOW":
        raise RuntimeError(
            "Movement halted: Pi status={}, risk={}".format(state, risk)
        )
    return data


def stop_robot():
    global stop_requested
    stop_requested = True
    try:
        dType.SetQueuedCmdForceStopExec(api)
        dType.SetQueuedCmdClear(api)
        print("\nStop requested; command queue cleared.", flush=True)
    except Exception as exc:
        print("\nSTOP COMMAND ERROR:", exc, flush=True)


def wait_for_point(target_index):
    started = time.time()
    last_print = 0
    print("Target queue index: {}".format(target_index), flush=True)

    while time.time() - started < POINT_TIMEOUT:
        read_state()
        current_index = dType.GetQueuedCmdCurrentIndex(api)[0]

        if time.time() - last_print >= 1:
            print(
                "Current queue index: {} | Target: {}".format(
                    current_index, target_index
                ),
                flush=True,
            )
            last_print = time.time()

        if current_index >= target_index:
            return
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        "Timed out waiting for queued command index {}.".format(target_index)
    )


try:
    result = dType.ConnectDobot(api, "", 115200)
    if result[0] != dType.DobotConnect.DobotConnect_NoError:
        raise RuntimeError("Dobot connection failed: " + str(result))

    connected = True
    print("Dobot connected:", result, flush=True)
    print("Pi API:", PI_URL, flush=True)
    print("Initial Pi state:", read_state(), flush=True)

    if not MOTION_ENABLED:
        print("Motion disabled; no movement commands sent.", flush=True)
    else:
        route = POINTS[:1] if TEST_ONE_POINT else POINTS
        print(
            "Starting continuous loop with {} point(s). Press Ctrl+C to stop.".format(
                len(route)
            ),
            flush=True,
        )

        queue_started = False
        while True:
            for number, (x, y, z, r) in enumerate(route, start=1):
                if stop_requested:
                    raise RuntimeError("Stop requested.")

                state = read_state()
                print(
                    "\nPoint {}/{} | Zone: {} | Risk: {} | State: {}".format(
                        number,
                        len(route),
                        state.get("current_zone", "UNKNOWN"),
                        state.get("risk_level", "UNKNOWN"),
                        state.get("status", "UNKNOWN"),
                    ),
                    flush=True,
                )

                queued = dType.SetPTPCmd(
                    api, PTP_MODE, x, y, z, r, 1
                )
                target_index = int(queued[0])
                print(
                    "Queued point {}: X={} Y={} Z={} R={} | index={}".format(
                        number, x, y, z, r, target_index
                    ),
                    flush=True,
                )

                if not queue_started:
                    dType.SetQueuedCmdStartExec(api)
                    queue_started = True
                    print("Command queue started.", flush=True)

                wait_for_point(target_index)
                print("Point {} completed.".format(number), flush=True)

            print("\nRoute completed. Restarting in 1 second...", flush=True)
            time.sleep(1)

except KeyboardInterrupt:
    print("\nController stopped by user.", flush=True)
    if connected:
        stop_robot()
except Exception as exc:
    print("\nController halted:", exc, flush=True)
    if connected:
        stop_robot()
finally:
    if connected:
        dType.DisconnectDobot(api)
    print("\nDobot disconnected.", flush=True)
