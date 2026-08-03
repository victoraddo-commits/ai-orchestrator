"""
RTX 5090 GPU utilization monitoring and management
"""

import subprocess
import psutil
import time
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class RTX5090Monitor:
    """Monitor and manage RTX 5090 GPU utilization."""
    
    def __init__(self):
        self.gpu_utilization = 0.0
        self.memory_utilization = 0.0
        self.temperature = 0.0
        self.is_available = False
        
    def check_gpu_availability(self) -> bool:
        """Check if RTX 5090 GPU is available."""
        try:
            # Use nvidia-smi to check GPU availability
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Parse the output
                lines = result.stdout.strip().split('\n')
                if lines:
                    gpu_data = lines[0].split(', ')
                    if len(gpu_data) >= 4:
                        self.gpu_utilization = float(gpu_data[0])
                        self.memory_utilization = (float(gpu_data[1]) / float(gpu_data[2])) * 100
                        self.temperature = float(gpu_data[3])
                        self.is_available = True
                        return True
                        
            self.is_available = False
            return False
            
        except Exception as e:
            logger.warning(f"Failed to check GPU availability: {e}")
            self.is_available = False
            return False
            
    def get_gpu_stats(self) -> Dict[str, Any]:
        """Get current GPU statistics."""
        return {
            "utilization": self.gpu_utilization,
            "memory_utilization": self.memory_utilization,
            "temperature": self.temperature,
            "available": self.is_available
        }
        
    def monitor_gpu_usage(self) -> Dict[str, Any]:
        """Monitor GPU usage continuously."""
        try:
            # Basic GPU monitoring using nvidia-smi
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    gpu_data = lines[0].split(', ')
                    if len(gpu_data) >= 4:
                        return {
                            "gpu_utilization": float(gpu_data[0]),
                            "memory_used": float(gpu_data[1]),
                            "memory_total": float(gpu_data[2]),
                            "temperature": float(gpu_data[3]),
                            "timestamp": time.time()
                        }
                        
        except Exception as e:
            logger.error(f"GPU monitoring error: {e}")
            
        return {
            "gpu_utilization": 0.0,
            "memory_used": 0.0,
            "memory_total": 0.0,
            "temperature": 0.0,
            "timestamp": time.time(),
            "error": "Failed to monitor GPU"
        }

# Global instance
gpu_monitor = RTX5090Monitor()

def init_gpu_monitoring():
    """Initialize GPU monitoring."""
    logger.info("Initializing RTX 5090 GPU monitoring...")
    
def get_gpu_status():
    """Get current GPU status."""
    return gpu_monitor.get_gpu_stats()
    
def is_gpu_available():
    """Check if GPU is available for tasks."""
    return gpu_monitor.check_gpu_availability()