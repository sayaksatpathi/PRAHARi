"""Train a stronger MOT17 person detector (yolov8s) on GPU and export to ONNX.

Improves on the yolov8n baseline for the MOTA/IDF1 metric. Held-out sequences
MOT17-02/04 are excluded from training (see datasets/mot_det/data.yaml) so the
MOT eval stays honest.
"""
import shutil, warnings
warnings.filterwarnings("ignore")
from ultralytics import YOLO

def main():
    m = YOLO("yolov8s.pt")
    m.train(data="datasets/mot_det/data.yaml", epochs=60, imgsz=640, device=0,
            batch=16, workers=2, project="var", name="mot_yolov8s", exist_ok=True,
            patience=20, verbose=True, plots=False)
    p = m.export(format="onnx", imgsz=640, opset=12, simplify=True)
    shutil.copy(p, "models/yolov8s_mot.onnx")
    print("EXPORTED models/yolov8s_mot.onnx")

if __name__ == "__main__":
    main()
