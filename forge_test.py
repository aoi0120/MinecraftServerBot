import subprocess
import os

with open("test_output.log", "a") as log:
    proc = subprocess.Popen(
        ["/bin/bash", "/home/xeus/Desktop/minecraftForge1.20.1/run.sh"],
        stdout=log,
        stderr=log,
        preexec_fn=os.setsid,
        cwd="/home/xeus/Desktop/minecraftForge1.20.1"
    )
    print(f"Started PID: {proc.pid}")