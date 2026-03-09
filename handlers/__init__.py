from .base import router as base_router
from .admin import router as admin_router
from .student import router as student_router
from .solutions import router as solutions_router

__all__ = [
    "base_router",
    "admin_router", 
    "student_router",
    "solutions_router"
]
