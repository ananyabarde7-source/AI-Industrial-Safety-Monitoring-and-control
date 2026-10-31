import cv2
import numpy as np
from ultralytics import YOLO
import RPi.GPIO as GPIO
import json
import logging
import os
import threading
import time

# ============================================================
# SIMPLE RASPBERRY PI SAFETY MONITOR
# ============================================================

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/events.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

shared_frame = None
frame_lock = threading.Lock()
frame_condition = threading.Condition(frame_lock)
frame_counter = 0

system_state = {
    "status": "NORMAL",
    "risk_level": "LOW",
    "current_zone": "SAFE",
    "predicted_zone": "SAFE",
    "velocity": 0.0,
    "direction": "NONE",
    "body_part_alert": "NONE",
    "person_detected": False
}
state_lock = threading.Lock()

tracker_state = {
    "prev_pos": None,
    "prev_time": None,
    "velocity": (0.0, 0.0),
    "last_logged_state": None,
    "last_log_time": 0.0
}

PREDICTION_TIME = 0.5
GPIO_PIN = 18
LOG_THROTTLE = 1.0
CONFIDENCE_THRESHOLD = 0.5

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
FRAME_SKIP = 2
INFERENCE_SIZE = 224

SHOW_WINDOW = bool(os.environ.get("DISPLAY"))


def load_zones():
    try:
        with open("fence_config.json", "r") as f:
            data = json.load(f)

        zones = {}
        for name in ("safe", "warning", "danger"):
            points = data.get(name, [])
            if len(points) < 3:
                raise ValueError(f"Not enough points for {name} zone")

            zones[name] = np.array(
                [(int(p["x"]), int(p["y"])) for p in points],
                dtype=np.int32
            )

        return zones

    except Exception as e:
        print(f"Error loading zones: {e}")
        print("Using default zones.")

        return {
            "safe": np.array(
                [(20, 15), (300, 15), (300, 225), (20, 225)],
                dtype=np.int32
            ),
            "warning": np.array(
                [(60, 50), (260, 50), (260, 190), (60, 190)],
                dtype=np.int32
            ),
            "danger": np.array(
                [(100, 80), (220, 80), (220, 160), (100, 160)],
                dtype=np.int32
            )
        }


def get_point_zone(point, zones):
    x, y = point

    for name in ("danger", "warning", "safe"):
        if cv2.pointPolygonTest(
            zones[name], (float(x), float(y)), False
        ) >= 0:
            return name

    return "outside"


def calculate_velocity(current_pos, current_time):
    previous_pos = tracker_state["prev_pos"]
    previous_time = tracker_state["prev_time"]

    if previous_pos is None or previous_time is None:
        tracker_state["prev_pos"] = current_pos
        tracker_state["prev_time"] = current_time
        return (0.0, 0.0), 0.0

    dt = current_time - previous_time

    if dt < 0.001:
        return tracker_state["velocity"], 0.0

    dx = current_pos[0] - previous_pos[0]
    dy = current_pos[1] - previous_pos[1]

    vx = dx / dt
    vy = dy / dt
    speed = float(np.hypot(vx, vy))

    tracker_state["prev_pos"] = current_pos
    tracker_state["prev_time"] = current_time
    tracker_state["velocity"] = (vx, vy)

    return (vx, vy), speed


def predict_position(current_pos, velocity):
    vx, vy = velocity
    return (
        int(current_pos[0] + vx * PREDICTION_TIME),
        int(current_pos[1] + vy * PREDICTION_TIME)
    )


def direction_to_danger(current_pos, velocity, zones):
    danger_center = zones["danger"].mean(axis=0)
    dx = danger_center[0] - current_pos[0]
    dy = danger_center[1] - current_pos[1]

    distance = np.hypot(dx, dy)
    speed = np.hypot(velocity[0], velocity[1])

    if distance < 1 or speed < 1:
        return 0.0

    dot = velocity[0] * dx + velocity[1] * dy
    return float(dot / (speed * distance))


