use super::*;

#[test]
fn wsl_path_accepts_local_drive_paths_and_rejects_mount_roots_and_unc() {
    assert_eq!(wsl_path(r"\\?\E:\workspace with spaces\notes").unwrap(), "/mnt/e/workspace with spaces/notes");
    for path in [r"E:\", r"\\example\share\notes", r"E:\workspace\..\private", "E:relative"] {
        assert!(wsl_path(path).is_err(), "{path}");
    }
    assert_eq!(distro_name(None).unwrap(), "astrion-sandbox");
    for name in ["-other-distro", "ws l", "fixture;command"] {
        assert!(distro_name(Some(name)).is_err(), "{name}");
    }
}

#[test]
fn wsl_argv_enforces_bound_mounts_without_reparsing_shell_content() {
    let workspace = "/mnt/e/workspace with spaces";
    let cwd = "/mnt/e/workspace with spaces/child";
    let read = "/mnt/e/reports".to_owned();
    let write = "/mnt/e/exports".to_owned();
    let script = "printf '%s' \"isolated fixture\"";
    let restricted = sandbox_args("fixture", workspace, cwd, SandboxAccess {
        write: false, network: Network::Restricted,
    }, script, std::slice::from_ref(&read), std::slice::from_ref(&write), None);
    assert_eq!(&restricted[..4], &["-d", "fixture", "-e", "/usr/bin/bwrap"]);
    assert!(restricted.windows(3).any(|args| args == ["--ro-bind", workspace, workspace]));
    assert!(restricted.windows(3).any(|args| args == ["--ro-bind", &read, &read]));
    assert!(!restricted.iter().any(|arg| arg == "--share-net" || arg == &write || arg == "/mnt/e"));
    assert_eq!(restricted.last().unwrap(), script);
    let full = sandbox_args("fixture", workspace, cwd, SandboxAccess {
        write: true, network: Network::Full,
    }, script, &[], std::slice::from_ref(&write), None);
    assert!(full.iter().any(|arg| arg == "--share-net"));
    assert!(full.windows(3).any(|args| args == ["--bind", &write, &write]));
}

#[test]
fn narrow_wsl_child_can_write_only_its_fixed_directory() {
    let workspace = "/mnt/e/project";
    let fixed = "/mnt/e/project/.astrion/child";
    let cwd = "/mnt/e/project/worktrees/next";
    let external = "/mnt/e/authorized-exports".to_owned();
    let args = sandbox_args("fixture", workspace, cwd, SandboxAccess {
        write: true, network: Network::Restricted,
    }, "pwd", std::slice::from_ref(&external), std::slice::from_ref(&external), Some(fixed));
    assert!(args.windows(3).any(|args| args == ["--ro-bind", workspace, workspace]));
    assert!(args.windows(3).any(|args| args == ["--bind", fixed, fixed]));
    assert!(args.windows(3).any(|args| args == ["--ro-bind", &external, &external]));
    assert!(!args.windows(3).any(|args| args == ["--bind", &external, &external]));
    assert!(args.windows(2).any(|args| args == ["--chdir", cwd]));
}
