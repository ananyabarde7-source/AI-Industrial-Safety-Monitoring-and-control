import time
import json
import urllib.request
import DobotDllType as dType

PI_URL = "http://172.20.10.2:5000/api/state"

# Runs all nine verified playback points in order.
MOTION_ENABLED = True
TEST_ONE_POINT = False

PTP_MODE = dType.PTPMode.PTPMOVJXYZMode
POLL_INTERVAL = 0.25
POINT_TIMEOUT = 60

POINTS = [
    (34.4780, -126.7197, -3.8069, -46.6541),
    (49.5954, -202.8594, 126.3125, -48.1364),
    (145.4505, -94.6796, 0.2390, -4.9364),
    (193.2646, -121.7743, 139.3514, -4.0894),
    (-81.3464, -214.6774, 127.5644, -82.6276),
    (-52.7669, -124.7741, -4.3377, -84.7982),
    (-82.1352, -194.2191, 156.2964, -84.7982),
    (158.9857, 103.6998, 141.1249, 61.2400),
    (125.7017, 53.4335, 20.6706, 51.1547),
]

api = dType.load()
connected = False


def read_state():
    req = urllib.request.Request(PI_URL)
    with urllib.request.urlopen(req, timeout=2) as response:
        data = json.loads(response.read().decode("utf-8"))

    state = str(data.get("status", "UNKNOWN")).upper()
    risk = str(data.get("risk_level", "UNKNOWN")).upper()
    if state != "NORMAL" or risk != "LOW":
        raise RuntimeError(
            "Pi stop condition: status={}, risk={}".format(state, risk)
        )
    return data


def stop_robot():
    try:
        dType.SetQueuedCmdForceStopExec(api)
        dType.SetQueuedCmdClear(api)
        print("\nSTOP: command queue halted and cleared.", flush=True)
    except Exception as exc:
        print("\nSTOP COMMAND ERROR:", exc, flush=True)


def wait_for_index(target_index, description):
    started = time.time()
    last_print = 0
    print("Waiting for {} (queue index {})".format(description, target_index), flush=True)

    while time.time() - started < POINT_TIMEOUT:
        read_state()
        current_index = dType.GetQueuedCmdCurrentIndex(api)[0]

        if time.time() - last_print >= 1:
            print("Queue index: {} / {}".format(current_index, target_index), flush=True)
            last_print = time.time()

        if current_index >= target_index:
            return
        time.sleep(POLL_INTERVAL)

    raise TimeoutError("Timed out waiting for {} at index {}".format(description, target_index))


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
        # Clear stale commands, then start the queue BEFORE adding commands.
        dType.SetQueuedCmdClear(api)
        dType.SetQueuedCmdStartExec(api)

        # Set PTP motion parameters in the same queue, before the coordinates.
        dType.SetPTPJointParams(
            api, 200, 200, 200, 200, 200, 200, 200, 200, isQueued=1
        )
        params_index = dType.SetPTPCommonParams(api, 30, 30, isQueued=1)[0]
        wait_for_index(params_index, "motion setup")

        route = POINTS[:1] if TEST_ONE_POINT else POINTS
        print("Starting {} point(s).".format(len(route)), flush=True)

        for number, (x, y, z, r) in enumerate(route, start=1):
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

            target_index = dType.SetPTPCmd(
                api, PTP_MODE, x, y, z, r, isQueued=1
            )[0]
            print(
                "Queued point {}: X={} Y={} Z={} R={} | index={}".format(
                    number, x, y, z, r, target_index
                ),
                flush=True,
            )
            wait_for_index(target_index, "point {}".format(number))
            print("Point {} completed.".format(number), flush=True)

        print("\nRoute completed. Monitoring Pi state; press Ctrl+C to exit.", flush=True)

    while True:
        state = read_state()
        print(
            "\rZone: {} | Risk: {} | State: {}   ".format(
                state.get("current_zone", "UNKNOWN"),
                state.get("risk_level", "UNKNOWN"),
                state.get("status", "UNKNOWN"),
            ),
            end="",
            flush=True,
        )
        time.sleep(POLL_INTERVAL)

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
