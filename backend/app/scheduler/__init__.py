from app.scheduler.connector_scheduler import run_connector_loop as start_connector_scheduler

# Back-compat alias — was named ``start_scheduler`` before. The new
# name is the honest one (it polls connector configs, not memory consolidation).
# Kept as an alias so any external import keeps working; remove in a later cycle.
start_scheduler = start_connector_scheduler

__all__ = ["start_connector_scheduler", "start_scheduler"]
