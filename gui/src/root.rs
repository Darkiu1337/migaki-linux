//! Repo-root discovery for the Rust frontend.
//!
//! The bridge and the runners live in the checkout (or the installed
//! ~/.local/share/migaki tree), so the binary locates it once at startup:
//! $MIGAKI_ROOT wins, otherwise walk up from the executable.
use std::path::{Path, PathBuf};

const MAX_LEVELS: usize = 6;

fn looks_like_root(dir: &Path) -> bool {
    dir.join("core/paths.py").is_file() && dir.join("scripts/migaki-lib.sh").is_file()
}

/// Absolute repo root, or an error naming what was tried.
pub fn find() -> anyhow::Result<PathBuf> {
    if let Ok(root) = std::env::var("MIGAKI_ROOT") {
        let path = PathBuf::from(root);
        if looks_like_root(&path) {
            return Ok(path);
        }
        anyhow::bail!(
            "MIGAKI_ROOT={} is not a migaki checkout (needs core/paths.py and scripts/migaki-lib.sh)",
            path.display()
        );
    }
    let exe = std::env::current_exe()?;
    let mut dir = exe.parent();
    let mut levels = 0;
    while let Some(candidate) = dir {
        if looks_like_root(candidate) {
            return Ok(candidate.to_path_buf());
        }
        if levels == MAX_LEVELS {
            break;
        }
        levels += 1;
        dir = candidate.parent();
    }
    anyhow::bail!(
        "could not find the migaki checkout from {} (set MIGAKI_ROOT)",
        exe.display()
    )
}
