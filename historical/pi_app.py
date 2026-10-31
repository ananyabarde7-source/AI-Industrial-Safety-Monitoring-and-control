from flask import Flask, render_template, request, jsonify, Response
import json, os, cv2

app = Flask(__name__)
CONFIG_FILE = "fence_config.json"
LOG_FILE = "logs/events.log"

def generate_frames():
    import project1
    while True:
        with project1.frame_lock:
            frame = project1.shared_frame
        if frame is None:
            continue
        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/fence", methods=["GET"])
def get_fence():
    with open(CONFIG_FILE) as f:
        return jsonify(json.load(f))

@app.route("/api/fence", methods=["POST"])
def save_fence():
    data = request.get_json()
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return jsonify({"status": "saved"})

@app.route("/api/logs")
def get_logs():
    if not os.path.exists(LOG_FILE):
        return jsonify({"logs": []})
    with open(LOG_FILE) as f:
        lines = f.readlines()[-100:]
    return jsonify({"logs": lines})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)