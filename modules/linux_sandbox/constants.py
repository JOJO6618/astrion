"""Dependency-free contracts shared by the app and the installed root helper."""
PROTOCOL_VERSION = 1
DEFAULT_SOCKET = "/run/astrion-sandbox/control.sock"
INSTALL_ROOT = "/usr/local/libexec/astrion-sandbox"
PROFILE_PREFIX = "astrion-linux-sandbox-"
MAX_PACKET = 65536
MAX_PATHS = 128
MAX_ARGUMENTS = 256
RUNTIME_ROOT = "/.astrion-runtime"
SYSTEM_DIRECTORIES = ("/usr", "/bin", "/sbin", "/lib", "/lib64")
SYSTEM_FILES = (
    "/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/passwd",
    "/etc/group", "/etc/gai.conf", "/etc/localtime", "/etc/os-release",
    "/etc/ssl/certs", "/etc/ssl/openssl.cnf",
)
MINIMAL_READABLE_PATHS = (*SYSTEM_DIRECTORIES, *SYSTEM_FILES)
SAFE_ENVIRONMENT = frozenset({
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "VIRTUAL_ENV",
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
    "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_NO_CACHE_DIR", "NO_COLOR", "FORCE_COLOR",
})
