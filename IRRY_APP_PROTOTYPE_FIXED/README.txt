IRRY — CONNECTED MOBILE APP + PC PROTOTYPE
=============================================

This package creates one shared system:

PHONE APP
   |
   v
FLASK BACKEND ON PC
   |                |
   v                v
MOBILE CONTROL     PC PROTOTYPE PAGE

Both phone and prototype use the SAME backend state.

1. PC
-----
cd pc
pip install -r requirements.txt
python backend_server.py

Open the PC prototype:
http://127.0.0.1:5000/prototype/

2. PHONE
--------
Connect the phone and PC to the same Wi-Fi.
Find the PC IP with:
ipconfig

Open on phone:
http://PC-IP:5000/mobile/

Install the PWA / Add to Home screen, or build the Android APK from
mobile_android.

3. ANDROID APK
--------------
The folder mobile_android is an Android Studio project.
The APK WebView loads the same mobile control page served by the backend.
The server address can be edited inside the app.

The repository also contains a GitHub Actions workflow that can build
app-debug.apk automatically.

4. CONTROL FLOW
---------------
Phone START -> /api/start -> backend -> irrigation state
Phone STOP -> /api/stop -> backend -> irrigation state
Phone EMERGENCY STOP -> /api/estop -> backend -> irrigation state
PC prototype polls /api/status -> shows the same valve/pump/moisture state.

IMPORTANT
---------
A real hardware connection requires the PC/backend to remain running and be
reachable from the phone. Current config.py is safe simulation mode.