def assess_risk(current_pos, current_zone, predicted_zone,
                wrist_zones, velocity, zones):

    for wrist_name, zone in wrist_zones.items():
        if zone == "danger":
            return (
                "HIGH",
                "STOP",
                f"{wrist_name.upper()} INTRUSION - DANGER ZONE"
            )

    if current_zone == "danger":
        return "HIGH", "STOP", "PERSON IN DANGER ZONE"

    if predicted_zone == "danger":
        return "HIGH", "STOP", "PREDICTED DANGER ZONE ENTRY"

    if current_zone == "warning":
        if direction_to_danger(current_pos, velocity, zones) > 0.25:
            return "MEDIUM", "SLOW", "MOVING TOWARD DANGER"
        return "MEDIUM", "SLOW", "WARNING ZONE"

    if current_zone == "safe":
        return "LOW", "NORMAL", "SAFE ZONE"

    return "LOW", "NORMAL", "OUTSIDE MONITORED AREA"


def log_event(message):
    now = time.time()

    if (
        message != tracker_state["last_logged_state"]
        or now - tracker_state["last_log_time"] >= LOG_THROTTLE
    ):
        print(f"[LOG] {message}")
        logging.info(message)
        tracker_state["last_logged_state"] = message
        tracker_state["last_log_time"] = now


def draw_zones(frame, zones):
    colors = {
        "safe": (0, 200, 0),
        "warning": (0, 210, 220),
        "danger": (0, 0, 230)
    }

    for name, points in zones.items():
        color = colors[name]
        cv2.polylines(frame, [points], True, color, 1)

        # Small, unobtrusive zone label.
        center = points.mean(axis=0).astype(int)
        cv2.putText(
            frame,
            name.upper(),
            tuple(center),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.30,
            color,
            1,
            cv2.LINE_AA
        )


def draw_keypoints(frame, keypoints_xy, keypoints_conf):
    # YOLO COCO pose indices: nose=0, left wrist=9, right wrist=10
    keypoint_indices = {
        "head": 0,
        "left_wrist": 9,
        "right_wrist": 10
    }

    if keypoints_xy is None or len(keypoints_xy) < 11:
        return

    for name, idx in keypoint_indices.items():
        x, y = keypoints_xy[idx]

        if keypoints_conf is not None and idx < len(keypoints_conf):
            conf = float(keypoints_conf[idx])
        else:
            conf = 1.0

        if conf < CONFIDENCE_THRESHOLD or x <= 0 or y <= 0:
            continue

        if "wrist" in name:
            color = (255, 120, 0)
            radius = 3
        else:
            color = (0, 220, 220)
            radius = 3

        cv2.circle(
            frame,
            (int(x), int(y)),
            radius,
            color,
            -1
        )


def get_wrist_zones(keypoints_xy, keypoints_conf, zones):
    wrist_zones = {}

    if keypoints_xy is None or len(keypoints_xy) < 11:
        return wrist_zones

    for name, idx in (
        ("left_wrist", 9),
        ("right_wrist", 10)
    ):
        x, y = keypoints_xy[idx]

        if keypoints_conf is not None and idx < len(keypoints_conf):
            conf = float(keypoints_conf[idx])
        else:
            conf = 1.0

        if conf < CONFIDENCE_THRESHOLD or x <= 0 or y <= 0:
            continue

        wrist_zones[name] = get_point_zone(
            (int(x), int(y)),
            zones
        )

    return wrist_zones


