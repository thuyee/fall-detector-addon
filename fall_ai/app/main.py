#!/usr/bin/env python3
import argparse, logging, os, time, threading, json
from pathlib import Path
import yaml
import cv2
import numpy as np
import onnxruntime as ort

LOG=logging.getLogger("fall_ai")

def load_config(path):
    with open(path,"r",encoding="utf-8") as f:
        cfg=yaml.safe_load(f) or {}
    if not isinstance(cfg,dict):
        raise ValueError("Config root must be a YAML mapping")
    cams=cfg.get("cameras")
    if cams is None:
        cams=[]
    if not isinstance(cams,list):
        raise ValueError("cameras must be a list")
    cfg["cameras"]=cams
    cfg.setdefault("global",{})
    return cfg

class PoseModel:
    def __init__(self,size=416,threads=2):
        self.size=int(size)
        so=ort.SessionOptions()
        so.intra_op_num_threads=max(1,int(threads))
        so.inter_op_num_threads=1
        so.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        path="/config/models/yolo11n-pose.onnx"
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        LOG.info("Loading Opset-21-compatible YOLO11n-pose ONNX model")
        self.session=ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        self.input=self.session.get_inputs()[0].name
        LOG.info("ONNX model loaded: %s",path)
        LOG.info("ONNX providers: %s",self.session.get_providers())

    def infer(self, frame):
        img=cv2.resize(frame,(self.size,self.size))
        img=cv2.cvtColor(img,cv2.COLOR_BGR2RGB).astype(np.float32)/255.0
        img=np.transpose(img,(2,0,1))[None,...]
        return self.session.run(None,{self.input:img})

def motion_changed(prev, frame, threshold, pixels):
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    gray=cv2.resize(gray,(320,180))
    if prev is None:
        return False,gray
    diff=cv2.absdiff(prev,gray)
    _,mask=cv2.threshold(diff,float(threshold),255,cv2.THRESH_BINARY)
    count=int(np.count_nonzero(mask))
    return count>=int(pixels),gray

class CameraWorker(threading.Thread):
    def __init__(self,cam,global_cfg,model):
        super().__init__(daemon=True)
        self.cam=cam; self.g=global_cfg; self.model=model; self.stop_event=threading.Event()
        self.last_motion=None
        self.last_infer=0
        self.motion_state=False
    def run(self):
        cid=self.cam.get("id","camera")
        rtsp=self.cam.get("rtsp")
        if not rtsp:
            LOG.error("%s: missing rtsp",cid); return
        cap=cv2.VideoCapture(rtsp,cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
        if not cap.isOpened():
            LOG.error("%s: cannot open RTSP",cid); return
        LOG.info("%s: RTSP connected",cid)
        interval=1.0/max(0.1,float(self.g.get("inference_fps",3)))
        while not self.stop_event.is_set():
            ok,frame=cap.read()
            if not ok:
                LOG.warning("%s: RTSP frame read failed; reconnecting",cid)
                cap.release(); time.sleep(2)
                cap=cv2.VideoCapture(rtsp,cv2.CAP_FFMPEG)
                continue
            motion,self.last_motion=motion_changed(
                self.last_motion,frame,
                self.g.get("motion_threshold",8.0),
                self.g.get("motion_pixels",250))
            self.motion_state=motion
            now=time.monotonic()
            if motion and now-self.last_infer>=interval:
                self.last_infer=now
                try:
                    self.model.infer(frame)
                except Exception:
                    LOG.exception("%s: inference failed",cid)
        cap.release()
    def stop(self): self.stop_event.set()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="/config/config.yaml")
    args=ap.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg=load_config(args.config)
    g=cfg.get("global") or {}
    LOG.info("Fall AI v0.1.2 starting")
    LOG.info("Configured cameras: %d",len(cfg["cameras"]))
    LOG.info("CPU threads: %s, inference FPS: %s",g.get("cpu_threads",2),g.get("inference_fps",3))
    model=PoseModel(g.get("image_size",416),g.get("cpu_threads",2))
    workers=[]
    for cam in cfg["cameras"]:
        if not isinstance(cam,dict): continue
        if cam.get("enabled",True):
            w=CameraWorker(cam,g,model); workers.append(w); w.start()
            LOG.info("Camera %s enabled; motion_entity=%s",cam.get("id"),cam.get("motion_entity"))
    while True:
        time.sleep(60)

if __name__=="__main__":
    main()
