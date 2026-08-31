"""API route tests for service registry."""
import pytest
from fastapi.testclient import TestClient
from core.api import app

@pytest.fixture
def client():
    return TestClient(app)
