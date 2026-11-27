#[cfg(target_os = "linux")]
fn main() -> std::process::ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match astrion_linux_sandbox::linux::cli(&args) {
        Ok(code) => std::process::ExitCode::from(code as u8),
        Err(error) => {
            eprintln!("Linux sandbox: {error}");
            if args.first().is_some_and(|argument| argument == "install") {
                std::process::ExitCode::FAILURE
            } else {
                std::process::ExitCode::from(125)
            }
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn main() -> std::process::ExitCode {
    eprintln!("The Astrion Linux sandbox requires Ubuntu 24.04.");
    std::process::ExitCode::FAILURE
}
