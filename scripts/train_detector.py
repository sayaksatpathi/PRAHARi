import os
import shutil
import torch
from ultralytics import YOLO

def main():
    print("Loading YOLOv8n pretrained model...")
    model = YOLO("yolov8n.pt")
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Training on device: {device}")
    
    print("Starting training...")
    # Train for 15 epochs (enough for fine-tuning on a small dataset without taking too long)
    model.train(
        data="datasets/mot_det/data.yaml",
        epochs=15,
        imgsz=640,
        device=device,
        batch=16,
        project="var",
        name="mot_det_train"
    )
    
    print("Exporting model to ONNX...")
    exported_path = model.export(format="onnx", imgsz=640)
    
    target_path = "models/yolov8n_ft.onnx"
    print(f"Moving exported model from {exported_path} to {target_path}")
    shutil.copy(exported_path, target_path)
    
    print(f"Detector fine-tuning and export complete. Saved to {target_path}")

if __name__ == "__main__":
    main()
