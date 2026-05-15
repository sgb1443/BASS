#!/bin/bash
exec ffmpeg -f v4l2 -input_format h264 -video_size 640x480 -framerate 5 -i /dev/video0 -c:v copy -f rtsp rtsp://localhost:8554/cam