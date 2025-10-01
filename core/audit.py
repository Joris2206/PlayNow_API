# core/audit.py
import logging
audit_logger = logging.getLogger('audit')

def log_action(user, action, entity_type, entity_id, extra=None):
    audit_logger.info(
        f'{action} {entity_type}={entity_id} by user={getattr(user, "id", None)}',
    )