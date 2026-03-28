import cv2
import numpy as np

face_cap = cv2.CascadeClassifier("/Users/arihantkaul/Desktop/haarcascade_frontalface_alt copy.xml")
video_cap = cv2.VideoCapture(0)

while True:
    ret, video_data = video_cap.read()
    col = cv2.cvtColor(video_data, cv2.COLOR_BGR2GRAY)
    faces = face_cap.detectMultiScale(
        col, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30), flags=cv2.CASCADE_SCALE_IMAGE)

    face_count = len(faces)

    for (x, y, w, h) in faces:
        cv2.rectangle(video_data, (x, y), (x+w, y+h), (0, 255, 0), 2)

    if face_count == 1:
        warning_text = "1 Face Detected"
        color = (0, 255, 0)        
    elif face_count == 2:
        warning_text = f"WARNING: {face_count} Faces Detected!"
        color = (0, 165, 255)      
    elif face_count >= 3:   
        warning_text = f"HIGH ALERT: {face_count} Faces Detected!"
        color = (0, 0, 255)        
    else:
        warning_text = "No Face Detected"
        color = (255, 255, 255)    


    cv2.putText(video_data, warning_text, (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

    cv2.imshow("Live Capture", video_data)
    if cv2.waitKey(10) == ord("a"):
        break

video_cap.release()
cv2.destroyAllWindows()