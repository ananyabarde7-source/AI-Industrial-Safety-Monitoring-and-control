from flask import Flask, render_template, request, jsonify, Response
import json
import os
import cv2
import threading
import project1

app = Flask(__name__)

CONFIG_FILE = "fence_config.json"
LOG_FILE = "logs/events.log"

# JPEG quality for streaming (70 = good balance of quality and speed)
JPEG_QUALITY = 70


# ============================================================
# VIDEO STREAM - OPTIMIZED FOR LOW LATENCY
# ============================================================

def generate_frames():
    """Stream processed frames from project1 to the dashboard.
    
    Optimized for low latency:
    - Only yields latest available frame
    - Uses reduced JPEG quality for faster compression
    - Prevents frame queue accumulation
    """
    last_frame_id = None
    
    while True:
        with project1.frame_lock:
            frame = project1.shared_frame
            current_frame_id = id(project1.shared_frame)

        if frame is None:
            continue

        # Skip duplicate frames to reduce output rate
        if current_frame_id == last_frame_id:
            continue

        last_frame_id = current_frame_id

        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )

        if not success:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(buffer.tobytes())).encode() + b"\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n"
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
    # No-cache headers to ensure fresh frames
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ============================================================
# ZONE CONFIGURATION
# ============================================================

@app.route("/api/zones", methods=["GET"])
def get_zones():
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)

        return jsonify(data)

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/api/zones", methods=["POST"])
def save_zones():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "error": "No zone data received"
            }), 400

        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "status": "zones saved"
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


# ============================================================
# SYSTEM STATE
# ============================================================

@app.route("/api/state")
def get_state():
    response = Response(
        json.dumps(get_state_dict()),
        mimetype="application/json"
    )
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


def get_state_dict():
    with project1.state_lock:
        state = project1.system_state.copy()
    return state


# ============================================================
# LOGS
# ============================================================

@app.route("/api/logs")
def get_logs():
    try:
        if not os.path.exists(LOG_FILE):
            return jsonify({
                "logs": []
            })

        with open(LOG_FILE, "r") as f:
            lines = f.readlines()[-50:]

        return jsonify({
            "logs": lines
        })

    except Exception:
        return jsonify({
            "logs": []
        })


# ============================================================
# START FLASK + AI SYSTEM
# ============================================================

def run_flask():
    """Run Flask in background so project1 can use the main thread."""
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )


if __name__ == "__main__":

    # Start Flask dashboard in background
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    print("Dashboard started on port 5000")
    print("Starting AI safety system...")

    # Run camera + YOLO in the main thread
    project1.main()
