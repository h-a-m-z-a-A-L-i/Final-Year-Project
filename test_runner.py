import os
import signal
import subprocess

print("Finding host process...")
try:
    output = subprocess.check_output('wmic process where "CommandLine like \'%host.py%\'" get ProcessId, CommandLine', shell=True).decode('utf-8', errors='ignore')
    print("Processes:")
    print(output)
    for line in output.strip().split('\n')[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        pid_str = parts[-1]
        try:
            pid = int(pid_str)
            if pid != os.getpid():
                print(f"Stopping {pid}")
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            print(f"Error: {e}")
except Exception as ex:
    print(f"Failed: {ex}")
