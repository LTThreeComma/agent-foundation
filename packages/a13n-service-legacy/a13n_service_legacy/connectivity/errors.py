from a13n_service_legacy.application_errors import ApplicationError


class NativeError(ApplicationError):
    """Safe stable error from native Account and Ingress operations."""
