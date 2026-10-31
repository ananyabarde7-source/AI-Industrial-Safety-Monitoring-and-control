import cv2
import numpy as np
from ultralytics import YOLO
import RPi.GPIO as GPIO
import json
import logging
import os
import threading
import time

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/events.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

shared_frame = None
frame_lock = threading.Lock()

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
INFERENCE_SIZE = 256

# SSH does not normally have a display. On the Pi desktop, DISPLAY is set.
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
        if direction_to_danger(
            current_pos, velocity, zones
        ) > 0.25:
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
        "safe": (0, 255, 0),
        "warning": (0, 255, 255),
        "danger": (0, 0, 255)
    }

    for name, points in zones.items():
        color = colors[name]
        cv2.polylines(frame, [points], True, color, 1)

        center = points.mean(axis=0).astype(int)
        cv2.putText(
            frame,
            name.upper(),
            tuple(center),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1
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
            color = (255, 0, 0)
            radius = 5
        else:
            color = (0, 255, 255)
            radius = 4

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


def main():
    global shared_frame

    print("Loading YOLO model...")
    # One pose model is enough for person detection + keypoints.
    # This is lighter for the Raspberry Pi 4.
    pose_model = YOLO("yolov8n-pose.pt")

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.OUT)
    GPIO.output(GPIO_PIN, GPIO.LOW)

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    zones = load_zones()

    frame_count = 0
    fps = 0
    fps_start = time.time()
    processed_frames = 0

    try:
        print("Starting main processing loop...")

        while True:
            ret, raw_frame = cap.read()

            if not ret:
                continue

            frame_count += 1
            if frame_count % FRAME_SKIP != 0:
                continue

            processed_frames += 1
            current_time = time.time()

            # Prepare frame for inference
            frame = raw_frame.copy()
            inference_frame = cv2.resize(frame, (INFERENCE_SIZE, INFERENCE_SIZE))

            person_detected = False
            person_center = None
            pose_keypoints = None
            pose_conf = None

            results = pose_model(
                inference_frame,
                imgsz=INFERENCE_SIZE,
                conf=0.5,
                iou=0.45,
                device=0,
                verbose=False
            )

            result = results[0]

            if len(result.boxes) > 0 and len(result.keypoints) > 0:
                box = result.boxes[0]

                # Scale box coordinates back to original frame size
                scale_x = FRAME_WIDTH / INFERENCE_SIZE
                scale_y = FRAME_HEIGHT / INFERENCE_SIZE

                x1, y1, x2, y2 = map(
                    int,
                    box.xyxy[0].cpu().numpy()
                )

                x1 = int(x1 * scale_x)
                y1 = int(y1 * scale_y)
                x2 = int(x2 * scale_x)
                y2 = int(y2 * scale_y)

                person_center = (
                    (x1 + x2) // 2,
                    (y1 + y2) // 2
                )
                person_detected = True

                # IMPORTANT:
                # keypoints.xy contains only x,y.
                # Confidence is stored separately in keypoints.conf.
                pose_keypoints = (
                    result.keypoints.xy[0]
                    .cpu()
                    .numpy()
                )

                # Scale keypoints back to original frame
                pose_keypoints[:, 0] *= scale_x
                pose_keypoints[:, 1] *= scale_y

                if result.keypoints.conf is not None:
                    pose_conf = (
                        result.keypoints.conf[0]
                        .cpu()
                        .numpy()
                    )

                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    (255, 255, 0),
                    2
                )

                cv2.circle(
                    frame,
                    person_center,
                    5,
                    (0, 255, 0),
                    -1
                )

                draw_keypoints(
                    frame,
                    pose_keypoints,
                    pose_conf
                )

            velocity = (0.0, 0.0)
            speed = 0.0
            predicted_pos = None

            if person_detected:
                velocity, speed = calculate_velocity(
                    person_center,
                    current_time
                )
                predicted_pos = predict_position(
                    person_center,
                    velocity
                )
            else:
                tracker_state["prev_pos"] = None
                tracker_state["prev_time"] = None
                tracker_state["velocity"] = (0.0, 0.0)

            current_zone = "outside"
            predicted_zone = "outside"
            wrist_zones = {}

            if person_center is not None:
                current_zone = get_point_zone(
                    person_center,
                    zones
                )

            if predicted_pos is not None:
                predicted_zone = get_point_zone(
                    predicted_pos,
                    zones
                )

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

            if safety_state in ("SLOW", "STOP"):
                GPIO.output(GPIO_PIN, GPIO.HIGH)
            else:
                GPIO.output(GPIO_PIN, GPIO.LOW)

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

            # Draw zones
            draw_zones(frame, zones)

            # Draw velocity arrow
            if person_detected and speed > 1:
                vx, vy = velocity
                arrow_scale = min(0.15, 25.0 / max(speed, 1.0))

                end_x = int(person_center[0] + vx * arrow_scale)
                end_y = int(person_center[1] + vy * arrow_scale)

                cv2.arrowedLine(
                    frame,
                    person_center,
                    (end_x, end_y),
                    (255, 0, 255),
                    2
                )

            # Draw predicted position
            if predicted_pos is not None:
                cv2.circle(
                    frame,
                    predicted_pos,
                    4,
                    (200, 200, 0),
                    2
                )

            # Compact HUD in top-left corner
            status_color = (
                (0, 0, 255)
                if safety_state == "STOP"
                else (0, 255, 255)
                if safety_state == "SLOW"
                else (0, 255, 0)
            )

            hud_x = 5
            hud_y = 15
            line_height = 14

            cv2.putText(
                frame,
                f"STATE: {safety_state}",
                (hud_x, hud_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                status_color,
                1
            )

            cv2.putText(
                frame,
                f"RISK: {risk_level}",
                (hud_x, hud_y + line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                status_color,
                1
            )

            cv2.putText(
                frame,
                f"ZONE: {current_zone.upper()}",
                (hud_x, hud_y + line_height * 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (255, 255, 0),
                1
            )

            cv2.putText(
                frame,
                f"PRED: {predicted_zone.upper()}",
                (hud_x, hud_y + line_height * 3),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (200, 200, 0),
                1
            )

            cv2.putText(
                frame,
                f"VEL: {speed:.1f}",
                (hud_x, hud_y + line_height * 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (200, 150, 100),
                1
            )

            if body_part_alert != "NONE":
                cv2.putText(
                    frame,
                    f"ALERT: {body_part_alert}",
                    (hud_x, hud_y + line_height * 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (0, 0, 255),
                    1
                )

            # Update FPS once per second
            elapsed = time.time() - fps_start
            if elapsed >= 1.0:
                fps = processed_frames / elapsed
                processed_frames = 0
                fps_start = time.time()

            cv2.putText(
                frame,
                f"FPS: {fps:.1f}",
                (FRAME_WIDTH - 65, 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (200, 200, 200),
                1
            )

            with frame_lock:
                shared_frame = frame.copy()

            # Only open a window when a graphical display is available
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
