
import time
import json
import urllib.request
import DobotDllType as dType

PI_URL = "http://172.20.10.2:5000/api/state"

# The saved route runs once, in order.
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

    # Fail closed: only move when the Pi reports NORMAL + LOW.
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
        print("\nStop requested; queue cleared.")
    except Exception as exc:
        print("\nSTOP COMMAND ERROR:", exc)


def wait_for_point(target_index):
    started = time.time()

    while time.time() - started < POINT_TIMEOUT:
        read_state()  # Stop waiting if the Pi is unsafe/unreachable.

        current = dType.GetQueuedCmdCurrentIndex(api)[0]
        if current >= target_index:
            return

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        "Timed out waiting for queued command index {}".format(target_index)
    )


try:
    result = dType.ConnectDobot(api, "", 115200)

    if result[0] != dType.DobotConnect.DobotConnect_NoError:
        raise RuntimeError("Dobot connection failed: " + str(result))

    connected = True
    print("Dobot connected:", result)
    print("Pi API:", PI_URL)

    # Verify the API and robot state before sending any movement.
    print("Initial Pi state:", read_state())

    if not MOTION_ENABLED:
        print("Motion disabled.")
    else:
        if TEST_ONE_POINT:
            route = POINTS[:1]
        else:
            route = POINTS

        print("Starting {} point(s).".format(len(route)))
        dType.SetQueuedCmdStartExec(api)

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
                    state.get("status", "UNKNOWN")
                )
            )

            # Queue only one point at a time.
            queued = dType.SetPTPCmd(
                api, PTP_MODE, x, y, z, r, 1
            )
            target_index = queued[0]

            if number == 1:
                dType.SetQueuedCmdStartExec(api)

            print(
                "Queued point {}: X={} Y={} Z={} R={}".format(
                    number, x, y, z, r
                )
            )
            wait_for_point(target_index)
            print("Point {} completed.".format(number))

        print("\nRoute completed.")

    # Continue monitoring after the route ends.
    while True:
        state = read_state()
        print(
            "\rZone: {} | Risk: {} | State: {}   ".format(
                state.get("current_zone", "UNKNOWN"),
                state.get("risk_level", "UNKNOWN"),
                state.get("status", "UNKNOWN")
            ),
            end="",
            flush=True
        )
        time.sleep(POLL_INTERVAL)

except KeyboardInterrupt:
    print("\nController stopped by user.")
    if connected:
        stop_robot()

except Exception as exc:
    print("\nController halted:", exc)
    if connected:
        stop_robot()

finally:
    if connected:
        try:
            stop_robot()
        finally:
            dType.DisconnectDobot(api)
    print("\nDobot disconnected.")
