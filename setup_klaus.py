#!/usr/bin/env python3
"""
KLAUS Legal Knowledge Acquisition System - Setup Script

This script initializes the KLAUS legal knowledge system.
"""

import os
import sys
from pathlib import Path

def setup_klaus_system():
    """Initialize the KLAUS legal knowledge system"""
    
    print("Setting up KLAUS Legal Knowledge Acquisition System...")
    
    # Create necessary directories
    law_docs_dir = Path.home() / "ai-orchestrator" / "law_documents"
    law_docs_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created legal documents directory: {law_docs_dir}")
    
    # Check if PostgreSQL is available and initialize database
    try:
        import psycopg2
        print("PostgreSQL driver found - attempting database initialization...")
        
        # Import our database manager
        from core.klaus.db_manager import init_database, init_sample_data
        
        # Initialize database
        if init_database():
            print("Database initialized successfully")
            
            # Initialize sample data
            if init_sample_data():
                print("Sample data initialized successfully")
            else:
                print("Warning: Could not initialize sample data")
        else:
            print("Warning: Could not initialize database")
            
    except ImportError:
        print("Warning: PostgreSQL driver not available. Database setup skipped.")
        print("To enable database functionality, install psycopg2-binary:")
        print("  pip install psycopg2-binary")
    
    # Create requirements file if it doesn't exist
    requirements_file = Path("requirements-klaus.txt")
    if not requirements_file.exists():
        with open(requirements_file, "w") as f:
            f.write("# KLAUS System Requirements\n")
            f.write("psycopg2-binary>=2.9.0\n")
            f.write("pypdf>=4.0.0\n")
            f.write("numpy>=1.24.0\n")
            f.write("scikit-learn>=1.3.0\n")
            print(f"Created {requirements_file}")
    
    print("\nSetup complete!")
    print("To use the system:")
    print("1. Install dependencies: pip install -r requirements-klaus.txt")
    print("2. Configure database settings in environment variables")
    print("3. Run: python -m core.klaus.background_worker")
    
    return True

if __name__ == "__main__":
    setup_klaus_system()