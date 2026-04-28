import torch
import time
from debugflow import flow_engine

def process_layer(tensor: torch.Tensor):
    """Simple layer to verify shape serialization."""
    print(f"Processing tensor of shape: {tensor.shape}")
    # Simulate some work
    time.sleep(0.2)
    return tensor

def recursive_scout(depth: int):
    """Verifies that the HUD handles recursion without freezing."""
    if depth <= 0:
        return "Leaf Reached"
    time.sleep(0.1)
    return recursive_scout(depth - 1)

def trigger_mismatch():
    """The 'Nuke' node: This will turn RED on your HUD."""
    x = torch.randn(64, 784)
    weights = torch.randn(10, 10) # Intentional mismatch
    return torch.matmul(x, weights)

def trainer_main():
    """The master entry point for the HUD."""
    print("🚀 Starting HUD Verification Flow...")
    
    # 1. Test standard flow and shapes
    data = torch.randn(1, 784)
    process_layer(data)
    
    # 2. Test recursion (Watch the HUD nodes stack up)
    recursive_scout(3)
    
    # 3. Trigger the failure
    trigger_mismatch()

if __name__ == "__main__":
    # This fires the Ghost Pass (mapping) then the Live Pass (execution)
    flow_engine.launch("trainer_main", Ghost=True, Real_Time=False)