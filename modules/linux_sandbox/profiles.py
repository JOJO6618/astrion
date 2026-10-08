"""Installed enforcing labels also prevent sandbox-to-broker nesting."""
try:
    from .helper_config import profile_name
except ImportError:
    from helper_config import profile_name


def profile_text() -> str:
    profiles = []
    for network in ("full", "restricted", "none"):
        lines = [f"profile {profile_name(network)} flags=(attach_disconnected,mediate_deleted) {{",
                 "  / r,", "  /** mrwklix,", "  capability,", "  network,", "  unix,",
                 "  signal,", "  ptrace,", "  mount,", "  umount,", "  pivot_root,"]
        # No change_profile rule: exec inherits this enforcing label.
        if network != "full":
            lines += ['  deny unix (connect, send, receive) peer=(addr="@**"),',
                      '  deny unix (bind) addr="@**",']
        # Pathname Unix sockets use file mediation in AppArmor. For 'none',
        # the separate seccomp policy prevents creating Unix sockets while
        # retaining anonymous stream/seqpacket socketpairs for internal IPC.
        profiles.append("\n".join([*lines, "}"]))
    return "\n\n".join(profiles) + "\n"
