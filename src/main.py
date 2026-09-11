import subprocess

def disable_and_remove_service(service_name):
    subprocess.run(['systemctl', 'disable', service_name], check=True)
    subprocess.run(['systemctl', 'stop', service_name], check=True)
    subprocess.run(['rm', f'/etc/systemd/system/{service_name}'], check=True)

def enable_and_start_service(service_name):
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', service_name], check=True)
    subprocess.run(['systemctl', 'start', service_name], check=True)

if __name__ == "__main__":
    disable_and_remove_service('juris-kai.service')
    enable_and_start_service('ai-orchestrator-juris-kai.service')
