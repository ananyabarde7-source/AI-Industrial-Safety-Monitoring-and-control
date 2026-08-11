
# Virtual Fencing

## About the Project

This project is a virtual fencing system developed using Python, OpenCV, and YOLOv8 for real-time intrusion detection. The idea is to create a digital boundary inside a camera feed and detect whenever an object enters that restricted area.

The system identifies objects such as people, vehicles, animals, bottles, and mobile phones. If the center of a detected object crosses the virtual fence, an intrusion alert is generated and the event is recorded with a timestamp.

To make the system more useful, we also developed a dashboard that displays intrusion logs and monitoring information, providing a simple interface to track security events.

## Features

* Real-time object detection using YOLOv8
* Virtual fence created using a polygon boundary
* Intrusion alerts when an object enters the restricted area
* Event logging with timestamps
* Dashboard for monitoring intrusion records
* Lightweight and optimized for real-time execution

## Technologies Used

* Python
* OpenCV
* Ultralytics YOLOv8
* NumPy

## How it Works

The webcam captures live video frames, which are processed by the YOLOv8 model for object detection. The center point of each detected object is checked against the predefined virtual fence. If the object lies inside the protected area, the system displays an alert, logs the event, and updates the dashboard.

## Applications

This project can be used for smart surveillance, industrial safety, restricted area monitoring, warehouse security, and other AI-based security applications.

## Future Scope

Possible improvements include email notifications, multi-camera support, cloud-based logging, face recognition, and a web-based dashboard for remote monitoring.


## Author 
  Ananya Barde
  Symbiosis institue of technology, Pune
  
