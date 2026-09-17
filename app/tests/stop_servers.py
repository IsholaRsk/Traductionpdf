import os
import signal
import subprocess
import sys

pattern = sys.argv[1] if len(sys.argv) > 1 else "serve"
me = os.getpid()
killed = []
out = subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True, text=True).stdout
for line in out.splitlines():
    parts = line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    pid, comm, args = parts
    if not comm.startswith("python"):
        continue
    argv = args.split()
    if len(argv) >= 2 and argv[0].startswith("python") and argv[1].endswith("server.py") and pattern in args:
        try:
            pid_i = int(pid)
            if pid_i != me:
                os.kill(pid_i, signal.SIGTERM)
                killed.append(pid_i)
        except Exception as exc:  # noqa: BLE001
            print("échec", pid, exc)
print("arrêté:", killed)
