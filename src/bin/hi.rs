//! `hi`: a short alias that runs the `hashcli` executable installed next to it.

use std::path::PathBuf;
use std::process::Command;

fn hashcli_path() -> PathBuf {
    let name = format!("hashcli{}", std::env::consts::EXE_SUFFIX);
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join(&name)))
        .filter(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from(name))
}

fn main() {
    let mut command = Command::new(hashcli_path());
    command.args(std::env::args_os().skip(1));

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let error = command.arg0("hi").exec();
        eprintln!("hi: failed to run hashcli: {error}");
        std::process::exit(127);
    }

    #[cfg(not(unix))]
    match command.status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!("hi: failed to run hashcli: {error}");
            std::process::exit(127);
        }
    }
}
