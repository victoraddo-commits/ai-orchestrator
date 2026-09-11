#!/bin/bash
# create-vm.sh
# Creates the CPU Model Fabric VM on Proxmox B
# SSH path: jump host (192.168.99.2) → Proxmox B via Tailscale (100.122.38.118)
#
# EXECUTED 2026-09-11 — VM 112 created successfully.

set -euo pipefail

echo "=== Creating VM 112 for CPU Model Fabric ==="
echo "Date: $(date)"
echo "Target: Proxmox B (100.122.38.118) via jump host (192.168.99.2)"
echo "VMID: 112 | Hostname: kai-cpu"

# Configuration
PROXMOX_B="100.122.38.118"
JUMP_HOST="192.168.99.2"
VMID=112
VM_NAME="kai-cpu"
VM_IP="192.168.1.242"
STORAGE="evo"
NETWORK="vmbr0"
CLOUD_IMG="/var/lib/vz/template/iso/ubuntu-22.04-server-cloudimg-amd64.img"
CLOUDINIT_SNIPPET="cloud-init-kai-cpu.yaml"

proxmox_cmd() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        -J root@"$JUMP_HOST" root@"$PROXMOX_B" "$@"
}

# Check connectivity
proxmox_cmd "echo 'Connected to Proxmox B'"

# Upload cloud-init
scp -o StrictHostKeyChecking=no \
    -J root@"$JUMP_HOST" \
    "$(dirname "$0")/../configs/cloud-init.yaml" \
    root@"$PROXMOX_B":/var/lib/vz/snippets/"$CLOUDINIT_SNIPPET"

# Create VM
proxmox_cmd bash -s << REMOTE
set -e
qm create $VMID \
    --name $VM_NAME \
    --memory 32768 \
    --cores 16 \
    --cpu host \
    --machine q35 \
    --ostype l26 \
    --scsihw virtio-scsi-pci \
    --net0 virtio,bridge=$NETWORK \
    --serial0 socket \
    --vga serial0 \
    --agent enabled=1 \
    --onboot 1 \
    --startup order=2,up=60

qm importdisk $VMID "$CLOUD_IMG" $STORAGE --format qcow2
qm set $VMID --scsi0 $STORAGE:$VMID/vm-$VMID-disk-0.qcow2,discard=on,size=100G
qm set $VMID \
    --boot order=scsi0 \
    --ide2 $STORAGE:cloudinit \
    --cicustom user=local:snippets/$CLOUDINIT_SNIPPET \
    --ciuser kai \
    --ipconfig0 ip=$VM_IP/24,gw=192.168.1.1 \
    --nameserver 8.8.8.8 \
    --searchdomain local
qm resize $VMID scsi0 100G
qm start $VMID
REMOTE

echo "✓ VM $VMID ($VM_NAME) created and started at $VM_IP"
echo "SSH: ssh -J root@$JUMP_HOST,root@$PROXMOX_B kai@$VM_IP"
