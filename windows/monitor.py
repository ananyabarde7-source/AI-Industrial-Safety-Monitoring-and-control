
import time
import json
import urllib.request
import urllib.error
import DobotDllType as dType

PI_URL = "http://172.20.10.2:5000/api/state"

api = dType.load()
connected = False
was_stopped = False

try:
    result = dType.ConnectDobot(api, "", 115200)

    if result[0] != dType.DobotConnect.DobotConnect_NoError:
        raise RuntimeError(f"Dobot connection failed: {result}")

    connected = True
    print("Dobot connected:", result)
    print("Monitoring Raspberry Pi. Press Ctrl+C to exit.")

    while True:
        try:
            req = urllib.request.Request(PI_URL)
            with urllib.request.urlopen(req, timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))

            state = str(data.get("status", "UNKNOWN")).upper()
            risk = str(data.get("risk_level", "UNKNOWN")).upper()
            intrusion = state == "STOP" or risk == "HIGH"

            if intrusion and not was_stopped:
                dType.SetQueuedCmdForceStopExec(api)
                print("\nINTRUSION: Dobot stop command sent")
                was_stopped = True
            elif not intrusion:
                was_stopped = False

            print(
                f"\rZone: {data.get('current_zone', 'UNKNOWN')} | "
                f"Risk: {risk} | State: {state}   ",
                end="",
                flush=True
            )

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"\nPi connection lost: {e}")
            dType.SetQueuedCmdForceStopExec(api)
            was_stopped = True

        time.sleep(0.25)

except KeyboardInterrupt:
    print("\nStopping monitor.")

finally:
    if connected:
        dType.SetQueuedCmdForceStopExec(api)
        dType.DisconnectDobot(api)
    print("Dobot disconnected.")
