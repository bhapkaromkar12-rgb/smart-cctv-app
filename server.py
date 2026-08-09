import cv2
import time
import datetime
import os
import random
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
import uvicorn
import requests

app = FastAPI()

# Allow Frontend to communicate with Backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RECORDINGS_DIR = "recordings"
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Global Controls
ai_motion_detection = True
manual_recording = False
back_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=True)

# ================= MONGODB CONNECTIVITY =================
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://bhapkaromkar12_db_user:BiOR5mxI09auCo7G@cluster0.l4phw0i.mongodb.net/?appName=Cluster0")

client = MongoClient(MONGO_URI)
db = client['smart_cctv']              # Database Name
users_collection = db['users']         # Collection (Table) for Users
otp_store = {}                        # Temporary OTP storage

# Base Path setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Pydantic Schemas
class SendOTPReq(BaseModel):
    mobile: str

class RegisterReq(BaseModel):
    username: str
    mobile: str
    otp: str
    password: str

class LoginReq(BaseModel):
    username: str
    password: str

class ResetPassReq(BaseModel):
    mobile: str
    otp: str
    new_password: str

# ================= VIDEO STREAMING LOGIC =================

def generate_frames():
    global ai_motion_detection, manual_recording
    cap = cv2.VideoCapture(0)  # Laptop Camera

    if not cap.isOpened():
        print("[ERROR] Camera disconnect ho gaya ya open nahi ho raha.")
        return

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    recording = False
    out = None
    last_motion_time = 0
    buffer_seconds = 3

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        current_time = time.time()
        motion_detected = False

        if ai_motion_detection:
            fg_mask = back_sub.apply(frame)
            _, thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                if cv2.contourArea(contour) > 1500:
                    motion_detected = True
                    (x, y, w, h) = cv2.boundingRect(contour)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        should_record = manual_recording or (ai_motion_detection and motion_detected)

        if should_record:
            last_motion_time = current_time
            if not recording:
                recording = True
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = os.path.join(RECORDINGS_DIR, f"clip_{timestamp}.mp4")
                h, w, _ = frame.shape
                out = cv2.VideoWriter(filename, fourcc, 20.0, (w, h))

        if recording:
            out.write(frame)
            cv2.circle(frame, (30, 30), 10, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (50, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            if not manual_recording and (current_time - last_motion_time > buffer_seconds):
                recording = False
                out.release()
                out = None

        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        
        time.sleep(0.03)

    cap.release()

# ================= PAGE ROUTES =================

@app.get("/", response_class=HTMLResponse)
def read_root():
    auth_file = os.path.join(BASE_DIR, "auth.html")
    if os.path.exists(auth_file):
        return FileResponse(auth_file)
    return HTMLResponse("<h2>auth.html file missing in root directory!</h2>", status_code=404)

@app.get("/dashboard", response_class=HTMLResponse)
def read_dashboard():
    index_file = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h2>index.html file missing in root directory!</h2>", status_code=404)

# ================= API ENDPOINTS =================

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/gallery")
def get_gallery():
    files = [f for f in os.listdir(RECORDINGS_DIR) if f.endswith(".mp4")]
    return {"videos": files}

@app.get("/video/{filename}")
def get_video(filename: str):
    file_path = os.path.join(RECORDINGS_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4")
    return {"error": "File not found"}

# --- AUTH API ROUTES ---

# Fast2SMS API Key (Render Environment Variable ya Direct String)
FAST2SMS_API_KEY = os.environ.get("FAST2SMS_API_KEY", "ndYBTOuoNDye8IHXPM9bh1mZp6V3GUfizLCJqStwsg47R5AvFj1ZDiVzb8BsUCgdfnkhJERm7eoGpya0")

@app.post("/api/send-otp")
def send_otp(data: SendOTPReq):
    # 4-Digit Random OTP
    otp = str(random.randint(1000, 9999))
    otp_store[data.mobile] = otp

    # Fast2SMS API Request
    url = "https://www.fast2sms.com/dev/bulkV2"
    payload = f"variables_values={otp}&route=otp&numbers={data.mobile}"
    headers = {
        'authorization': FAST2SMS_API_KEY,
        'Content-Type': "application/x-www-form-urlencoded",
        'Cache-Control': "no-cache"
    }

    try:
        response = requests.request("POST", url, data=payload, headers=headers)
        res_data = response.json()

        if res_data.get("return") == True:
            print(f"[SMS SENT] OTP {otp} sent to {data.mobile}")
            return {"message": f"OTP successfully sent to {data.mobile}"}
        else:
            print(f"[SMS ERROR] Fast2SMS Response: {res_data}")
            # Fallback to console print if API fails
            return {"message": f"OTP sent to {data.mobile}", "demo_otp": otp}

    except Exception as e:
        print(f"[SMS EXCEPTION] {e}")
        return {"message": f"OTP sent to {data.mobile}", "demo_otp": otp}

@app.post("/api/register")
def register(data: RegisterReq):
    if otp_store.get(data.mobile) != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP!")
    
    existing_user = users_collection.find_one({"$or": [{"username": data.username}, {"mobile": data.mobile}]})
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or Mobile number already registered!")
    
    user_document = {
        "username": data.username,
        "mobile": data.mobile,
        "password": data.password
    }
    users_collection.insert_one(user_document)
    otp_store.pop(data.mobile, None)
    return {"message": "User registered successfully!"}

@app.post("/api/login")
def login(data: LoginReq):
    user = users_collection.find_one({"username": data.username})
    if not user or user["password"] != data.password:
        raise HTTPException(status_code=401, detail="Invalid username or password!")
    return {"message": "Login successful"}

@app.post("/api/reset-password")
def reset_password(data: ResetPassReq):
    if otp_store.get(data.mobile) != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP!")
    
    user = users_collection.find_one({"mobile": data.mobile})
    if not user:
        raise HTTPException(status_code=404, detail="Mobile number not registered!")
    
    users_collection.update_one(
        {"mobile": data.mobile},
        {"$set": {"password": data.new_password}}
    )
    otp_store.pop(data.mobile, None)
    return {"message": "Password updated successfully!"}

# ================= SERVER START =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)