def draw_hud(frame, safety_state, risk_level, current_zone,
             predicted_zone, speed, body_part_alert, fps):
    if safety_state == "STOP":
        status_color = (0, 0, 255)
    elif safety_state == "SLOW":
        status_color = (0, 220, 255)
    else:
        status_color = (0, 220, 0)

    # Compact dark HUD background.
    x, y = 5, 5
    width = 122
    height = 72 if body_part_alert == "NONE" else 84

    overlay = frame.copy()
    cv2.rectangle(
        overlay, (x, y), (x + width, y + height),
        (10, 10, 10), -1
    )
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    rows = [
        f"{safety_state}  |  {risk_level}",
        f"ZONE  {current_zone.upper()}",
        f"PRED  {predicted_zone.upper()}",
        f"VEL   {speed:.0f} px/s"
    ]

    for i, text in enumerate(rows):
        color = status_color if i == 0 else (220, 220, 220)
        cv2.putText(
            frame,
            text,
            (9, 17 + i * 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            color,
            1,
            cv2.LINE_AA
        )

    if body_part_alert != "NONE":
        cv2.putText(
            frame,
            body_part_alert,
            (9, 77),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.28,
            (0, 0, 255),
            1,
            cv2.LINE_AA
        )

    cv2.putText(
        frame,
        f"{fps:.1f} FPS",
        (FRAME_WIDTH - 48, 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
        (200, 200, 200),
        1,
        cv2.LINE_AA
    )


def main():
    global shared_frame, frame_counter

    print("Loading YOLO model...")

    # One lightweight pose model handles person + keypoints.
    pose_model = YOLO("yolov8n-pose.pt")

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.OUT)
    GPIO.output(GPIO_PIN, GPIO.LOW)

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # Ask USB cameras for MJPG when supported.
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except Exception:
        pass

    if not cap.isOpened():
        print("ERROR: Could not open camera /dev/video0")
        GPIO.cleanup()
        return

    print(f"Camera initialized: {FRAME_WIDTH}x{FRAME_HEIGHT} @ 30 FPS")
    print("Security system started")

    zones = load_zones()
    print(f"Zones loaded: {list(zones.keys())}")

    frame_count = 0
    fps = 0.0
    fps_start = time.time()
    processed_frames = 0
    last_zone_reload = time.time()

    try:
        while True:
            ret, raw_frame = cap.read()

            if not ret:
                time.sleep(0.01)
                continue

            frame_count += 1

            # Keep inference lightweight on Pi 4.
            if frame_count % FRAME_SKIP != 0:
                continue

            current_time = time.time()
            frame = raw_frame.copy()

            # Reload zone configuration occasionally so dashboard edits
            # take effect without restarting the program.
            if current_time - last_zone_reload >= 1.0:
                zones = load_zones()
                last_zone_reload = current_time

            person_detected = False
            person_center = None
            pose_keypoints = None
            pose_conf = None

            results = pose_model(
                frame,
                imgsz=INFERENCE_SIZE,
                conf=0.5,
                iou=0.45,
                device="cpu",
                verbose=False
            )

            result = results[0]

            if len(result.boxes) > 0 and len(result.keypoints) > 0:
                # Use the highest-confidence detected person.
                best_idx = int(result.boxes.conf.argmax().item())

                box = result.boxes[best_idx]
                x1, y1, x2, y2 = map(
                    int,
                    box.xyxy[0].cpu().numpy()
                )

                x1 = max(0, min(FRAME_WIDTH - 1, x1))
                y1 = max(0, min(FRAME_HEIGHT - 1, y1))
                x2 = max(0, min(FRAME_WIDTH - 1, x2))
                y2 = max(0, min(FRAME_HEIGHT - 1, y2))

                person_center = (
                    (x1 + x2) // 2,
                    (y1 + y2) // 2
                )
                person_detected = True

                pose_keypoints = (
                    result.keypoints.xy[best_idx]
                    .cpu()
                    .numpy()
                )

                if result.keypoints.conf is not None:
                    pose_conf = (
                        result.keypoints.conf[best_idx]
                        .cpu()
                        .numpy()
                    )

                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    (255, 255, 0),
                    1
                )

                draw_keypoints(frame, pose_keypoints, pose_conf)

            if person_detected:
                velocity, speed = calculate_velocity(
                    person_center, current_time
                )
                predicted_pos = predict_position(
                    person_center, velocity
                )
            else:
                velocity = (0.0, 0.0)
                speed = 0.0
                predicted_pos = None
                tracker_state["prev_pos"] = None
                tracker_state["prev_time"] = None
                tracker_state["velocity"] = (0.0, 0.0)

            current_zone = "outside"
            predicted_zone = "outside"
            wrist_zones = {}

            if person_center is not None:
                current_zone = get_point_zone(person_center, zones)

            if predicted_pos is not None:
                predicted_zone = get_point_zone(predicted_pos, zones)

            if pose_keypoints is not None:
                wrist_zones = get_wrist_zones(
                    pose_keypoints,
                    pose_conf,
                    zones
                )

            risk_level = "LOW"
            safety_state = "NORMAL"
            risk_reason = "NO PERSON DETECTED"
            body_part_alert = "NONE"

            if person_detected:
                for wrist_name, zone in wrist_zones.items():
                    if zone == "danger":
                        body_part_alert = f"{wrist_name.upper()}_DANGER"

                risk_level, safety_state, risk_reason = assess_risk(
                    person_center,
                    current_zone,
                    predicted_zone,
                    wrist_zones,
                    velocity,
                    zones
                )

            GPIO.output(
                GPIO_PIN,
                GPIO.HIGH if safety_state in ("SLOW", "STOP") else GPIO.LOW
            )

            if person_detected:
                log_event(
                    f"ZONE: {current_zone.upper()} | "
                    f"PRED: {predicted_zone.upper()} | "
                    f"RISK: {risk_level} | "
                    f"STATE: {safety_state} | "
                    f"REASON: {risk_reason}"
                )

                if body_part_alert != "NONE":
                    log_event(
                        f"BODY PART ALERT: {body_part_alert}"
                    )

            with state_lock:
                system_state.update({
                    "status": safety_state,
                    "risk_level": risk_level,
                    "current_zone": current_zone.upper(),
                    "predicted_zone": predicted_zone.upper(),
                    "velocity": round(float(speed), 2),
                    "direction": (
                        f"({velocity[0]:.1f}, {velocity[1]:.1f})"
                        if person_detected else "NONE"
                    ),
                    "body_part_alert": body_part_alert,
                    "person_detected": person_detected
                })

            draw_zones(frame, zones)

            if person_detected and speed > 1:
                vx, vy = velocity
                arrow_scale = min(0.12, 20.0 / max(speed, 1.0))

                end_x = int(person_center[0] + vx * arrow_scale)
                end_y = int(person_center[1] + vy * arrow_scale)

                cv2.arrowedLine(
                    frame,
                    person_center,
                    (end_x, end_y),
                    (255, 0, 255),
                    1
                )

            if predicted_pos is not None:
                cv2.circle(
                    frame,
                    predicted_pos,
                    3,
                    (200, 200, 0),
                    1
                )

            processed_frames += 1
            elapsed = time.time() - fps_start

            if elapsed >= 1.0:
                fps = processed_frames / elapsed
                processed_frames = 0
                fps_start = time.time()

            draw_hud(
                frame,
                safety_state,
                risk_level,
                current_zone,
                predicted_zone,
                speed,
                body_part_alert,
                fps
            )

            # Publish only the newest frame.
            with frame_condition:
                shared_frame = frame.copy()
                frame_counter += 1
                frame_condition.notify_all()

            if SHOW_WINDOW:
                cv2.imshow("Predictive Safety Monitor", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\nProgram stopped by user")
        logging.info("=== Security system stopped ===")

    finally:
        cap.release()
        GPIO.output(GPIO_PIN, GPIO.LOW)
        GPIO.cleanup()

        if SHOW_WINDOW:
            cv2.destroyAllWindows()

        print("Cleanup complete")


if __name__ == "__main__":
    main()
