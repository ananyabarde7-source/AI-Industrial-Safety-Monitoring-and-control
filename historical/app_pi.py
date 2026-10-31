from flask import Flask, render_template, request, jsonify, Response
import json
import os
import cv2
import time
import threading
import project1

app = Flask(__name__)

CONFIG_FILE = "fence_config.json"
LOG_FILE = "logs/events.log"
JPEG_QUALITY = 68


def generate_frames():
    """Low-latency MJPEG stream using only the newest processed frame."""
    last_counter = -1

    while True:
        with project1.frame_condition:
            project1.frame_condition.wait_for(
                lambda: project1.frame_counter != last_counter,
                timeout=1.0
            )

            frame = project1.shared_frame
            counter = project1.frame_counter

            if frame is not None:
                frame = frame.copy()

        if frame is None:
            continue

        last_counter = counter

        ok, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )

        if not ok:
            continue

        data = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(data)).encode() +
            b"\r\n\r\n" + data + b"\r\n"
        )


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/video_feed")
def video_feed():
    response = Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"

    return response


@app.route("/api/zones", methods=["GET"])
def get_zones():
    try:
        with open(CONFIG_FILE, "r") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/zones", methods=["POST"])
def save_zones():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No zone data received"}), 400

        for name in ("safe", "warning", "danger"):
            if len(data.get(name, [])) < 3:
                return jsonify({
                    "error": f"{name} zone needs at least 3 points"
                }), 400

        # Atomic replace prevents the AI loop from reading a half-written file.
        temp_file = CONFIG_FILE + ".tmp"

        with open(temp_file, "w") as f:
            json.dump(data, f, indent=2)

        os.replace(temp_file, CONFIG_FILE)

        return jsonify({"status": "zones saved"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/state")
def get_state():
    with project1.state_lock:
        state = project1.system_state.copy()

    response = jsonify(state)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.route("/api/logs")
def get_logs():
    try:
        if not os.path.exists(LOG_FILE):
            return jsonify({"logs": []})

        with open(LOG_FILE, "r") as f:
            lines = f.readlines()[-30:]

        return jsonify({"logs": lines})

    except Exception:
        return jsonify({"logs": []})


def run_flask():
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True,
        use_reloader=False
    )


if __name__ == "__main__":
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )
    flask_thread.start()

    print("Dashboard started on port 5000")
    print("Starting AI safety system...")

    project1.main()
