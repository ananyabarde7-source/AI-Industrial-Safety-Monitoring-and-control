import cv2
import numpy as np
from ultralytics import YOLO
from datetime import datetime
import threading
import json
import os
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

model = YOLO("yolov8s.pt")


cap = cv2.VideoCapture(1)

if not cap.isOpened():
    print("Camera 1 not available, trying camera 0...")
    cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("No camera found")
    exit()

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

print("Camera opened successfully")
print("Dual-zone security system started")


DEFAULT_CONFIG = {
    "red_zone": {
        "left": 180,
        "top": 120,
        "right": 450,
        "bottom": 350
    },
    "orange_zone": {
        "left": 80,
        "top": 60,
        "right": 550,
        "bottom": 420
    }
}


if not os.path.exists("zone_config.json"):
    with open("zone_config.json", "w") as file:
        json.dump(DEFAULT_CONFIG, file, indent=4)


if not os.path.exists("intrusion_log.txt"):
    with open("intrusion_log.txt", "w") as file:
        file.write("Security Logs\n")

@app.route('/save_zones', methods=['POST'])
def save_zones():
    data = request.json

    with open('zone_config.json', 'w') as file:
        json.dump(data, file, indent=4)

    return jsonify({"status": "success"})

@app.route('/get_logs', methods=['GET'])
def get_logs():
    try:
        with open('intrusion_log.txt', 'r') as file:
            logs = file.readlines()
        return jsonify(logs[::-1])
    except:
        return jsonify([])

@app.route('/latest_frame.jpg')
def latest_frame():
    return send_from_directory('.', 'latest_frame.jpg')


threading.Thread(
    target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False),
    daemon=True
).start()

last_red_log = ""
last_orange_log = ""

while True:
    ret, frame = cap.read()

    if not ret:
        print("Failed to read frame")
        continue

    
    try:
        with open('zone_config.json', 'r') as file:
            config = json.load(file)
    except:
        config = DEFAULT_CONFIG

    red = config['red_zone']
    orange = config['orange_zone']

    red_zone = [
        (red['left'], red['top']),
        (red['right'], red['top']),
        (red['right'], red['bottom']),
        (red['left'], red['bottom'])
    ]

    orange_zone = [
        (orange['left'], orange['top']),
        (orange['right'], orange['top']),
        (orange['right'], orange['bottom']),
        (orange['left'], orange['bottom'])
    ]

    red_array = np.array(red_zone, np.int32)
    orange_array = np.array(orange_zone, np.int32)

    
    cv2.rectangle(
        frame,
        (orange['left'], orange['top']),
        (orange['right'], orange['bottom']),
        (0, 165, 255),
        2
    )

    cv2.putText(
        frame,
        'WARNING ZONE',
        (orange['left'], orange['top'] - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 165, 255),
        2
    )

    
    cv2.rectangle(
        frame,
        (red['left'], red['top']),
        (red['right'], red['bottom']),
        (0, 0, 255),
        2
    )

    cv2.putText(
        frame,
        'DANGER ZONE',
        (red['left'], red['top'] - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 0, 255),
        2
    )

    
    results = model(
        frame,
        imgsz=640,
        classes=[0],   
        conf=0.35,
        verbose=False
    )

    red_intrusion = False
    orange_intrusion = False

    for r in results:
        for box in r.boxes:
            conf = float(box.conf[0])

            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            
            cv2.circle(frame, (cx, cy), 4, (255, 0, 0), -1)

            
            label = f"Person {conf:.2f}"
            cv2.putText(
                frame,
                label,
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2
            )

            
            inside_red = cv2.pointPolygonTest(red_array, (cx, cy), False)
            inside_orange = cv2.pointPolygonTest(orange_array, (cx, cy), False)

            if inside_red >= 0:
                red_intrusion = True

            elif inside_orange >= 0:
                orange_intrusion = True

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    
    if orange_intrusion:
        warning_text = "NOT A SAFE ZONE TO BE"

        cv2.putText(
            frame,
            warning_text,
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 165, 255),
            3
        )

        if last_orange_log != current_time[:16]:
            log = f"[{current_time}] Warning: Person entered orange zone\n"
            print(log.strip())

            with open("intrusion_log.txt", "a") as file:
                file.write(log)

            last_orange_log = current_time[:16]

    
    if red_intrusion:
        alert_text = "INTRUSION DETECTED - ALARM"

        cv2.putText(
            frame,
            alert_text,
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            3
        )

        print("\a")  

        if last_red_log != current_time[:16]:
            log = f"[{current_time}] ALERT: Person entered RED zone\n"
            print(log.strip())

            with open("intrusion_log.txt", "a") as file:
                file.write(log)

            last_red_log = current_time[:16]

    
    cv2.imwrite("latest_frame.jpg", frame)

    
    cv2.imshow("Mac Security System", frame)

    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
