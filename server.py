import cv2
import time
import datetime
import os
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import random
from pymongo import MongoClient

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
    buffer_seconds = 3  # Motion rukne ke 3 sec baad auto stop

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
                    # Green Box Draw Karo Motion Par
                    (x, y, w, h) = cv2.boundingRect(contour)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        should_record = manual_recording or (ai_motion_detection and motion_detected)

        # Start Recording
        if should_record:
            last_motion_time = current_time
            if not recording:
                recording = True
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = os.path.join(RECORDINGS_DIR, f"clip_{timestamp}.mp4")
                h, w, _ = frame.shape
                out = cv2.VideoWriter(filename, fourcc, 20.0, (w, h))
                print(f"[🔴 RECORDING STARTED] File: {filename}")

        # Stop Recording Condition
        if recording:
            out.write(frame)
            cv2.circle(frame, (30, 30), 10, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (50, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            if not manual_recording and (current_time - last_motion_time > buffer_seconds):
                recording = False
                out.release()
                out = None
                print(f"[⏹ RECORDING SAVED] Auto stopped after {buffer_seconds}s no motion.")

        # Stream JPEG Frame
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        
        time.sleep(0.03)

    cap.release()

# --- API ENDPOINTS ---

@app.get("/")
def home():
    # Frontend HTML file serve kar raha hai
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "Server running! Put index.html in backend folder."}

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

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)




MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://bhapkaromkar12_db_user:BiOR5mxI09auCo7G@cluster0.l4phw0i.mongodb.net/?appName=Cluster0")

client = MongoClient(MONGO_URI)
db = client['smart_cctv']              # Database Name
users_collection = db['users']         # Collection (Table) for Users
otp_store = {}                         # { mobile: "1234" }
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

# ================= ROUTES =================

@app.get("/", response_class=HTMLResponse)
def read_root():
    return FileResponse("auth.html")

@app.get("/dashboard", response_class=HTMLResponse)
def read_dashboard():
    return FileResponse("index.html")

# 1. Send OTP
@app.post("/api/send-otp")
def send_otp(data: SendOTPReq):
    otp = str(random.randint(1000, 9999))
    otp_store[data.mobile] = otp
    return {"message": f"OTP sent to {data.mobile}", "demo_otp": otp}

# 2. Register User (Saving to MongoDB)
@app.post("/api/register")
def register(data: RegisterReq):
    # Verify OTP
    if otp_store.get(data.mobile) != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP!")
    
    # Check if username or mobile already exists in DB
    existing_user = users_collection.find_one({"$or": [{"username": data.username}, {"mobile": data.mobile}]})
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or Mobile number already registered!")
    
    # Insert User into MongoDB
    user_document = {
        "username": data.username,
        "mobile": data.mobile,
        "password": data.password  # Production me pass ko hash karke save karte hain
    }
    users_collection.insert_one(user_document)
    
    # Clear OTP
    otp_store.pop(data.mobile, None)
    return {"message": "User registered successfully!"}

# 3. Login User (Fetch from MongoDB)
@app.post("/api/login")
def login(data: LoginReq):
    # Find user in DB
    user = users_collection.find_one({"username": data.username})
    
    if not user or user["password"] != data.password:
        raise HTTPException(status_code=401, detail="Invalid username or password!")
    
    return {"message": "Login successful"}

# 4. Reset Password (Update in MongoDB)
@app.post("/api/reset-password")
def reset_password(data: ResetPassReq):
    # Verify OTP
    if otp_store.get(data.mobile) != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP!")
    
    # Find user by mobile number
    user = users_collection.find_one({"mobile": data.mobile})
    if not user:
        raise HTTPException(status_code=404, detail="Mobile number not registered!")
    
    # Update Password in DB
    users_collection.update_one(
        {"mobile": data.mobile},
        {"$set": {"password": data.new_password}}
    )
    
    otp_store.pop(data.mobile, None)
    return {"message": "Password updated successfully!"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)