import cv2
import numpy as np
from ultralytics import YOLO
from datetime import datetime
import RPi.GPIO as GPIO
import json
import logging
import os
import threading

# Logging setup
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/events.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

# Shared frame for dashboard
shared_frame = None
frame_lock = threading.Lock()

# Industrial threat classes
THREAT_CLASSES = {
    0: "Person",
    2: "Car",
    3: "Motorcycle",
    5: "Bus",
    7: "Truck",
    15: "Cat",
    16: "Dog",
    39: "Bottle",
    67: "Phone"
}

THREAT_CLASS_IDS = list(THREAT_CLASSES.keys())

def load_fence():
    try:
        with open("fence_config.json") as f:
            data = json.load(f)
        coords = data.get("fence", [])
        if len(coords) < 3:
            raise ValueError("Not enough points")
        if "x" in coords[0]:
            return [(p["x"], p["y"]) for p in coords]
        lats = [p["lat"] for p in coords]
        lngs = [p["lng"] for p in coords]
        min_lat, max_lat = min(lats), max(lats)
        min_lng, max_lng = min(lngs), max(lngs)
        pixels = []
        for p in coords:
            px = int((p["lng"] - min_lng) / (max_lng - min_lng + 0.0001) * 280 + 20)
            py = int((1 - (p["lat"] - min_lat) / (max_lat - min_lat + 0.0001)) * 200 + 20)
            pixels.append((px, py))
        return pixels
    except:
        return [(40, 30), (280, 30), (280, 210), (40, 210)]

# Load YOLO model
model = YOLO("yolov8n.pt")

# Buzzer setup
BUZZER_PIN = 18
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.output(BUZZER_PIN, GPIO.LOW)

# Open camera
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("Camera not opened")
    GPIO.cleanup()
    exit()

print("Security system started")
logging.info("Security system started")

DIGITAL_FENCE = load_fence()
FENCE_ARRAY = np.array(DIGITAL_FENCE, np.int32)

intrusion_logged = {}  # per class tracking
frame_count = 0
fence_reload_counter = 0

for _ in range(5):
    cap.read()

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame_count += 1
        fence_reload_counter += 1

        # Reload fence every 100 frames
        if fence_reload_counter >= 100:
            DIGITAL_FENCE = load_fence()
            FENCE_ARRAY = np.array(DIGITAL_FENCE, np.int32)
            fence_reload_counter = 0

        if frame_count % 2 != 0:
            continue

        # Draw fence in red
        for i in range(len(DIGITAL_FENCE)):
            cv2.line(frame, DIGITAL_FENCE[i],
                     DIGITAL_FENCE[(i + 1) % len(DIGITAL_FENCE)],
                     (0, 0, 255), 2)

        # YOLO detection — all threat classes, lower confidence
        results = model(
            frame,
            imgsz=256,
            classes=THREAT_CLASS_IDS,
            conf=0.35,        # lowered from 0.6 → more sensitive
            iou=0.4,          # lower iou → detect more overlapping objects
            verbose=False
        )

        intrusion_detected = False
        intruding_objects = []

        for r in results:
            for box in r.boxes:
                conf = float(box.conf[0])
                class_id = int(box.cls[0])
                label = THREAT_CLASSES.get(class_id, "Object")

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                inside = cv2.pointPolygonTest(FENCE_ARRAY, (cx, cy), False)

                if inside >= 0:
                    intrusion_detected = True
                    intruding_objects.append(label)
                    # Red box — inside fence
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    cv2.putText(frame, f"!! {label} {conf:.2f}", (x1, y1 - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
                else:
                    # Yellow box — outside fence but detected
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 1)
                    cv2.putText(frame, f"{label} {conf:.2f}", (x1, y1 - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

                cv2.circle(frame, (cx, cy), 4, (255, 0, 0), -1)

        # Intrusion handling
        if intrusion_detected:
            GPIO.output(BUZZER_PIN, GPIO.HIGH)
            current_time = datetime.now().strftime("%H:%M:%S")
            objects_str = ", ".join(set(intruding_objects))

            cv2.putText(frame, f"!! INTRUSION: {objects_str}", (5, 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            for obj in set(intruding_objects):
                if not intrusion_logged.get(obj, False):
                    msg = f"INTRUSION DETECTED - {obj} at {current_time}"
                    print(msg)
                    logging.warning(msg)
                    with open("intrusion_log.txt", "a") as file:
                        file.write(msg + "\n")
                    intrusion_logged[obj] = True
        else:
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            intrusion_logged = {}

        # Share processed frame with Flask
        with frame_lock:
            shared_frame = frame.copy()

        cv2.imshow("Raspberry Pi Security Feed", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

except KeyboardInterrupt:
    print("Program stopped by user")
    logging.info("Security system stopped")

finally:
    cap.release()
    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.cleanup()
    cv2.destroyAllWindows()