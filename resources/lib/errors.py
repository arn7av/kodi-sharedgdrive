class PluginError(Exception):
    """An expected error safe to present to the user."""


class ConfigurationError(PluginError):
    pass


class AuthenticationError(PluginError):
    pass


class DriveError(PluginError):
    pass


class UnauthorizedError(DriveError):
    pass


class AccessBoundaryError(DriveError):
    pass
