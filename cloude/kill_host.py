import subprocess
import os
import signal

# Use wmic to get processes
try:
    output = subprocess.check_output('wmic process where "CommandLine like \'%host.py%\'" get ProcessId, CommandLine', shell=True).decode('utf-8', errors='ignore')
    print("WMIC Output:")
    print(output)
    lines = output.strip().split('\n')
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        pid_str = parts[-1]
        try:
            pid = int(pid_str)
            if pid != os.getpid():
                print(f"Killing PID {pid}")
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            print(f"Error killing PID {pid_str}: {e}")
except Exception as ex:
    print(f"Error running wmic: {ex}")
